import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from idogcam_bridge.config import Settings, Facility
from idogcam_bridge.server import create_app, BRIDGE_KEY
from idogcam_bridge.viewer import ViewerMetadata


@pytest.mark.asyncio
async def test_on_demand_end_to_end_no_credentials_in_playlist():
    received = []

    async def hls(request):
        received.append(request.path)
        return web.Response(text='#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:3.0,\n++hls_media?&cameraNum=9&auth=secret123&index=1\n',
                            content_type='application/x-mpegurl')

    async def segment(request):
        received.append(request.path)
        return web.Response(body=b'\x47' * 1880, content_type='video/mp2t')

    upstream = web.Application()
    upstream.router.add_get('/++hls_mediaplaylist', hls)
    upstream.router.add_get('/++hls_media', segment)
    async with TestServer(upstream) as us:
        facility = Facility('dogbeach', 'DogBeach - Ponte Vedra Beach', (10088, 10089, 3782))
        app = create_app(Settings((facility,), 300), 'http://idogcam-bridge:8080')

        async def meta(id_):
            names = {10088: ('Sandcastle Play Field', 9), 10089: ('Golf Play Field', 12), 3782: ('Pool', None)}
            name, num = names[id_]
            return ViewerMetadata(id_, f'DogBeach - Ponte Vedra Beach - {name}', name, num,
                                  'Pool groups are daily from 11am-1PM.' if id_ == 3782 else None)

        async def src(id_):
            return None if id_ == 3782 else str(us.make_url('/++hls_mediaplaylist?cameraNum=9&auth=secret123'))

        app[BRIDGE_KEY].metadata = meta
        app[BRIDGE_KEY].upstream_for = src
        async with TestClient(TestServer(app)) as client:
            m = await client.get('/channels.m3u')
            assert m.status == 200
            text = await m.text()
            assert 'tvg-id="10088"' in text and 'tvg-chno="10088"' in text
            assert 'Golf Play Field' in text and 'Pool' in text
            assert 'group-title="DogBeach - Ponte Vedra Beach"' in text
            assert 'secret123' not in text

            manifest = await client.get('/hls/10088/index.m3u8')
            assert manifest.status == 200
            data = await manifest.text()
            assert 'secret123' not in data
            assert re.search(r'/media/10088/[\w-]+\.ts', data)
            path = re.search(r'(/media/10088/[\w-]+\.ts)', data).group(1)
            video = await client.get(path)
            assert video.status == 200
            assert video.headers['Content-Type'] == 'video/mp2t'
            assert await video.read() == b'\x47' * 1880
            assert (await client.get(path.replace('/10088/', '/10089/'))).status == 404
            assert (await client.get('/hls/3782/index.m3u8')).status == 503
            channel_info = await (await client.get('/api/channels')).json()
            assert channel_info['channels'][2]['offline_message'] == 'Pool groups are daily from 11am-1PM.'
            assert 'secret123' not in json.dumps(channel_info)
            assert received == ['/++hls_mediaplaylist', '/++hls_media']


@pytest.mark.asyncio
async def test_cross_origin_playlist_returns_502():
    async def bad(request):
        return web.Response(text='#EXTM3U\nhttps://external.example/other.ts\n', content_type='application/x-mpegurl')
    upstream = web.Application()
    upstream.router.add_get('/++hls_mediaplaylist', bad)
    async with TestServer(upstream) as us:
        app = create_app(Settings((Facility('a','A',(111,)),)), 'http://idogcam-bridge:8080')
        async def src(id_):return str(us.make_url('/++hls_mediaplaylist'))
        app[BRIDGE_KEY].upstream_for = src
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/hls/111/index.m3u8')
            assert response.status == 502


@pytest.mark.asyncio
async def test_401_refreshes_viewer_credentials_once_without_leakage():
    attempted = []
    async def playlist(request):
        key = request.query.get('auth')
        attempted.append(key)
        if key != 'new-viewer-key':
            raise web.HTTPUnauthorized()
        return web.Response(text='#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:3.0,\n'
                                 '++hls_media?cameraNum=9&auth=new-viewer-key&index=99\n',
                            content_type='application/x-mpegurl')

    upstream = web.Application()
    upstream.router.add_get('/++hls_mediaplaylist', playlist)
    async with TestServer(upstream) as us:
        app = create_app(Settings((Facility('a', 'A', (10088,)),)),
                         'http://idogcam-bridge:8080')
        from idogcam_bridge.viewer import ViewerMetadata
        state = {'credential': 'old-viewer-key', 'refresh_count': 0}

        async def metadata(id_, force_refresh=False):
            if force_refresh:
                state['credential'] = 'new-viewer-key'
                state['refresh_count'] += 1
            url = str(us.make_url('/++hls_mediaplaylist')) + '?cameraNum=9&auth=' + state['credential']
            return ViewerMetadata(10088, 'A - Camera', 'Camera', 9, None, url)

        async def upstream_for(id_):
            return (await metadata(id_)).stream_url

        app[BRIDGE_KEY].metadata = metadata
        app[BRIDGE_KEY].upstream_for = upstream_for
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/hls/10088/index.m3u8')
            assert response.status == 200
            body = await response.text()
            assert 'new-viewer-key' not in body
            assert 'old-viewer-key' not in body
            assert attempted == ['old-viewer-key', 'new-viewer-key']
            assert state['refresh_count'] == 1
