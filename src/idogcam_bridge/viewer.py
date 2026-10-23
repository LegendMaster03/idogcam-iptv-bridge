"""Extract official Gingr V2 camera metadata and viewer-provided authorization."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .config import validate_manifest_url, validate_stream_origin


@dataclass(frozen=True)
class ViewerMetadata:
    viewer_id: int
    full_title: str
    name: str
    camera_num: int | None
    offline_message: str | None
    stream_url: str | None = field(default=None, repr=False, compare=False)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.suppressed = 0
        self.lines: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('script', 'style'):
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ('script', 'style') and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            s = ' '.join(data.split())
            if s:
                self.lines.append(s)


def _viewer_stream_url(html_source: str, camera_num: int, configured_origin: str | None) -> str | None:
    """Resolve a stream only from credentials published in *this viewer's* HTML.

    Refuse ambiguous credentials/hosts. Never synthesize credentials from an
    earlier HAR, another viewer, or a default username/password.
    """
    page = html.unescape(html_source).replace('\\/', '/')

    # The verified pages contain auth=... in their JavaScript. No credentials
    # are decoded, printed, or put into the Jellyfin M3U.
    credentials = {unquote(value) for value in re.findall(
        r'(?:[?&]|\b)auth=([A-Za-z0-9+/%_=-]{1,512})', page, re.I
    )}
    if len(credentials) != 1:
        return None
    auth = next(iter(credentials))
    if not auth or not re.fullmatch(r'[A-Za-z0-9+/_=-]{1,512}', auth):
        return None

    origins = set()
    for match in re.finditer(r'https://[a-zA-Z0-9.-]+\.viewidogcam\.com(?::\d{1,5})?', page, re.I):
        try:
            origins.add(validate_stream_origin(match.group(0)))
        except ValueError:
            pass

    if configured_origin:
        configured_origin = validate_stream_origin(configured_origin)
        # If viewer publishes an origin, it must agree with configuration.
        if origins and configured_origin not in origins:
            return None
        origin = configured_origin
    elif len(origins) == 1:
        origin = next(iter(origins))
    else:
        return None

    qualities = set(re.findall(r'\bquality\s*=\s*["\']?([012])\b', page, re.I))
    if len(qualities) > 1:
        return None
    # High quality (2) is verified for Dog Beach; unknown/other variants need
    # their own adapter rather than guessing their video profile.
    if not qualities:
        return None
    quality = next(iter(qualities))
    url = (f'{origin}/++hls_mediaplaylist?cameraNum={camera_num}'
           f'&quality={quality}&auth={quote(auth, safe="")}' )
    return validate_manifest_url(url)


def parse_viewer(viewer_id: int, html_source: str, facility_name: str,
                 stream_origin: str | None = None) -> ViewerMetadata:
    title_match = re.search(r'<title\b[^>]*>(.*?)</title>', html_source, re.I | re.S)
    if not title_match:
        raise ValueError('Viewer has no official title')
    title = html.unescape(re.sub(r'\s+', ' ', title_match.group(1))).strip()
    if not title or len(title) > 250:
        raise ValueError('Invalid official title')
    name = title[len(facility_name) + 3:] if title.startswith(facility_name + ' - ') else title

    candidates = []
    for pattern in (
        r'cameraNum\s*=\s*["\']?(\d+)\b',
        r'cameraNum\s*["\']?\s*:\s*["\']?(\d+)\b',
        r'cameraNum=(\d+)\b',
        r'cameraNum%3D(\d+)\b',
    ):
        candidates.extend(int(n) for n in re.findall(pattern, html_source, re.I))
    camera_num = candidates[0] if candidates else None
    if candidates and any(n != camera_num for n in candidates):
        raise ValueError('Conflicting cameraNum values in viewer metadata')

    parser = _VisibleText()
    parser.feed(html_source)
    message = next((line for line in parser.lines if
                    len(line) <= 500 and line != title and
                    re.search(r'\b(?:daily|offline|closed|unavailable|not available|hours|\d{1,2}\s*(?:am|pm))\b', line, re.I)), None)
    stream = _viewer_stream_url(html_source, camera_num, stream_origin) if camera_num is not None else None
    return ViewerMetadata(viewer_id, title, name, camera_num, message, stream)
