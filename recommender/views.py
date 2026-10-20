import logging
from concurrent.futures import ThreadPoolExecutor
from django.db import transaction
from django.db.models import F
from django.shortcuts import render
from django.views.decorators.vary import vary_on_headers
from .catalog import CatalogError, search_candidates, song_key, verify_recommendation
from .gemini_service import GeminiRateLimitError, GeminiServiceError, get_recommendations
from .models import SearchQuery, TrackRecommendation

logger = logging.getLogger(__name__)

SHOWN_COUNT = 5   
MIN_TO_CACHE = 3  

def index(request):
    return render(request, "index.html")


def _wants_fragment(request):
    is_htmx = bool(request.headers.get("HX-Request"))
    is_history_restore = bool(request.headers.get("HX-History-Restore-Request"))
    return is_htmx and not is_history_restore


def _select_candidate(candidates, pick_id):
    for candidate in candidates:
        if str(candidate.catalog_id) == pick_id:
            return candidate
    return candidates[0]


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
    try:
        match = verify_recommendation(rec.title, rec.artist)
        return rec, match, True
    except CatalogError:
        logger.warning("iTunes lookup lowk failed while verifying %r by %r", rec.title, rec.artist)
        return rec, None, False


def _verify_batch(batch):
    with ThreadPoolExecutor(max_workers=SHOWN_COUNT) as pool:
        return list(pool.map(_safe_verify, batch))


def _verify_until_enough(track, recs):
    seen = {song_key(track.title, track.artist)}  
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

        if not verified and failed == len(batch):
            break

    return verified[:SHOWN_COUNT], failed


def _get_results(track):
    key = song_key(track.title, track.artist)
    cached = SearchQuery.objects.filter(song_key=key).first()
    if cached and cached.commentary:
        SearchQuery.objects.filter(pk=cached.pk).update(request_count=F("request_count") + 1)
        return list(cached.recommendations.all()), cached.commentary

    recs, commentary = get_recommendations(track.title, track.artist)
    verified, failed = _verify_until_enough(track, recs)

    if not verified:
        if failed:
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
        return rows, commentary  

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