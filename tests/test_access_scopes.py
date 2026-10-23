"""Camera access scope annotations do not grant upstream permissions."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from idogcam_bridge.config import load_settings
from idogcam_bridge.server import BRIDGE_KEY, create_app
from idogcam_bridge.viewer import ViewerMetadata

ROOT = Path(__file__).resolve().parents[1]


def config_file(tmp_path, scopes=None, pending=None, published=None):
    path = tmp_path / 'config.json'
    facility = {
        'key': 'dogbeach', 'name': 'DogBeach', 'adapter': 'gingr_v2',
        'viewer_ids': [10088] if published is None else published,
        'pending_viewer_ids': [10089, 3782] if pending is None else pending,
    }
    if scopes is not None:
        facility['access_scopes'] = scopes
    path.write_text(json.dumps({'schema_version': 2, 'facilities': [facility]}))
    return path


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_customer_classification_is_metadata_only(tmp_path):
    path = config_file(tmp_path, {'10088': 'shared_customer', '10089': 'private'})
    facility = load_settings(path).facilities[0]
    assert facility.access_scope(10088) == 'shared_customer'
    assert facility.access_scope(10089) == 'private'
    assert 10089 not in load_settings(path).cameras


def test_legacy_config_not_implicitly_public(tmp_path):
    facility = load_settings(config_file(tmp_path)).facilities[0]
    assert facility.access_scope(10088) == 'unreviewed'


@pytest.mark.parametrize('mapping', [
    {'10088': 'private'},
    {'10088': 'not-a-scope'},
    {'10088': None},
    {'99999': 'public'},
    {'10088': 'Public'},
    {'010088': 'public'},
    ['10088'],
])
def test_refuse_invalid_or_private_published_scopes(tmp_path, mapping):
    with pytest.raises(ValueError):
        load_settings(config_file(tmp_path, mapping))


def test_newly_promoted_public_camera_has_public_scope(tmp_path):
    path = config_file(tmp_path)
    load_tool('promote_public').promote(path, 'dogbeach', (10089,))
    fac = load_settings(path).facilities[0]
    assert fac.access_scope(10089) == 'public'
    assert fac.pending_viewer_ids == (3782,)


def test_promote_pending_shared_customer_camera(tmp_path):
    path = config_file(tmp_path)
    load_tool('promote_public').promote(path, 'dogbeach', (10089,), scope='shared_customer')
    fac = load_settings(path).facilities[0]
    assert fac.access_scope(10089) == 'shared_customer'
    assert fac.viewer_ids == (10088, 10089)
    assert fac.pending_viewer_ids == (3782,)


def test_private_pending_camera_rejected_from_tuner(tmp_path):
    path = config_file(tmp_path, {'10089': 'private'})
    with pytest.raises(ValueError, match='Private'):
        load_tool('promote_public').promote(path, 'dogbeach', (10089,), scope='shared_customer')
    assert load_settings(path).facilities[0].pending_viewer_ids == (10089, 3782)


def test_classify_existing_does_not_change_published_channel_list(tmp_path):
    path = config_file(tmp_path, pending=[10089])
    load_tool('classify_access').classify(path, 'dogbeach', (10088,), 'shared_customer')
    settings = load_settings(path)
    assert settings.facilities[0].access_scope(10088) == 'shared_customer'
    assert tuple(settings.cameras) == (10088,)
    assert settings.facilities[0].pending_viewer_ids == (10089,)


def test_can_not_classify_pending_as_published(tmp_path):
    path = config_file(tmp_path)
    with pytest.raises(ValueError, match='existing published'):
        load_tool('classify_access').classify(path, 'dogbeach', (10089,), 'public')


def test_can_not_classify_private_for_shared_tuner(tmp_path):
    path = config_file(tmp_path)
    with pytest.raises(ValueError):
        load_tool('classify_access').classify(path, 'dogbeach', (10088,), 'private')


@pytest.mark.parametrize('tool, args', [
    ('promote_public', ['--ids', '10089', '--scope', 'shared_customer']),
    ('classify_access', ['--ids', '10088', '--scope', 'shared_customer']),
])
def test_household_shared_requires_explicit_cli_acknowledgement(tmp_path, tool, args):
    path = config_file(tmp_path)
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / f'{tool}.py'),
                            '--config', str(path), '--facility', 'dogbeach', *args],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert '--confirm-shared-household' in result.stderr
    assert load_settings(path).facilities[0].access_scope(10088) == 'unreviewed'


@pytest.mark.asyncio
async def test_status_api_scopes_are_nonsecret_and_pending_hidden(tmp_path):
    settings = load_settings(config_file(tmp_path, {'10088': 'shared_customer', '10089': 'private'}))
    app = create_app(settings)

    async def metadata(id_):
        assert id_ == 10088
        return ViewerMetadata(id_, 'DogBeach - Sandcastle', 'Sandcastle', None, None)

    app[BRIDGE_KEY].metadata = metadata
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/channels')
        info = (await response.json())['channels']
        assert len(info) == 1
        assert info[0]['access_scope'] == 'shared_customer'
        assert 'auth' not in str(info)
        playlist = await (await client.get('/channels.m3u')).text()
        assert '10088' in playlist
        assert '10089' not in playlist


@pytest.mark.parametrize('tool, action', [
    ('classify_access', 'classify'),
    ('promote_public', 'promote'),
])
def test_config_update_preserves_existing_read_permissions(tmp_path, tool, action):
    import stat
    path = config_file(tmp_path)
    path.chmod(0o644)
    module = load_tool(tool)
    if action == 'classify':
        module.classify(path, 'dogbeach', (10088,), 'shared_customer')
    else:
        module.promote(path, 'dogbeach', (10089,), scope='shared_customer')
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
