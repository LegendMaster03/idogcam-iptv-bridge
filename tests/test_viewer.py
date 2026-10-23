import pytest
from idogcam_bridge.viewer import parse_viewer


def test_online_camera_number_is_not_viewer_id():
    page = '''<html><title>DogBeach - Ponte Vedra Beach - Golf Play Field</title>
    <script>let foo = "cameraNum=12&quality=2";</script></html>'''
    result = parse_viewer(10089, page, 'DogBeach - Ponte Vedra Beach')
    assert result.viewer_id == 10089
    assert result.camera_num == 12
    assert result.name == 'Golf Play Field'
    assert result.offline_message is None


def test_offline_public_message_is_not_assumed_to_be_schedule():
    page = '''<html><head><title>DogBeach - Ponte Vedra Beach - Pool</title></head>
    <body><p>Pool groups are daily from 11am-1PM.</p>
    <script>function cameraCmd(cameraNum, command) {}</script></body></html>'''
    result = parse_viewer(3782, page, 'DogBeach - Ponte Vedra Beach')
    assert result.camera_num is None
    assert result.offline_message == 'Pool groups are daily from 11am-1PM.'


def test_conflicting_numbers_rejected():
    with pytest.raises(ValueError, match='Conflicting'):
        parse_viewer(4, '<title>ABC</title><script>cameraNum=9; cameraNum=10;</script>', 'ABC')


def test_online_html_supplies_credentials_without_seed():
    page = '''<html><title>DogBeach - Ponte Vedra Beach - Golf Play Field</title>
    <script>var stream='https://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?&cameraNum=12&quality=2&auth=TEST+AUTH/==';</script></html>'''
    result = parse_viewer(10089, page, 'DogBeach - Ponte Vedra Beach')
    assert result.camera_num == 12
    assert result.stream_url == ('https://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?'
                                 'cameraNum=12&quality=2&auth=TEST%2BAUTH%2F%3D%3D')
    assert 'TEST+AUTH' not in repr(result)


def test_page_embedded_credentials_with_configured_host():
    page = '''<html><title>DogBeach - Ponte Vedra Beach - Sandcastle Play Field</title>
    <script>let cameraNum=9; let v="&quality=2&auth=capture_not_needed";</script></html>'''
    info = parse_viewer(10088, page, 'DogBeach - Ponte Vedra Beach', 'https://dogbeach.viewidogcam.com:10000')
    assert info.stream_url == ('https://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?'
                               'cameraNum=9&quality=2&auth=capture_not_needed')


def test_offline_viewer_retains_message_without_stream_even_if_auth_embedded():
    page = '''<html><title>DogBeach - Ponte Vedra Beach - Pool</title>
    <p>Pool groups are daily from 11am-1PM.</p>
    <script>var x='quality=2&auth=published_auth';</script></html>'''
    result = parse_viewer(3782, page, 'DogBeach - Ponte Vedra Beach',
                          'https://dogbeach.viewidogcam.com:10000')
    assert result.stream_url is None
    assert result.offline_message == 'Pool groups are daily from 11am-1PM.'


def test_ambiguous_credential_or_stream_host_refused():
    page = '''<html><title>Facility - Example</title><script>
    var stream='https://dogbeach.viewidogcam.com:10000/++hls_mediaplaylist?cameraNum=9&quality=2&auth=one';
    var other='https://elsewhere.viewidogcam.com:10000/++hls_mediaplaylist?cameraNum=9&quality=2&auth=two';
    </script></html>'''
    result = parse_viewer(123, page, 'Facility')
    assert result.stream_url is None


def test_do_not_assume_camera_number_is_viewer_id_or_guess_missing_quality():
    page = '''<html><title>Facility - Camera</title>
    <script>let cameraNum=12; let url='https://dogbeach.viewidogcam.com:10000/&auth=abc';</script></html>'''
    result = parse_viewer(10089, page, 'Facility')
    assert result.stream_url is None
