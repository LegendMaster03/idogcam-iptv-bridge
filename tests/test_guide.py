"""XMLTV guide tests use only synthetic viewer metadata, no live requests."""
from datetime import datetime
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

import pytest
from aiohttp.test_utils import TestClient, TestServer

from idogcam_bridge.config import Facility, Settings, load_settings
from idogcam_bridge.guide import make_xmltv, parse_published_hours, published_schedule_status
from idogcam_bridge.server import BRIDGE_KEY, create_app
from idogcam_bridge.viewer import ViewerMetadata


@pytest.mark.parametrize(('message', 'expected', 'daily'), [
    ('Camera access is from 8am - 5pm with a break from 11am - 1pm while dogs are at rest and/or at the pool.',
     ((480, 660), (780, 1020)), False),
    ('Cam Online 8am-5pm Daily', ((480, 1020),), True),
    ('Pool groups are daily from 11am-1PM.', ((660, 780),), True),
    ('Open daily from 12pm to 2:30pm', ((720, 870),), True),
    ('Camera access from 12am–12pm', ((0, 720),), False),
])
def test_known_published_hours(message, expected, daily):
    parsed = parse_published_hours(message)
    assert parsed is not None
    assert parsed.windows == expected
    assert parsed.recurring_explicitly == daily


@pytest.mark.parametrize('message', [
    None, '', 'Camera online', 'Cam online 5pm-8am Daily',
    'Cam open 8am-10am and 1pm-5pm',
    'Camera from 8am-5pm with break from 6am-7am',
    'Camera 8am-8am', 'Call 8am-5pm for help',
    'Camera 8am-5pm or 9am-6pm',
])
def test_ambiguous_hours_are_not_invented(message):
    assert parse_published_hours(message) is None


def test_xmltv_dogbeach_all_channels_and_daily_break():
    facility = Facility('dogbeach', 'DogBeach - Ponte Vedra Beach', (10088, 10089, 3782),
                        time_zone='America/New_York')
    messages = {
        10088: ('Sandcastle Play Field', 'Camera access is from 8am - 5pm with a break from 11am - 1pm.'),
        10089: ('Golf Play Field', 'Cam Online 8am-5pm Daily'),
        3782: ('Pool', 'Pool groups are daily from 11am-1PM.'),
    }
    data = [(i, facility, ViewerMetadata(i, name, name, None, notice))
            for i, (name, notice) in messages.items()]
    xml = make_xmltv(data, now=datetime(2026, 10, 24, 12, tzinfo=ZoneInfo('America/New_York')))
    root = ET.fromstring(xml)
    assert [c.attrib['id'] for c in root.findall('channel')] == ['10088', '10089', '3782']
    assert [c.findtext('display-name') for c in root.findall('channel')] == [
        'Sandcastle Play Field', 'Golf Play Field', 'Pool']
    entries = [p for p in root.findall('programme') if p.attrib['channel'] == '10088'
               and p.attrib['start'].startswith('20261024')]
    assert [(p.attrib['start'][8:12], p.attrib['stop'][8:12], p.findtext('title'))
            for p in entries] == [
        ('0800', '1100', 'DogBeach - Ponte Vedra Beach — Sandcastle Play Field'),
        ('1300', '1700', 'DogBeach - Ponte Vedra Beach — Sandcastle Play Field')]
    assert 'Outside published viewing window' not in ET.tostring(root, encoding='unicode')
    assert all(' -0400' in p.attrib['start'] for p in entries)
    assert any('Daily recurrence is inferred' in p.findtext('desc') for p in entries)
    assert all('auth=' not in ET.tostring(root, encoding='unicode') for _ in (0,))


def test_xmltv_dst_offsets_change_at_transition():
    facility = Facility('a', 'A', (10088,), time_zone='America/New_York')
    metadata = ViewerMetadata(10088, 'A - Pool', 'Pool', None, 'Pool groups are daily from 11am-1PM.')
    root = ET.fromstring(make_xmltv([(10088, facility, metadata)],
                                      now=datetime(2026, 10, 31, 14, tzinfo=ZoneInfo('America/New_York'))))
    starts = [p.attrib['start'] for p in root.findall('programme')
              if p.findtext('title') == 'A — Pool']
    assert any(s.startswith('20261031110000 -0400') for s in starts)
    assert any(s.startswith('20261101110000 -0500') for s in starts)


