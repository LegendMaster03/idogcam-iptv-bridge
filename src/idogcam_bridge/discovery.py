"""Conservative extraction of viewer links from explicitly supplied iDogCam listings.

No probing of undisclosed numeric IDs and no session or authentication bypass.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

TRUSTED_VIEWER_HOSTS = frozenset({'idogcam.com', 'www.idogcam.com'})
VIEWER_PATH = '/idogcamgingrviewv2.php'
MAX_LISTING_BYTES = 1_000_000
MAX_IMPORT_CANDIDATES = 50


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == 'a':
            for key, value in attrs:
                if key.lower() == 'href' and value:
                    self.hrefs.append(value)


def validate_official_listing_url(url: str) -> str:
    """Allow fetching only iDogCam's public HTTPS website, not arbitrary URLs."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in TRUSTED_VIEWER_HOSTS
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.fragment or not parsed.path.endswith('.php')):
        raise ValueError('Listing URL must be an HTTPS page on the official iDogCam website')
    return url


def extract_viewer_ids(page: str, *, listing_url: str) -> tuple[int, ...]:
    """Return only Gingr V2 viewers linked by a supplied, official listing.

    Deliberately ignore numeric IDs merely occurring in scripts, data attributes,
    and unknown URL formats. The caller verifies each official page's facility.
    """
    validate_official_listing_url(listing_url)
    parser = _Anchors()
    parser.feed(page)
    found: set[int] = set()
    for href in parser.hrefs:
        try:
            parsed = urlsplit(urljoin(listing_url, href))
            port = parsed.port
        except ValueError:
            continue
        if (parsed.scheme != 'https' or parsed.hostname not in TRUSTED_VIEWER_HOSTS
                or parsed.username or parsed.password or port is not None
                or parsed.path != VIEWER_PATH or parsed.fragment):
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        ids = query.get('id', [])
        if len(ids) == 1 and ids[0].isascii() and ids[0].isdecimal():
            viewer_id = int(ids[0])
            if viewer_id > 0:
                found.add(viewer_id)
    if len(found) > MAX_IMPORT_CANDIDATES:
        raise ValueError('Listing exceeds the safe import limit; narrow the selected facility')
    return tuple(sorted(found))
