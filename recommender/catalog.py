import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import httpx

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_TIMEOUT_SECONDS = 4
TITLE_MATCH_THRESHOLD = 0.85
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


_LATIN_FOLDS = str.maketrans(
    {"ø": "o", "ł": "l", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe"}
)
_DASHES = re.compile(r"[-\u2010-\u2015\u2212]")


def _strip_latin_accents(text: str) -> str:
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
    text = _strip_latin_accents(text.casefold()).translate(_LATIN_FOLDS)
    text = text.replace("&", " and ").replace("+", " and ")
    text = _DASHES.sub(" ", text)

    kept_chars = []
    for ch in text:
        category = unicodedata.category(ch)[0]
        if ch.isspace() or category in ("L", "N", "M"):
            kept_chars.append(ch)

    return " ".join("".join(kept_chars).split())


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

_ALT_RECORDING = re.compile(
    r"\b(?:live|remix|rmx|acoustic|demo|instrumental|karaoke)\b",
    re.IGNORECASE,
)


def _remove_bracket_if_noise(match: re.Match) -> str:
    text = match.group(0)
    if _VERSION_NOISE.search(text):
        return ""
    return text


def _strip_version_noise(title: str) -> str:
    title = unicodedata.normalize("NFKC", title) 
    title = _BRACKETED.sub(_remove_bracket_if_noise, title)

    parts = _DASH_SPLIT.split(title)
    while len(parts) > 1 and _VERSION_NOISE.search(parts[-1]):
        parts.pop()
    return " - ".join(parts).strip()


@lru_cache(maxsize=4096)
def _title_forms(title: str) -> tuple[str, str]:
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
    return f" {w} " in f" {g} " or f" {g} " in f" {w} "


def _matches(rec_title: str, rec_artist: str, track: CatalogTrack) -> bool:
    return _artists_match(rec_artist, track.artist) and _titles_match(rec_title, track.title)

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
    query = query.strip()
    if not query:
        return []
    return _search(query, limit)


def verify_recommendation(title: str, artist: str) -> CatalogTrack | None:
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
    full, _bare = _title_forms(title)
    return f"{full}|{_normalize(artist)}"[:SONG_KEY_MAX_LENGTH]