#!/usr/bin/env python3
"""Read-only viewer/auth discovery probe. Never prints tokenized upstream URLs."""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

# Allow the probe to run from the source tree, without installing the bridge.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import load_settings
from idogcam_bridge.viewer import parse_viewer


def request(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=12) as result:
            return result.status, result.read(1_000_001)
    except urllib.error.HTTPError as exc:
        return exc.code, b''


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else 'config.json')
    settings = load_settings(path)
    for viewer_id, facility in settings.cameras.items():
        print(f'\n=== Viewer ID {viewer_id} ===')
        viewer_url = f'https://idogcam.com/idogcamgingrviewv2.php?id={viewer_id}'
        try:
            code, contents = request(viewer_url)
            if code != 200 or len(contents) > 1_000_000:
                print(f'Viewer: HTTP {code} or oversized HTML')
                continue
            meta = parse_viewer(viewer_id, contents.decode('utf-8', 'replace'),
                                facility.name, facility.stream_origin)
            print('Official name:', meta.name)
            print('cameraNum:', meta.camera_num if meta.camera_num is not None else 'offline/unresolved')
            print('Offline message:', meta.offline_message or '(none)')
            print('Viewer-provided stream resolved:', bool(meta.stream_url))
            if meta.stream_url is None:
                continue
            code, playlist = request(meta.stream_url)
            print('Upstream playlist HTTP:', code)
            print('Valid HLS:', playlist.startswith(b'#EXTM3U'))
        except (OSError, ValueError, UnicodeError) as exc:
            # Do not report exception details: they could include a tokenized URL.
            print('Probe failed:', type(exc).__name__)


if __name__ == '__main__':
    main()
