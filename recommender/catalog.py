import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import httpx

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_TIMEOUT_SECONDS = 4

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

# Latin letters that don't decompose into "base letter + accent" via NFKD.
_LATIN_FOLDS = str.maketrans(
    {"ø": "o", "ł": "l", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe"}
)
# Matches ASCII hyphen, Unicode hyphens/dashes (U+2010..U+2015), and minus sign (U+2212).
_DASHES = re.compile(r"[-\u2010-\u2015\u2212]")


def _strip_latin_accents(text: str) -> str:
    """Turn 'Beyoncé' into 'Beyonce', while leaving non-Latin scripts untouched.

    Decomposes the text, then drops combining marks only when they sit on a
    Latin base letter. Marks on Devanagari, Thai, Hangul, kana, Cyrillic
    (й = и + breve) and so on are part of the letter and are kept.
    """
    if text.isascii():
        return text

    kept = []
    base_is_latin = False
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch):
            if base_is_latin:
                continue
        else:
            base_is_latin = unicodedata.name(ch, "").startswith("LATIN")
        kept.append(ch)

    return unicodedata.normalize("NFC", "".join(kept))


def _normalize(text: str) -> str:
    """Return a simplified lowercase string for fuzzy title and artist comparisons."""
    text = _strip_latin_accents(text.casefold()).translate(_LATIN_FOLDS)
    text = text.replace("&", " and ").replace("+", " and ")
    text = _DASHES.sub(" ", text)

    # Keep whitespace and characters in Unicode categories:
    # L (Letters), N (Numbers), and M (combining Marks needed by non-Latin scripts).
    kept_chars = []
    for ch in text:
        category = unicodedata.category(ch)[0]
        if ch.isspace() or category in ("L", "N", "M"):
            kept_chars.append(ch)

    return " ".join("".join(kept_chars).split())


# --------------------------------------------------------------------------
# Titles: ignore "(feat. X)", "- Remastered 2011" and similar release noise
# --------------------------------------------------------------------------

# Tags that describe the release, not the song.
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

# Words that indicate a different recording altogether rather than release metadata.
_ALT_RECORDING = re.compile(
    r"\b(?:live|remix|rmx|acoustic|demo|instrumental|karaoke)\b",
    re.IGNORECASE,
)


def _remove_bracket_if_noise(match: re.Match) -> str:
    """Drop a '(...)' or '[...]' section only if it contains release noise."""
    text = match.group(0)
    if _VERSION_NOISE.search(text):
        return ""
    return text


def _strip_version_noise(title: str) -> str:
    title = unicodedata.normalize("NFKC", title)  # full-width brackets -> ASCII
    title = _BRACKETED.sub(_remove_bracket_if_noise, title)

    parts = _DASH_SPLIT.split(title)
    while len(parts) > 1 and _VERSION_NOISE.search(parts[-1]):
        parts.pop()
    return " - ".join(parts).strip()


@lru_cache(maxsize=4096)
def _title_forms(title: str) -> tuple[str, str]:
    """Return (full, bare) normalized comparison forms of a song title.

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


def _titles_match(want: str, got: str) -> bool:
    want_is_alt = bool(_ALT_RECORDING.search(want))
    got_is_alt = bool(_ALT_RECORDING.search(got))
    if want_is_alt != got_is_alt:
        return False
    return _title_score(want, got) >= TITLE_MATCH_THRESHOLD


def _artists_match(want: str, got: str) -> bool:
    w = _normalize(want)
    g = _normalize(got)
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

_http_client = httpx.Client(timeout=ITUNES_TIMEOUT_SECONDS)

def _search(term: str, limit: int) -> list[CatalogTrack]:
    try:
        response = _http_client.get(
            ITUNES_SEARCH_URL,
            params={"term": term, "media": "music", "entity": "song", "limit": limit},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise CatalogError(f"iTunes lookup failed: {exc}") from exc

    tracks = []
    for item in results:
        if item.get("kind") != "song" or "trackId" not in item:
            continue
        artwork_100 = item.get("artworkUrl100") or ""
        tracks.append(
            CatalogTrack(
                catalog_id=item["trackId"],
                title=item.get("trackName") or "",
                artist=item.get("artistName") or "",
                album=item.get("collectionName") or "",
                artwork_url=artwork_100.replace("100x100", "600x600"),
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
    best = None
    best_score = 0.0
    for track in _search(f"{title} {artist}", limit=10):
        if not _matches(title, artist, track):
            continue
        score = _title_score(title, track.title)
        if score > best_score:
            best = track
            best_score = score
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