"""New facility onboarding must not leak private or unreviewed cameras to Jellyfin."""
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from idogcam_bridge.config import load_settings
from idogcam_bridge.server import create_app
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]


def tool():
    spec = importlib.util.spec_from_file_location('onboard_facility',
                                                    ROOT / 'tools' / 'onboard_facility.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'dogbeach', 'name': 'DogBeach', 'adapter': 'gingr_v2',
         'viewer_ids': [10088], 'time_zone': 'America/New_York'}]}))
    return path


@pytest.mark.parametrize('bad_link', [
    'https://evil.com/idogcamgingrviewv2.php?id=111',
    'http://idogcam.com/idogcamgingrviewv2.php?id=111',
    'https://idogcam.com/idogcamgingrviewv2.php?id=111&auth=secret',
    'https://idogcam.com/idogcamgingrviewv2.php?id=111&id=222',
    'https://idogcam.com/idogcamgingrviewv2.php?id=wrong',
    'https://idogcam.com/other.php?id=111',
])
def test_explicit_url_requires_only_official_viewer(bad_link):
    with pytest.raises(ValueError):
        tool().listed_viewer_links([bad_link])


def test_valid_official_links_are_preserved_without_numeric_scanning():
    page = tool().listed_viewer_links([
        'https://idogcam.com/idogcamgingrviewv2.php?id=4567',
        'https://www.idogcam.com/idogcamgingrviewv2.php?id=7890',
    ])
    assert 'id=4567' in page
    assert 'id=7890' in page
    assert '10088' not in page


def test_staging_other_facility_does_not_publish(tmp_path):
    path = configuration(tmp_path)
    tool().stage_new_facility(path, 'example-pet-care', 'Example Pet Care',
                              'America/Chicago', [(4567, 'Play Room')])
    settings = load_settings(path)
    assert len(settings.facilities) == 2
    assert tuple(settings.cameras) == (10088,)
    assert settings.facilities[1].viewer_ids == ()
    assert settings.facilities[1].pending_viewer_ids == (4567,)
    assert settings.facilities[1].access_scope(4567) == 'unreviewed'


@pytest.mark.asyncio
async def test_pending_only_facility_does_not_create_m3u_or_guide_entries(tmp_path):
    path = configuration(tmp_path)
    tool().stage_new_facility(path, 'example', 'Example', 'America/New_York',
                              [(4567, 'Play Room')])
    app = create_app(load_settings(path))

    async def metadata(cam):
        from idogcam_bridge.viewer import ViewerMetadata
        assert cam == 10088
        return ViewerMetadata(cam, 'DogBeach - Play Room', 'Play Room', None, None)

    from idogcam_bridge.server import BRIDGE_KEY
    app[BRIDGE_KEY].metadata = metadata
    async with TestClient(TestServer(app)) as client:
        m3u = await (await client.get('/channels.m3u')).text()
        xmltv = await (await client.get('/xmltv.xml')).text()
        assert '10088' in m3u and '4567' not in m3u
        assert '10088' in xmltv and '4567' not in xmltv


def test_pending_only_facility_is_valid_but_empty_facility_is_not(tmp_path):
    path = configuration(tmp_path)
    raw = json.loads(path.read_text())
    raw['facilities'].append({'key': 'new', 'name': 'New', 'adapter': 'gingr_v2',
                              'viewer_ids': [], 'pending_viewer_ids': [4567]})
    path.write_text(json.dumps(raw))
    assert load_settings(path).facilities[-1].pending_viewer_ids == (4567,)
    raw['facilities'][-1]['pending_viewer_ids'] = []
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize('name,zone', [
    ('', 'America/New_York'), ('Example', 'Invalid/Zone'),
    ('Example\nInjected', 'America/New_York'),
])
def test_invalid_new_facility_fails_atomically(tmp_path, name, zone):
    path = configuration(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        tool().stage_new_facility(path, 'example', name, zone, [(4567, 'Cam')])
    assert path.read_bytes() == original


def test_duplicate_camera_or_facility_rejected_without_modifying_config(tmp_path):
    path = configuration(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        tool().stage_new_facility(path, 'other', 'Other', 'America/New_York',
                                  [(10088, 'Wrong')])
    with pytest.raises(ValueError):
        tool().stage_new_facility(path, 'dogbeach', 'Other', 'America/New_York',
                                  [(4567, 'Wrong')])
    assert path.read_bytes() == original


def test_new_facility_preserves_existing_file_mode(tmp_path):
    path = configuration(tmp_path)
    path.chmod(0o644)
    tool().stage_new_facility(path, 'other', 'Other', 'America/New_York',
                              [(4567, 'Group Room')])
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_cli_does_not_modify_without_apply(tmp_path, monkeypatch, capsys):
    path = configuration(tmp_path)
    original = path.read_bytes()
    module = tool()
    monkeypatch.setattr(module, 'facility_candidates',
                        lambda page, facility, listing_url: [(4567, 'Group Room')])
    monkeypatch.setattr(sys, 'argv', ['onboard_facility.py', '--config', str(path),
                                      '--key', 'other', '--name', 'Other',
                                      '--time-zone', 'America/New_York',
                                      '--viewer-url',
                                      'https://idogcam.com/idogcamgingrviewv2.php?id=4567'])
    assert module.main() == 0
    assert 'Dry run only' in capsys.readouterr().out
    assert path.read_bytes() == original


def test_promotion_after_facility_onboarding_explicit_only(tmp_path):
    from importlib.util import spec_from_file_location, module_from_spec
    path = configuration(tmp_path)
    tool().stage_new_facility(path, 'other', 'Other', 'America/New_York',
                              [(4567, 'Group Room')])
    spec = spec_from_file_location('promote_public', ROOT / 'tools' / 'promote_public.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.promote(path, 'other', (4567,), scope='public')
    assert 4567 in load_settings(path).cameras
    assert load_settings(path).facilities[1].access_scope(4567) == 'public'
