"""Only listed and independently verified viewers may be imported."""
import json
from pathlib import Path

import pytest

from idogcam_bridge.config import Facility
from idogcam_bridge.discovery import extract_viewer_ids, validate_official_listing_url


LISTING = '''
<a href="https://idogcam.com/idogcamgingrviewv2.php?id=10088">Sandcastle</a>
<a href="/idogcamgingrviewv2.php?id=10089&amp;foo=bar">Golf</a>
<a href="https://www.idogcam.com/idogcamgingrviewv2.php?id=3782">Pool</a>
<a href="https://evil.com/idogcamgingrviewv2.php?id=1234">Invalid</a>
<a href="https://idogcam.com/secret.php?id=1235">Invalid path</a>
<script>var hidden = 'idogcamgingrviewv2.php?id=98765'</script>
<div data-viewer-id="3456">Unlisted</div>
<a href="javascript:alert(1001)">Invalid</a>
<a href="https://idogcam.com:bad/idogcamgingrviewv2.php?id=10012">Invalid port</a>
<a href="/idogcamgingrviewv2.php?id=3782">Duplicate</a>
'''


def test_extract_only_visible_official_links():
    assert extract_viewer_ids(LISTING, listing_url='https://www.idogcam.com/idcviewcams.php') == (3782, 10088, 10089)


@pytest.mark.parametrize('url', [
    'http://www.idogcam.com/idcviewcams.php',
    'https://idogcam.com.evil.com/idcviewcams.php',
    'https://127.0.0.1/idcviewcams.php',
    'https://username:secret@idogcam.com/idcviewcams.php',
    'https://idogcam.com:9999/idcviewcams.php',
    'https://idogcam.com/idcviewcams.php#hidden',
])
def test_rejects_unofficial_listing_urls(url):
    with pytest.raises(ValueError):
        validate_official_listing_url(url)


def test_rejects_large_enumerated_list():
    html = ''.join(f'<a href="/idogcamgingrviewv2.php?id={i}">x</a>' for i in range(1, 70))
    with pytest.raises(ValueError, match='limit'):
        extract_viewer_ids(html, listing_url='https://idogcam.com/idcviewcams.php')


def test_unlinked_ids_not_auto_discovered():
    html = '<script>const cameraNum = 9; const viewer = 10089</script>'
    assert extract_viewer_ids(html, listing_url='https://idogcam.com/idcviewcams.php') == ()


def test_import_preserves_existing_and_other_facilities(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('import_listing', Path(__file__).resolve().parents[1] / 'tools' / 'import_listing.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key':'dogbeach', 'adapter':'gingr_v2', 'name':'DogBeach - Ponte Vedra Beach', 'viewer_ids':[10088]},
        {'key':'elsewhere', 'adapter':'gingr_v2', 'name':'Another Facility', 'viewer_ids':[9876]}
    ]}))
    tool.apply_import(config, 'dogbeach', [10089, 3782, 9876])
    updated = json.loads(config.read_text())
    assert updated['facilities'][0]['viewer_ids'] == [10088]
    assert updated['facilities'][0]['pending_viewer_ids'] == [10089, 3782]
    assert updated['facilities'][1]['viewer_ids'] == [9876]


def test_facility_metadata_verification(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('import_listing', Path(__file__).resolve().parents[1] / 'tools' / 'import_listing.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool, 'fetch_page', lambda url: '<title>DogBeach - Ponte Vedra Beach - Sandcastle Play Field</title>'
                         if '10088' in url else '<title>Unrelated Facility - Private Room</title>')
    html = ('<a href="/idogcamgingrviewv2.php?id=10088">a</a>'
            '<a href="/idogcamgingrviewv2.php?id=10089">b</a>')
    facility = Facility('dogbeach','DogBeach - Ponte Vedra Beach',(10088,))
    assert tool.facility_candidates(html, facility, listing_url='https://idogcam.com/idcviewcams.php') == [(10088,'Sandcastle Play Field')]


def test_import_stages_instead_of_exposing(tmp_path):
    import importlib.util
    from idogcam_bridge.config import load_settings
    spec = importlib.util.spec_from_file_location('import_listing', Path(__file__).resolve().parents[1] / 'tools' / 'import_listing.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'a', 'adapter': 'gingr_v2', 'name': 'A', 'viewer_ids': [10088]}]}))
    tool.apply_import(path, 'a', [10089])
    settings = load_settings(path)
    assert set(settings.cameras) == {10088}
    assert settings.facilities[0].pending_viewer_ids == (10089,)


def test_verified_iframe_link_extraction():
    source = '<iframe src="/idogcamgingrviewv2.php?id=10089"></iframe><div data-id="99999"></div>'
    assert extract_viewer_ids(source, listing_url='https://idogcam.com/idcviewcams.php') == (10089,)


def test_public_promotion_explicit(tmp_path):
    import importlib.util
    from idogcam_bridge.config import load_settings
    spec = importlib.util.spec_from_file_location('promote_public', Path(__file__).resolve().parents[1] / 'tools' / 'promote_public.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'a', 'adapter': 'gingr_v2', 'name': 'A',
         'viewer_ids': [10088], 'pending_viewer_ids': [10089, 3782]}]}))
    with pytest.raises(ValueError):
        tool.promote(path, 'a', (98765,))
    tool.promote(path, 'a', (10089,))
    settings = load_settings(path)
    assert set(settings.cameras) == {10088, 10089}
    assert settings.facilities[0].pending_viewer_ids == (3782,)


def test_listing_import_preserves_config_permissions(tmp_path):
    import importlib.util
    import stat
    spec = importlib.util.spec_from_file_location(
        'import_listing', Path(__file__).resolve().parents[1] / 'tools' / 'import_listing.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'schema_version': 2, 'facilities': [
        {'key': 'dogbeach', 'adapter': 'gingr_v2', 'name': 'DogBeach', 'viewer_ids': [10088]}]}))
    path.chmod(0o644)
    tool.apply_import(path, 'dogbeach', [10089])
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