def test_unknown_schedule_or_timezone_produces_info_not_guessed_hours():
    metadata = ViewerMetadata(10088, 'A - pool', 'Pool', None, 'Pool groups are daily from 11am-1PM.')
    facility = Facility('a', 'A', (10088,))
    root = ET.fromstring(make_xmltv([(10088, facility, metadata)],
                                      now=datetime(2026, 10, 24, tzinfo=ZoneInfo('UTC'))))
    assert len(root.findall('channel')) == 1
    assert not root.findall('programme')


def test_xmltv_escapes_markup():
    facility = Facility('a', 'A', (101,), time_zone='UTC')
    meta = ViewerMetadata(101, 'A - Pool & Spa', 'Pool & Spa', None, 'Cam Online 8am-5pm Daily <now>')
    raw = make_xmltv([(101, facility, meta)], now=datetime(2026, 10, 24, tzinfo=ZoneInfo('UTC')))
    assert b'Pool &amp; Spa' in raw and b'&lt;now&gt;' in raw
    assert ET.fromstring(raw).find('channel').findtext('display-name') == 'Pool & Spa'


@pytest.mark.asyncio
async def test_xmltv_endpoint_matches_m3u_and_excludes_pending():
    facility = Facility('a', 'A', (10088,), pending_viewer_ids=(10089,), time_zone='America/New_York')
    app = create_app(Settings((facility,)))

    async def meta(viewer_id):
        assert viewer_id == 10088
        return ViewerMetadata(viewer_id, 'A - Sandcastle', 'Sandcastle', None,
                              'Cam Online 8am-5pm Daily')

    app[BRIDGE_KEY].metadata = meta
    async with TestClient(TestServer(app)) as client:
        res = await client.get('/xmltv.xml')
        assert res.status == 200
        assert res.headers['Content-Type'].startswith('application/xml')
        root = ET.fromstring(await res.read())
        assert [c.get('id') for c in root.findall('channel')] == ['10088']
        assert set(p.get('channel') for p in root.findall('programme')) == {'10088'}
        m3u = await (await client.get('/channels.m3u')).text()
        assert 'tvg-id="10088"' in m3u
        assert '10089' not in m3u


def test_time_zone_configuration_validated(tmp_path):
    import json
    base = {'schema_version': 2, 'facilities': [
        {'key': 'a', 'name': 'A', 'adapter': 'gingr_v2', 'viewer_ids': [10088],
         'time_zone': 'America/New_York'}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(base))
    assert load_settings(path).facilities[0].time_zone == 'America/New_York'
    base['facilities'][0]['time_zone'] = 'No/SuchZone'
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match='Unknown facility time zone'):
        load_settings(path)


def test_notice_cache_persists_without_credentials(tmp_path):
    from idogcam_bridge.guide import PublishedNoticeCache
    path = tmp_path / 'notices.json'
    cache = PublishedNoticeCache(str(path))
    assert cache.get(10088) is None
    cache.remember(10088, 'Cam Online 8am-5pm Daily')
    assert PublishedNoticeCache(str(path)).get(10088) == 'Cam Online 8am-5pm Daily'
    assert 'auth=' not in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == '0o600'


def test_notice_cache_expires_stale_public_schedule(tmp_path):
    import json
    import time
    from idogcam_bridge.guide import PublishedNoticeCache
    path = tmp_path / 'notices.json'
    path.write_text(json.dumps({'10088': {'notice': 'Cam Online 8am-5pm Daily',
                                         'retrieved_at': time.time() - 20 * 86400}}))
    assert PublishedNoticeCache(str(path)).get(10088) is None


def test_notice_cache_rejects_invalid_data(tmp_path):
    from idogcam_bridge.guide import PublishedNoticeCache
    path = tmp_path / 'notices.json'
    path.write_text('{broken')
    assert PublishedNoticeCache(str(path)).get(10088) is None


def test_sparse_schedule_has_no_fabricated_off_air_programmes():
    zone = ZoneInfo('America/New_York')
    fac = Facility('a', 'A', (10088,), time_zone='America/New_York')
    cam = ViewerMetadata(10088, 'A - Cam', 'Cam', None,
                         'Camera access is from 8am - 5pm with a break from 11am - 1pm.')
    xml = make_xmltv([(10088, fac, cam)], now=datetime(2026, 10, 24, 12, tzinfo=zone))
    root = ET.fromstring(xml)
    programmes = [p for p in root.findall('programme') if p.attrib['start'].startswith('20261024')]
    assert len(programmes) == 2
    assert all(p.findtext('title') == 'A — Cam' for p in programmes)
    assert 'Outside published viewing window' not in xml.decode()
    assert 'Schedule information unavailable' not in xml.decode()
    assert all('not verify' in p.findtext('desc') for p in programmes)


