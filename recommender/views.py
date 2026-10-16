import logging
from django.db import transaction
from django.db.models import F
from django.shortcuts import render
from concurrent.futures import ThreadPoolExecutor
from .catalog import CatalogError, search_candidates, song_key, verify_recommendation
from .gemini_service import (
    GeminiRateLimitError,
    GeminiServiceError,
    get_recommendations,
)
from .models import SearchQuery, TrackRecommendation

logger = logging.getLogger(__name__)


def index(request):
    return render(request, "index.html")


def search(request):
    q = request.GET.get("q", "").strip()[:200]
    pick = request.GET.get("pick", "")
    template = "_results.html" if request.headers.get("HX-Request") else "index.html"
    context = {"q": q}

    if not q:
        context["error"] = "Type a song first. I can judge your taste, not your silence."
        return render(request, template, context)

    try:
        candidates = search_candidates(q, limit=10)
        if not candidates:
            context["error"] = f"Couldn't find '{q}'. Check the spelling, or add the artist's name."
        else:
            chosen = candidates[0]
            if pick.isdigit():
                chosen = next((c for c in candidates if c.catalog_id == int(pick)), chosen)
            context["chosen"] = chosen
            context["alternatives"] = _alternatives(candidates, chosen)

            search_obj = _get_results(chosen)
            if search_obj is None:
                context["error"] = "I couldn't verify any recommendations for that one. Try another song."
            else:
                context["search"] = search_obj
                context["recommendations"] = search_obj.recommendations.all()
    except GeminiRateLimitError:
        context["error"] = "The robot musicologist needs a break. Try again in a minute."
    except GeminiServiceError:
        logger.exception("Gemini failed")
        context["error"] = "The musicologist is having a moment. Try again shortly."
    except CatalogError:
        logger.exception("iTunes lookup failed")
        context["error"] = "Couldn't reach the music catalog. Try again shortly."

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


def _verify_all(recs):
    """Look up every recommendation in iTunes at once. Results keep Gemini's order."""
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(lambda r: verify_recommendation(r.title, r.artist), recs))


def _get_results(track):
    """Cache-first: return a SearchQuery with recommendations, or None."""
    cached = SearchQuery.objects.filter(catalog_id=track.catalog_id).first()
    if cached:
        SearchQuery.objects.filter(pk=cached.pk).update(request_count=F("request_count") + 1)
        return cached

    recs = get_recommendations(track.title, track.artist)
    matches = _verify_all(recs)

    seen = {song_key(track.title, track.artist)}  # never recommend the searched song
    verified = []
    for rec, match in zip(recs, matches):
        if match is None:
            continue
        key = song_key(match.title, match.artist)
        if key in seen:
            continue
        seen.add(key)
        verified.append((rec, match))

    if not verified:
        return None

    with transaction.atomic():
        search_obj, created = SearchQuery.objects.get_or_create(
            catalog_id=track.catalog_id,
            defaults={
                "title": track.title,
                "artist": track.artist,
                "artwork_url": track.artwork_url,
                "link": track.link,
            },
        )
        if created:
            TrackRecommendation.objects.bulk_create(
                TrackRecommendation(
                    search_query=search_obj,
                    catalog_id=match.catalog_id,
                    title=match.title,
                    artist=match.artist,
                    rationale=rec.rationale,
                    artwork_url=match.artwork_url,
                    link=match.link,
                    preview_url=match.preview_url,
                )
                for rec, match in verified
            )
    return search_obj