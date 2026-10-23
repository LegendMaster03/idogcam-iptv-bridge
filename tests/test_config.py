import json
import pytest
from idogcam_bridge.config import load_settings, validate_manifest_url, validate_stream_origin


def test_config_uses_nonsecret_stream_origin(tmp_path):
    conf = tmp_path / 'config.json'
    conf.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'dogbeach', 'adapter': 'gingr_v2', 'name': 'DogBeach - Ponte Vedra Beach',
         'viewer_ids': [10088, 10089, 3782], 'stream_origin': 'https://dogbeach.viewidogcam.com:10000'}]}))
    settings = load_settings(conf)
    assert len(settings.cameras) == 3
    assert settings.facilities[0].stream_origin == 'https://dogbeach.viewidogcam.com:10000'


@pytest.mark.parametrize('url', [
    'http://dogbeach.viewidogcam.com:10000',
    'https://localhost:10000',
    'https://dogbeach.viewidogcam.com.evil.test',
    'https://username:password@dogbeach.viewidogcam.com:10000',
    'https://dogbeach.viewidogcam.com/++hls_mediaplaylist?cameraNum=9',
    'https://dogbeach.viewidogcam.com:999999',
])
def test_rejects_invalid_stream_origin(url):
    with pytest.raises(ValueError):
        validate_stream_origin(url)


@pytest.mark.parametrize('url', [
    'http://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?cameraNum=9',
    'https://localhost:10000/++hls_mediaplaylist?cameraNum=9',
    'https://dogbeach.viewidogcam.com.evil.test/++hls_mediaplaylist?cameraNum=9',
    'https://dogbeach.viewidogcam.com/++hls_mediaplaylist?cameraNum=evil',
    'https://dogbeach.viewidogcam.com/anything?cameraNum=9',
])
def test_rejects_nonverified_source(url):
    with pytest.raises(ValueError):
        validate_manifest_url(url)


def test_old_secret_based_config_rejected(tmp_path):
    conf = tmp_path / 'config.json'
    conf.write_text(json.dumps({'schema_version': 1, 'facilities': [
        {'key':'a', 'adapter':'gingr_v2','name':'A','viewer_ids':[10088],
         'manifest_url_file':'/run/secrets/a'}]}))
    with pytest.raises(ValueError, match='schema_version'):
        load_settings(conf)


def test_duplicate_ids_rejected(tmp_path):
    source = tmp_path / 'config.json'
    source.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key':'a', 'adapter':'gingr_v2','name':'A','viewer_ids':[10088,10088]}]}))
    with pytest.raises(ValueError):
        load_settings(source)

@pytest.mark.parametrize('pending', [[10088], [10089,10089], [-1], ['x'], [True]])
def test_rejects_invalid_pending(tmp_path, pending):
    file = tmp_path / 'config.json'
    file.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'a', 'adapter': 'gingr_v2', 'name': 'A',
         'viewer_ids': [10088], 'pending_viewer_ids': pending}]}))
    with pytest.raises(ValueError):
        load_settings(file)