def test_published_schedule_status_covers_break_and_next_opening():
    zone = ZoneInfo('America/New_York')
    notice = 'Camera access is from 8am - 5pm with a break from 11am - 1pm.'
    status = published_schedule_status(notice, 'America/New_York',
                                       now=datetime(2026, 10, 24, 12, tzinfo=zone))
    assert status['within_published_window'] is False
    assert status['next_window_start'] == '2026-10-24T13:00:00-04:00'
    assert status['current_window_end'] is None
    assert status['daily_recurrence_explicit'] is False
    assert status['windows_local'] == [
        {'start': '08:00', 'end': '11:00'},
        {'start': '13:00', 'end': '17:00'},
    ]
    status = published_schedule_status(notice, 'America/New_York',
                                       now=datetime(2026, 10, 24, 13, 45, tzinfo=zone))
    assert status['within_published_window'] is True
    assert status['current_window_end'] == '2026-10-24T17:00:00-04:00'
    assert status['next_window_start'] == '2026-10-25T08:00:00-04:00'


def test_published_status_handles_next_day_and_dst():
    zone = ZoneInfo('America/New_York')
    notice = 'Cam Online 8am-5pm Daily'
    result = published_schedule_status(notice, 'America/New_York',
                                       now=datetime(2026, 10, 31, 19, tzinfo=zone))
    assert result['next_window_start'] == '2026-11-01T08:00:00-05:00'
    assert result['daily_recurrence_explicit'] is True


def test_published_status_unknown_is_not_online_indicator():
    assert published_schedule_status('Camera online', 'America/New_York') is None
    assert published_schedule_status('Cam Online 8am-5pm Daily', None) is None
    with pytest.raises(ValueError, match='timezone'):
        published_schedule_status('Cam Online 8am-5pm Daily', 'America/New_York',
                                  now=datetime(2026, 10, 24))


@pytest.mark.asyncio
async def test_channels_api_exposes_schedule_without_claiming_live():
    fac = Facility('a', 'A', (10088,), time_zone='America/New_York')
    app = create_app(Settings((fac,)))
    async def meta(viewer_id):
        return ViewerMetadata(viewer_id, 'A - Cam', 'Cam', None, 'Cam Online 8am-5pm Daily')
    app[BRIDGE_KEY].metadata = meta
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/channels')
        assert response.status == 200
        channel = (await response.json())['channels'][0]
        assert channel['stream_resolvable'] is False
        assert channel['published_schedule']['basis'] == 'official_viewer_notice'
        assert channel['published_schedule']['windows_local'] == [{'start':'08:00','end':'17:00'}]
        assert 'online' not in channel


def test_same_camera_name_in_different_facilities_has_distinct_programme_titles():
    zone = ZoneInfo('UTC')
    first = Facility('east', 'Eastside Pet Care', (101,), time_zone='UTC')
    second = Facility('west', 'Westside Pet Care', (202,), time_zone='UTC')
    sources = [
        (101, first, ViewerMetadata(101, 'Eastside Pet Care - Pool', 'Pool', None,
                                    'Pool groups are daily from 11am-1PM.')),
        (202, second, ViewerMetadata(202, 'Westside Pet Care - Pool', 'Pool', None,
                                     'Pool groups are daily from 11am-1PM.')),
    ]
    root = ET.fromstring(make_xmltv(sources, now=datetime(2026, 10, 24, 12, tzinfo=zone)))
    by_channel = {}
    for programme in root.findall('programme'):
        by_channel.setdefault(programme.attrib['channel'], set()).add(
            programme.findtext('title'))
    assert by_channel == {
        '101': {'Eastside Pet Care — Pool'},
        '202': {'Westside Pet Care — Pool'},
    }
    assert all('not verify that the camera is online' in programme.findtext('desc')
               for programme in root.findall('programme'))


def test_guide_title_xml_escapes_facility_and_camera_names():
    zone = ZoneInfo('UTC')
    facility = Facility('spa', 'Paws & Play', (101,), time_zone='UTC')
    camera = ViewerMetadata(101, 'Paws & Play - Pool <North>',
                            'Pool <North>', None, 'Pool groups are daily from 11am-1PM.')
    xml = make_xmltv([(101, facility, camera)], now=datetime(2026, 10, 24, 12, tzinfo=zone))
    assert b'Paws &amp; Play' in xml
    assert b'Pool &lt;North&gt;' in xml
    assert ET.fromstring(xml).find('programme').findtext('title') == 'Paws & Play — Pool <North>'
