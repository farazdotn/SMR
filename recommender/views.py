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
    """Return True for a normal htmx request (swap just the results).

    When htmx restores a page after Back/Forward it also sends HX-Request,
    but it expects the full page back, so those requests are excluded.
    """
    is_htmx = bool(request.headers.get("HX-Request"))
    is_history_restore = bool(request.headers.get("HX-History-Restore-Request"))
    return is_htmx and not is_history_restore


def _select_candidate(candidates, pick_id):
    """Return the candidate matching pick_id, or the top search result by default.

    Compares IDs as strings so non-numeric user input in the query string
    can never raise a ValueError.
    """
    for candidate in candidates:
        if str(candidate.catalog_id) == pick_id:
            return candidate
    return candidates[0]


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
            chosen = _select_candidate(candidates, pick)
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
    for candidate in candidates:
        key = song_key(candidate.title, candidate.artist)
        if key in seen:
            continue
        seen.add(key)
        alts.append(candidate)
    return alts[:max_items]


def _safe_verify(rec):
    """Look up one recommendation in iTunes. Returns (rec, match, lookup_ok).

    lookup_ok is False when the HTTP request itself failed (network error,
    rate limit), as opposed to the lookup succeeding but finding no match.
    """
    try:
        match = verify_recommendation(rec.title, rec.artist)
        return rec, match, True
    except CatalogError:
        logger.warning("iTunes lookup lowk failed while verifying %r by %r", rec.title, rec.artist)
        return rec, None, False


def _verify_batch(batch):
    """Look up a batch of recommendations in iTunes concurrently, keeping their order."""
    with ThreadPoolExecutor(max_workers=SHOWN_COUNT) as pool:
        return list(pool.map(_safe_verify, batch))


def _verify_until_enough(track, recs):
    """Verify Gemini's picks, closest first, until SHOWN_COUNT have passed.

    Picks are checked in batches of SHOWN_COUNT, so the backup picks only cost
    extra iTunes calls when some of the first batch don't verify.
    Returns (verified, failed): a list of (recommendation, catalog match) and
    the number of lookups that errored out.
    """
    seen = {song_key(track.title, track.artist)}  # never recommend the searched song
    verified = []
    failed = 0

    for start in range(0, len(recs), SHOWN_COUNT):
        if len(verified) >= SHOWN_COUNT:
            break

        batch = recs[start:start + SHOWN_COUNT]
        for rec, match, lookup_ok in _verify_batch(batch):
            if not lookup_ok:
                failed += 1
                continue
            if match is None:
                continue
            key = song_key(match.title, match.artist)
            if key in seen:
                continue
            seen.add(key)
            verified.append((rec, match))

        # If every lookup in the first batch failed with a network error,
        # iTunes is unreachable—don't waste time trying the next batch.
        if not verified and failed == len(batch):
            break

    return verified[:SHOWN_COUNT], failed


def _get_results(track):
    """Cache-first. Returns (recommendations_list, commentary_str).

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

    rows = []
    for rec, match in verified:
        rows.append(
            TrackRecommendation(
                catalog_id=match.catalog_id,
                title=match.title,
                artist=match.artist,
                rationale=rec.rationale,
                artwork_url=match.artwork_url,
                link=match.link,
                preview_url=match.preview_url,
            )
        )

    if failed or len(rows) < MIN_TO_CACHE:
        return rows, commentary  # show them, but don't cache

    with transaction.atomic():
        if cached:
            # Replace any old cache entry saved before the commentary field existed.
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