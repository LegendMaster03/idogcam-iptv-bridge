"""Extension-compatible, credential-free rewriting of HLS playlists."""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit


class InvalidUpstreamURL(ValueError):
    pass


def same_origin(base: str, candidate: str) -> bool:
    a = urlsplit(base)
    b = urlsplit(candidate)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port) and bool(b.hostname) and not b.username and not b.password


def media_suffix(url: str, *, kind: str) -> str:
    if kind == 'playlist':
        return 'm3u8'
    ext = urlsplit(url).path.rsplit('/', 1)[-1].rsplit('.', 1)
    if len(ext) == 2 and ext[1].lower() in ('ts', 'aac', 'mp4', 'm4s', 'vtt', 'webvtt', 'key', 'bin'):
        return ext[1].lower()
    return 'ts'  # Proven iDogCam ++hls_media segments contain MPEG-TS data.


@dataclass
class Token:
    channel_id: int
    upstream_url: str
    kind: str
    expires_at: float


class TokenMap:
    def __init__(self, ttl: int = 180, max_entries: int = 20000) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._items: dict[str, Token] = {}

    def put(self, channel_id: int, upstream_url: str, kind: str) -> str:
        if len(self._items) >= self.max_entries:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if v.expires_at > now}
            if len(self._items) >= self.max_entries:
                raise RuntimeError('Too many active segment tokens')
        key = secrets.token_urlsafe(24)
        self._items[key] = Token(channel_id, upstream_url, kind, time.monotonic() + self.ttl)
        return key

    def get(self, token: str, channel_id: int) -> Token | None:
        item = self._items.get(token)
        if not item:
            return None
        if item.expires_at <= time.monotonic():
            self._items.pop(token, None)
            return None
        return item if item.channel_id == channel_id else None


def rewrite_playlist(body: str, upstream_url: str, channel_id: int, tokens: TokenMap) -> str:
    if not body.lstrip('\ufeff').startswith('#EXTM3U'):
        raise ValueError('Upstream response was not an HLS playlist')
    if len(body) > 512_000:
        raise ValueError('Oversized upstream playlist')
    rewritten: list[str] = []
    expect_variant = False

    def proxy_uri(value: str, kind: str) -> str:
        target = urljoin(upstream_url, value)
        if not same_origin(upstream_url, target):
            raise InvalidUpstreamURL('Cross-origin HLS resource not permitted')
        suffix = media_suffix(target, kind=kind)
        opaque = tokens.put(channel_id, target, kind)
        return f'/media/{channel_id}/{opaque}.{suffix}'

    for line in body.splitlines():
        value = line.strip()
        if value.startswith('#'):
            if value.startswith('#EXT-X-STREAM-INF:'):
                expect_variant = True
            if 'URI=' in value:
                kind = 'playlist' if value.startswith(('#EXT-X-MEDIA:', '#EXT-X-I-FRAME-STREAM-INF:')) else 'media'
                value = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + proxy_uri(m[1], kind) + '"', value)
            rewritten.append(value)
        elif value:
            kind = 'playlist' if expect_variant or urlsplit(value).path.endswith('.m3u8') else 'media'
            rewritten.append(proxy_uri(value, kind))
            expect_variant = False
        else:
            rewritten.append(value)
    return '\n'.join(rewritten) + '\n'
