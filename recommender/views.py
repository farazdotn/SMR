import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import transaction
from django.db.models import F
from django.shortcuts import render
from django.views.decorators.vary import vary_on_headers

from .catalog import CatalogError, search_candidates, song_key, verify_recommendation
from .gemini_service import (
    GeminiRateLimitError,
    GeminiServiceError,
    get_recommendations,
)
from .models import SearchQuery, TrackRecommendation

logger = logging.getLogger(__name__)

SHOWN_COUNT = 5   # how many recommendations the user sees
MIN_TO_CACHE = 3  # results thinner than this are shown but never saved


def index(request):
    return render(request, "index.html")


def _wants_fragment(request):
    """True for a normal htmx request (swap just the results).

    When htmx restores a page after Back/Forward it also sends HX-Request,
    but it expects the full page back, so those requests are excluded.
    """
    return bool(request.headers.get("HX-Request")) and not request.headers.get(
        "HX-History-Restore-Request"
    )


# The same URL returns a fragment or a full page depending on these headers,
# so tell browsers and proxies not to reuse one for the other.
@vary_on_headers("HX-Request", "HX-History-Restore-Request")
def search(request):
    q = request.GET.get("q", "").strip()[:200]
    pick = request.GET.get("pick", "").strip()[:20]
    template = "_results.html" if _wants_fragment(request) else "index.html"
    context = {"q": q}

    if not q:
        context["error"] = "Type a song first dude."
        return render(request, template, context)

    try:
        candidates = search_candidates(q, limit=10)
        if not candidates:
            context["error"] = f"Couldn't find '{q}'. Check the spelling, or add the artist's name."
        else:
            # Compare as strings: never turns user input into an int, so
            # "²" or a 5000-digit number can't raise ValueError.
            chosen = next(
                (c for c in candidates if str(c.catalog_id) == pick), candidates[0]
            )
            context["chosen"] = chosen
            context["alternatives"] = _alternatives(candidates, chosen)

            recommendations, commentary = _get_results(chosen)
            if recommendations:
                context["recommendations"] = recommendations
                context["commentary"] = commentary
            else:
                context["error"] = "I couldn't verify any recommendations for that one. Try another song."
    except GeminiRateLimitError:
        context["error"] = "Lmao you got rate-limited, just hold on a little bit and try again."
    except (GeminiServiceError, CatalogError):
        logger.exception("External lookup failed")
        context["error"] = "Connection error. You might need to either turn on or off your VPN."

    return render(request, template, context)


def _alternatives(candidates, chosen, max_items=5):
    """Other songs the user might have meant, minus re-releases of the chosen one."""
    seen = {song_key(chosen.title, chosen.artist)}
    alts = []
    for c in candidates:
        key = song_key(c.title, c.artist)
        if key in seen:
            continue
        seen.add(key)
        alts.append(c)
    return alts[:max_items]


def _safe_verify(rec):
    """Look up one recommendation. Returns (match, ok).

    ok is False when the lookup itself failed (network error, rate limit),
    as opposed to the lookup working but finding no matching song.
    """
    try:
        return verify_recommendation(rec.title, rec.artist), True
    except CatalogError:
        logger.warning("iTunes lookup lowk failed while verifying %r by %r", rec.title, rec.artist)
        return None, False


def _verify_all(recs):
    """Look up a batch of recommendations in iTunes at once, keeping their order."""
    with ThreadPoolExecutor(max_workers=SHOWN_COUNT) as pool:
        return list(pool.map(_safe_verify, recs))


def _verify_until_enough(track, recs):
    """Verify Gemini's picks, closest first, until SHOWN_COUNT have passed.

    Picks are checked in batches of SHOWN_COUNT, so the backup picks only cost
    extra iTunes calls when some of the first batch don't verify.
    Returns (verified, failed): a list of (recommendation, catalog match) and
    the number of lookups that errored out.
    """
    # song_key ignores "- Remastered" and "(feat. X)" tags, so a re-release of
    # the searched song is caught here too.
    seen = {song_key(track.title, track.artist)}  # never recommend the searched song
    verified = []
    failed = 0

    for start in range(0, len(recs), SHOWN_COUNT):
        if len(verified) >= SHOWN_COUNT:
            break
        batch = recs[start:start + SHOWN_COUNT]
        for rec, (match, ok) in zip(batch, _verify_all(batch)):
            if not ok:
                failed += 1
                continue
            if match is None:
                continue
            key = song_key(match.title, match.artist)
            if key in seen:
                continue
            seen.add(key)
            verified.append((rec, match))

    return verified[:SHOWN_COUNT], failed


def _get_results(track):
    """Cache-first. Returns a list of TrackRecommendation, empty if nothing verified.

    The cache is keyed by song (title + artist), not by iTunes track id, so the
    single, the album cut and the remaster of one song share one entry and one
    Gemini call.

    Results are only saved when every lookup worked and enough songs verified.
    Otherwise they are shown once and the next search for this song tries again,
    instead of a thin or error-damaged list being served forever.
    """
    key = song_key(track.title, track.artist)
    cached = SearchQuery.objects.filter(song_key=key).first()
    if cached and cached.commentary:
        SearchQuery.objects.filter(pk=cached.pk).update(request_count=F("request_count") + 1)
        return list(cached.recommendations.all()), cached.commentary

    recs, commentary = get_recommendations(track.title, track.artist)
    verified, failed = _verify_until_enough(track, recs)

    if not verified:
        if failed:
            # Nothing verified and some lookups errored: iTunes is the problem,
            # not Gemini's picks. Say so instead of "couldn't verify anything".
            raise CatalogError(f"{failed} iTunes lookups failed and none succeeded. Brutal.")
        return [], ""

    rows = [
        TrackRecommendation(
            catalog_id=match.catalog_id,
            title=match.title,
            artist=match.artist,
            rationale=rec.rationale,
            artwork_url=match.artwork_url,
            link=match.link,
            preview_url=match.preview_url,
        )
        for rec, match in verified
    ]

    if failed or len(rows) < MIN_TO_CACHE:
        return rows, commentary  # show them, but don't cache

    with transaction.atomic():
        if cached:
            cached.delete()
        search_obj, created = SearchQuery.objects.get_or_create(
            song_key=key,
            defaults={
                "catalog_id": track.catalog_id,
                "title": track.title,
                "artist": track.artist,
                "artwork_url": track.artwork_url,
                "link": track.link,
                "commentary": commentary,
            },
        )
        if created:
            for row in rows:
                row.search_query = search_obj
            TrackRecommendation.objects.bulk_create(rows)
    return list(search_obj.recommendations.all()), search_obj.commentary