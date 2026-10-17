import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

import requests

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

TITLE_MATCH_THRESHOLD = 0.85
# Keep in sync with SearchQuery.song_key's max_length in models.py.
SONG_KEY_MAX_LENGTH = 600


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


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

# Latin letters that don't decompose into "base letter + accent".
_LATIN_FOLDS = str.maketrans(
    {"ø": "o", "ł": "l", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe"}
)
_DASHES = re.compile(r"[-‐-―−]")


def _strip_latin_accents(text: str) -> str:
    """Beyoncé -> Beyonce, but leave other scripts alone.

    Decomposes the text, then drops combining marks only when they sit on a
    Latin base letter. Marks on Devanagari, Thai, Hangul, kana, Cyrillic
    (й = и + breve) and so on are part of the letter and are kept.
    """
    if text.isascii():
        return text
    kept, base_is_latin = [], False
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch):
            if base_is_latin:
                continue
        else:
            base_is_latin = unicodedata.name(ch, "").startswith("LATIN")
        kept.append(ch)
    return unicodedata.normalize("NFC", "".join(kept))


def _normalize(text: str) -> str:
    text = _strip_latin_accents(text.casefold()).translate(_LATIN_FOLDS)
    text = text.replace("&", " and ").replace("+", " and ")
    text = _DASHES.sub(" ", text)
    text = "".join(
        ch for ch in text if ch.isspace() or unicodedata.category(ch)[0] in "LNM"
    )
    return " ".join(text.split())


# --------------------------------------------------------------------------
# Titles: ignore "(feat. X)", "- Remastered 2011" and similar release noise
# --------------------------------------------------------------------------

# Tags that describe the release, not the song. Deliberately NOT included:
# live, remix, acoustic, demo, instrumental. Those are different recordings.
_VERSION_NOISE = re.compile(
    r"\b(?:"
    r"feat|featuring|ft|"
    r"remaster(?:ed)?|"
    r"mono|stereo|explicit|clean|deluxe|bonus|expanded|anniversary|"
    r"(?:single|album|radio|original|short|extended|edited)\s+(?:version|edit|mix)|"
    r"radio\s+edit"
    r")\b",
    re.IGNORECASE,
)
_BRACKETED = re.compile(r"\s*[(\[][^()\[\]]*[)\]]")
_DASH_SPLIT = re.compile(r"\s+[-–—]\s+")


def _strip_version_noise(title: str) -> str:
    title = unicodedata.normalize("NFKC", title)  # full-width brackets -> ASCII
    title = _BRACKETED.sub(
        lambda m: "" if _VERSION_NOISE.search(m.group(0)) else m.group(0), title
    )
    parts = _DASH_SPLIT.split(title)
    while len(parts) > 1 and _VERSION_NOISE.search(parts[-1]):
        parts.pop()
    return " - ".join(parts).strip()


@lru_cache(maxsize=4096)
def _title_forms(title: str) -> tuple[str, str]:
    """(full, bare) comparison forms of a title.

    full: release noise removed, everything else kept.
    bare: additionally drops every (...) / [...] group, so
          "(I Can't Get No) Satisfaction" still equals "Satisfaction" and
          "Rocket Man (I Think It's Going to Be a Long Long Time)" equals
          "Rocket Man".
    """
    stripped = _strip_version_noise(title)
    full = _normalize(stripped) or _normalize(title)
    bare = _normalize(_BRACKETED.sub("", stripped))
    return full, bare


def _title_score(want: str, got: str) -> float:
    want_full, want_bare = _title_forms(want)
    got_full, got_bare = _title_forms(got)
    if not want_full or not got_full:
        return 0.0
    if want_full == got_full:
        return 1.0
    ratio = SequenceMatcher(None, want_full, got_full).ratio()
    if want_bare and want_bare == got_bare:
        return max(ratio, 0.9)
    return ratio

_ALT_RECORDING = re.compile(
    r"\b(?:live|remix|rmx|acoustic|demo|instrumental|karaoke)\b",
    re.IGNORECASE,
)

def _titles_match(want: str, got: str) -> bool:
    if bool(_ALT_RECORDING.search(want)) != bool(_ALT_RECORDING.search(got)):
        return False
    return _title_score(want, got) >= TITLE_MATCH_THRESHOLD

def _artists_match(want: str, got: str) -> bool:
    w, g = _normalize(want), _normalize(got)
    if not w or not g:
        return False
    if w == g or w.replace(" ", "") == g.replace(" ", ""):
        return True
    # Whole-word containment, so "Prince" matches "Prince and the Revolution"
    # and "Daft Punk" matches "Daft Punk & Pharrell Williams", but "Air" no
    # longer matches "Chairlift".
    return f" {w} " in f" {g} " or f" {g} " in f" {w} "


def _matches(rec_title: str, rec_artist: str, track: CatalogTrack) -> bool:
    return _artists_match(rec_artist, track.artist) and _titles_match(rec_title, track.title)


# --------------------------------------------------------------------------
# iTunes lookups
# --------------------------------------------------------------------------

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
                title=item.get("trackName") or "",
                artist=item.get("artistName") or "",
                album=item.get("collectionName") or "",
                artwork_url=(item.get("artworkUrl100") or "").replace("100x100", "600x600"),
                preview_url=item.get("previewUrl") or "",
                link=item.get("trackViewUrl") or "",
            )
        )
    return tracks


def search_candidates(query: str, limit: int = 5) -> list[CatalogTrack]:
    """Top matches for what the user typed (feeds the 'Wrong song?' button)."""
    query = query.strip()
    if not query:
        return []
    return _search(query, limit)


def verify_recommendation(title: str, artist: str) -> CatalogTrack | None:
    """Find the catalog track that best matches a Gemini recommendation, or None."""
    best, best_score = None, 0.0
    for track in _search(f"{title} {artist}", limit=10):
        if not _matches(title, artist, track):
            continue
        score = _title_score(title, track.title)
        if score > best_score:
            best, best_score = track, score
    return best


def song_key(title: str, artist: str) -> str:
    """Identity of a song across releases. Used for the cache and for de-duplicating.

    "Reckoner", "Reckoner - Remastered" and "Reckoner (feat. Someone)" by the
    same artist all produce the same key, whether they come from a single, an
    album or a reissue.

    Saved keys depend on how this function normalizes: if you change the rules
    later, old cache rows stop matching (they are simply regenerated).
    """
    full, _bare = _title_forms(title)
    return f"{full}|{_normalize(artist)}"[:SONG_KEY_MAX_LENGTH]