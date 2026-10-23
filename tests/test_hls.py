import re
import pytest
from idogcam_bridge.hls import InvalidUpstreamURL, TokenMap, rewrite_playlist

SOURCE = 'https://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?&cameraNum=9&quality=2&auth=credential'


def test_rewrites_actual_har_pattern_without_leaking_credential():
    manifest = '''#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:154
#EXTINF:3.083333,
++hls_media?&cameraNum=9&quality=2&auth=credential&index=154
#EXTINF:3.081667,
++hls_media?&cameraNum=9&quality=2&auth=credential&index=155
'''
    tokens = TokenMap()
    result = rewrite_playlist(manifest, SOURCE, 10088, tokens)
    assert '#EXTM3U' in result
    assert 'index=154' not in result
    assert 'credential' not in result
    assert len(re.findall(r'/media/10088/[\w-]+\.ts', result)) == 2
    token = next(iter(tokens._items))
    assert tokens.get(token, 10088).upstream_url.endswith('index=154')
    assert tokens.get(token, 10089) is None


def test_rejects_cross_origin_segment():
    with pytest.raises(InvalidUpstreamURL):
        rewrite_playlist('#EXTM3U\nhttps://another.example/private.ts\n', SOURCE, 10088, TokenMap())


def test_variant_playlist_rewritten_as_m3u8():
    tokens = TokenMap()
    result = rewrite_playlist('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1200\nsub/variant\n', SOURCE, 10088, tokens)
    assert re.search(r'/media/10088/[\w-]+\.m3u8', result)
    assert next(iter(tokens._items.values())).kind == 'playlist'


def test_non_hls_rejected():
    with pytest.raises(ValueError):
        rewrite_playlist('<html>unauthorized</html>', SOURCE, 10088, TokenMap())
