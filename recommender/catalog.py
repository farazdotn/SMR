import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


class CatalogError(Exception):
    """Raised when the iTunes Search API can't be reached or misbehaves."""


@dataclass
class CatalogTrack:
    catalog_id: int
    title: str
    artist: str
    album: str
    artwork_url: str
    preview_url: str
    link: str


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation, but keep letters from ANY script."""
    text = re.sub(r"[^\w\s]", "", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def _search(term: str, limit: int) -> list[CatalogTrack]:
    try:
        response = requests.get(
            ITUNES_SEARCH_URL,
            params={"term": term, "media": "music", "entity": "song", "limit": limit},
            timeout=10,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        raise CatalogError(f"iTunes lookup failed: {exc}") from exc

    tracks = []
    for item in results:
        if item.get("kind") != "song" or "trackId" not in item:
            continue
        tracks.append(
            CatalogTrack(
                catalog_id=item["trackId"],
                title=item.get("trackName", ""),
                artist=item.get("artistName", ""),
                album=item.get("collectionName", ""),
                artwork_url=item.get("artworkUrl100", "").replace("100x100", "600x600"),
                preview_url=item.get("previewUrl", ""),
                link=item.get("trackViewUrl", ""),
            )
        )
    return tracks


def search_candidates(query: str, limit: int = 5) -> list[CatalogTrack]:
    """Top matches for what the user typed (feeds the 'Wrong song?' button)."""
    query = query.strip()
    if not query:
        return []
    return _search(query, limit)


def _matches(rec_title: str, rec_artist: str, track: CatalogTrack) -> bool:
    want_title, got_title = _normalize(rec_title), _normalize(track.title)
    want_artist, got_artist = _normalize(rec_artist), _normalize(track.artist)
    if not (want_title and want_artist and got_title and got_artist):
        return False

    artist_ok = want_artist in got_artist or got_artist in want_artist
    title_ok = (
        want_title in got_title
        or SequenceMatcher(None, want_title, got_title).ratio() >= 0.85
    )
    return artist_ok and title_ok


def verify_recommendation(title: str, artist: str) -> CatalogTrack | None:
    """Find the catalog track that best matches a Gemini recommendation, or None."""
    want_title = _normalize(title)
    best, best_score = None, 0.0
    for track in _search(f"{title} {artist}", limit=10):
        if not _matches(title, artist, track):
            continue
        score = SequenceMatcher(None, want_title, _normalize(track.title)).ratio()
        if score > best_score:
            best, best_score = track, score
    return best

def song_key(title: str, artist: str) -> str:
    return f"{_normalize(title)}|{_normalize(artist)}"