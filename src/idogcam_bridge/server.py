"""On-demand Gingr V2 to Jellyfin IPTV bridge.

Never emits the upstream authorization URL in HTTP responses or logs.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import Settings
from .guide import PublishedNoticeCache, make_xmltv
from .hls import TokenMap, rewrite_playlist, same_origin
from .viewer import ViewerMetadata, parse_viewer

LOG = logging.getLogger('idogcam_bridge')
BRIDGE_KEY = web.AppKey('bridge', 'Bridge')
BASE_URL_KEY = web.AppKey('base_url', str)
MAX_HTML = 1_000_000
MAX_MANIFEST = 512_000


async def _read_bounded(response, limit: int) -> bytes:
    """Read every upstream chunk through EOF without exceeding the size limit.

    aiohttp StreamReader.read(n) can return fewer than n bytes even when
    the response has not finished, notably with chunked transfer encoding.
    """
    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(payload) + len(chunk) > limit:
            raise ValueError('Upstream response exceeds size limit')
        payload.extend(chunk)
    return bytes(payload)


class UpstreamUnauthorized(Exception):
    """No credentials or upstream URL included in this exception."""


@dataclass
class MetadataCacheEntry:
    value: ViewerMetadata | None
    refreshed_at: float


class Bridge:
    def __init__(self, settings: Settings, notice_cache_path: str | None = None):
        self.settings = settings
        self.notice_cache = PublishedNoticeCache(notice_cache_path)
        self.cache: dict[int, MetadataCacheEntry] = {}
        self.locks: dict[int, asyncio.Lock] = {camera: asyncio.Lock() for camera in settings.cameras}
        self.tokens = TokenMap()
        self.client: ClientSession | None = None

    async def open(self) -> None:
        self.client = ClientSession(timeout=ClientTimeout(total=20), trust_env=False, headers={'User-Agent': 'iDogCam-IPTV-Bridge/0.1'})

    async def close(self) -> None:
        if self.client:
            await self.client.close()

    def _session(self) -> ClientSession:
        if self.client is None:
            raise RuntimeError('HTTP client not started')
        return self.client

    async def metadata(self, viewer_id: int, force_refresh: bool = False) -> ViewerMetadata | None:
        previous = self.cache.get(viewer_id)
        if not force_refresh and previous and time.monotonic() - previous.refreshed_at < self.settings.metadata_ttl_seconds:
            return previous.value
        async with self.locks[viewer_id]:
            previous = self.cache.get(viewer_id)
            if not force_refresh and previous and time.monotonic() - previous.refreshed_at < self.settings.metadata_ttl_seconds:
                return previous.value
            facility = self.settings.cameras[viewer_id]
            try:
                # Only the known official Gingr V2 viewer is supported.
                url = f'https://idogcam.com/idogcamgingrviewv2.php?id={viewer_id}'
                async with self._session().get(url, allow_redirects=False) as res:
                    if res.status != 200:
                        raise ValueError('Viewer not available')
                    if res.content_length and res.content_length > MAX_HTML:
                        raise ValueError('Viewer page oversized')
                    payload = await _read_bounded(res, MAX_HTML)
                result = parse_viewer(viewer_id, payload.decode('utf-8', 'replace'), facility.name, facility.stream_origin)
                if result.offline_message:
                    try:
                        self.notice_cache.remember(viewer_id, result.offline_message)
                    except OSError:
                        LOG.warning('Unable to persist public schedule notice for viewer %d', viewer_id)
                else:
                    last_notice = self.notice_cache.get(viewer_id)
                    if last_notice:
                        result = replace(result, offline_message=last_notice)
            except (ClientError, asyncio.TimeoutError, UnicodeError, ValueError) as exc:
                LOG.warning('Metadata refresh failed for viewer %d (%s)', viewer_id, type(exc).__name__)
                result = previous.value if previous else None
            self.cache[viewer_id] = MetadataCacheEntry(result, time.monotonic())
            return result

    async def upstream_for(self, viewer_id: int) -> str | None:
        meta = await self.metadata(viewer_id)
        return meta.stream_url if meta else None


def _name(value: str) -> str:
    return re.sub(r'[\r\n"\t]', ' ', html.unescape(value)).strip()


async def channels(request: web.Request) -> web.Response:
    bridge: Bridge = request.app[BRIDGE_KEY]
    meta = await asyncio.gather(*(bridge.metadata(v) for v in bridge.settings.cameras))
    lines = ['#EXTM3U']
    for (id_, facility), information in zip(bridge.settings.cameras.items(), meta):
        name = _name(information.name) if information else f'Camera {id_}'
        lines.append(
            f'#EXTINF:-1 tvg-id="{id_}" tvg-chno="{id_}" '
            f'tvg-name="{name}" group-title="{_name(facility.name)}",{name}'
        )
        lines.append(f'{request.app[BASE_URL_KEY]}/hls/{id_}/index.m3u8')
    return web.Response(text='\n'.join(lines) + '\n', content_type='audio/x-mpegurl', headers={'Cache-Control': 'no-store'})


async def guide_xmltv(request: web.Request) -> web.Response:
    bridge: Bridge = request.app[BRIDGE_KEY]
    metadata = await asyncio.gather(*(bridge.metadata(v) for v in bridge.settings.cameras))
    sources = [(id_, facility, meta)
               for (id_, facility), meta in zip(bridge.settings.cameras.items(), metadata)]
    document = make_xmltv(sources)
    return web.Response(body=document, content_type='application/xml',
                        headers={'Cache-Control': 'no-store'})


async def info(request: web.Request) -> web.Response:
    bridge: Bridge = request.app[BRIDGE_KEY]
    result = []
    for viewer_id, facility in bridge.settings.cameras.items():
        metadata = await bridge.metadata(viewer_id)
        result.append({
            'viewer_id': viewer_id,
            'facility': facility.name,
            'name': metadata.name if metadata else f'Camera {viewer_id}',
            'camera_number': metadata.camera_num if metadata else None,
            'viewer_metadata_available': metadata is not None,
            'offline_message': metadata.offline_message if metadata else None,
            'stream_resolvable': bool(metadata and metadata.stream_url),
            # Deliberately no 'online' status: that requires an HLS fetch.
        })
    return web.json_response({'channels': result}, headers={'Cache-Control': 'no-store'})


async def hls_playlist(request: web.Request) -> web.Response:
    bridge: Bridge = request.app[BRIDGE_KEY]
    viewer_id = int(request.match_info['viewer_id'])
    if viewer_id not in bridge.settings.cameras:
        raise web.HTTPNotFound()
    source = await bridge.upstream_for(viewer_id)
    if source is None:
        raise web.HTTPServiceUnavailable(text='Camera is offline or not configured')
    try:
        return await _read_playlist(bridge, source, viewer_id)
    except UpstreamUnauthorized:
        # The official viewer can publish updated authorization without a
        # new bridge deployment. Refresh that one page and retry once.
        await bridge.metadata(viewer_id, force_refresh=True)
        replacement = await bridge.upstream_for(viewer_id)
        if not replacement or replacement == source:
            raise web.HTTPServiceUnavailable(text='Upstream viewer authorization unavailable') from None
        try:
            return await _read_playlist(bridge, replacement, viewer_id)
        except UpstreamUnauthorized:
            raise web.HTTPServiceUnavailable(text='Upstream viewer authorization unavailable') from None


async def _read_playlist(bridge: Bridge, upstream: str, viewer_id: int) -> web.Response:
    try:
        async with bridge._session().get(upstream, allow_redirects=False, headers={'Origin': 'https://idogcam.com', 'Referer': 'https://idogcam.com/'}) as res:
            if res.status == 401:
                raise UpstreamUnauthorized()
            if res.status != 200:
                raise web.HTTPServiceUnavailable(text='Upstream stream is not available')
            if res.content_length and res.content_length > MAX_MANIFEST:
                raise web.HTTPBadGateway(text='Invalid upstream playlist')
            raw = await _read_bounded(res, MAX_MANIFEST)
        content = rewrite_playlist(raw.decode('utf-8-sig'), upstream, viewer_id, bridge.tokens)
    except (ClientError, asyncio.TimeoutError, UnicodeError, ValueError, RuntimeError):
        raise web.HTTPBadGateway(text='Unable to process upstream playlist') from None
    return web.Response(text=content, content_type='application/vnd.apple.mpegurl', headers={'Cache-Control': 'no-store'})


async def media(request: web.Request) -> web.StreamResponse:
    bridge: Bridge = request.app[BRIDGE_KEY]
    viewer_id = int(request.match_info['viewer_id'])
    token = bridge.tokens.get(request.match_info['token'], viewer_id)
    if token is None or viewer_id not in bridge.settings.cameras:
        raise web.HTTPNotFound(text='Expired or unknown HLS resource')
    source = await bridge.upstream_for(viewer_id)
    if source is None or not same_origin(source, token.upstream_url):
        raise web.HTTPServiceUnavailable(text='Camera is offline')
    if token.kind == 'playlist':
        try:
            return await _read_playlist(bridge, token.upstream_url, viewer_id)
        except UpstreamUnauthorized:
            await bridge.metadata(viewer_id, force_refresh=True)
            raise web.HTTPServiceUnavailable(text='Upstream authorization updated; reload channel') from None
    try:
        async with bridge._session().get(token.upstream_url, allow_redirects=False,
                                         headers={'Origin': 'https://idogcam.com', 'Referer': 'https://idogcam.com/'}) as upstream:
            if upstream.status == 401:
                await bridge.metadata(viewer_id, force_refresh=True)
                raise web.HTTPServiceUnavailable(text='Upstream authorization updated; reload channel')
            if upstream.status != 200:
                raise web.HTTPServiceUnavailable(text='HLS segment not available')
            # Extension fixes FFmpeg's HLS parser. Upstream bytes are NOT transcoded.
            ext = request.match_info['ext']
            content_type = {'ts': 'video/mp2t', 'aac': 'audio/aac', 'm4s': 'video/iso.segment',
                            'mp4': 'video/mp4', 'vtt': 'text/vtt', 'key': 'application/octet-stream',
                            'bin': 'application/octet-stream'}.get(ext, 'application/octet-stream')
            outgoing = web.StreamResponse(status=200, headers={'Content-Type': content_type, 'Cache-Control': 'no-store'})
            await outgoing.prepare(request)
            async for part in upstream.content.iter_chunked(64 * 1024):
                await outgoing.write(part)
            await outgoing.write_eof()
            return outgoing
    except (ClientError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(text='Unable to retrieve HLS segment') from None


async def health(request: web.Request) -> web.Response:
    return web.json_response({'ok': True})


async def on_startup(app: web.Application) -> None:
    await app[BRIDGE_KEY].open()


async def on_cleanup(app: web.Application) -> None:
    await app[BRIDGE_KEY].close()


def create_app(settings: Settings, base_url: str = 'http://idogcam-bridge:8080',
               notice_cache_path: str | None = None) -> web.Application:
    parsed = urlsplit(base_url)
    if parsed.scheme != 'http' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Base URL should be an internal HTTP origin, such as http://idogcam-bridge:8080')
    app = web.Application(client_max_size=MAX_MANIFEST)
    app[BRIDGE_KEY] = Bridge(settings, notice_cache_path)
    app[BASE_URL_KEY] = base_url.rstrip('/')
    app.router.add_get('/healthz', health)
    app.router.add_get('/channels.m3u', channels)
    app.router.add_get('/xmltv.xml', guide_xmltv)
    app.router.add_get('/api/channels', info)
    app.router.add_get('/hls/{viewer_id:\\d+}/index.m3u8', hls_playlist)
    app.router.add_get('/media/{viewer_id:\\d+}/{token:[A-Za-z0-9_-]+}.{ext:[a-z0-9]+}', media)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    from .config import load_settings
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = load_settings(os.environ.get('IDOGCAM_CONFIG', '/config/config.json'))
    base_url = os.environ.get('IDOGCAM_BASE_URL', 'http://idogcam-bridge:8080')
    port = int(os.environ.get('IDOGCAM_PORT', '8080'))
    LOG.info('Starting bridge with %s facilities and %s authorized viewer IDs', len(settings.facilities), len(settings.cameras))
    notice_cache = os.environ.get('IDOGCAM_NOTICE_CACHE')
    web.run_app(create_app(settings, base_url=base_url, notice_cache_path=notice_cache),
                host='0.0.0.0', port=port, access_log=None)


if __name__ == '__main__':
    main()
