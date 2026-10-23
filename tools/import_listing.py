#!/usr/bin/env python3
"""Import only officially linked, verified viewers; dry-run by default.

Usage: python3 tools/import_listing.py --facility KEY --listing-file saved.html
       python3 tools/import_listing.py --facility KEY --listing-url https://www.idogcam.com/...php

A customer-portal listing requiring login must first be supplied through an
explicitly authorized, locally saved HTML page. Do not save cookies/HAR tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import load_settings
from idogcam_bridge.discovery import (
    MAX_LISTING_BYTES, extract_viewer_ids, validate_official_listing_url,
)
from idogcam_bridge.viewer import parse_viewer


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def fetch_page(url: str) -> str:
    request = Request(url, headers={'User-Agent': 'iDogCam-IPTV-Bridge/0.2'})
    with OPENER.open(request, timeout=15) as response:
        data = response.read(MAX_LISTING_BYTES + 1)
    if len(data) > MAX_LISTING_BYTES:
        raise ValueError('Official page exceeds size limit')
    return data.decode('utf-8', 'replace')


def facility_candidates(page: str, facility, *, listing_url: str) -> list[tuple[int, str]]:
    """Only include candidates independently verified by their official titles."""
    result: list[tuple[int, str]] = []
    for camera_id in extract_viewer_ids(page, listing_url=listing_url):
        viewer_page = fetch_page(f'https://idogcam.com/idogcamgingrviewv2.php?id={camera_id}')
        meta = parse_viewer(camera_id, viewer_page, facility.name, facility.stream_origin)
        if meta.full_title.startswith(facility.name + ' - '):
            result.append((camera_id, meta.name))
    return result


def apply_import(config_path: Path, facility_key: str, ids: list[int]) -> None:
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    selected = next(row for row in raw['facilities'] if row['key'] == facility_key)
    pending = list(selected.get('pending_viewer_ids', []))
    existing = {int(cam) for item in raw['facilities'] for cam in (*item['viewer_ids'], *item.get('pending_viewer_ids', []))}
    selected['pending_viewer_ids'] = pending + [cam for cam in ids if cam not in existing]
    fd, temp_path = tempfile.mkstemp(prefix='.idogcam-config-', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(raw, handle, indent=2)
            handle.write('\n')
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, config_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=Path('config.json'))
    cli.add_argument('--facility', required=True, help='Existing facility key')
    group = cli.add_mutually_exclusive_group(required=True)
    group.add_argument('--listing-file', type=Path, help='Authorized saved HTML listing')
    group.add_argument('--listing-url', help='Public iDogCam PHP listing URL')
    cli.add_argument('--apply', action='store_true', help='Write newly verified IDs to config.json')
    args = cli.parse_args()
    settings = load_settings(args.config)
    facility = next((f for f in settings.facilities if f.key == args.facility), None)
    if facility is None:
        cli.error('Unknown facility key')
    try:
        if args.listing_url:
            listing_url = validate_official_listing_url(args.listing_url)
            page = fetch_page(listing_url)
        else:
            if args.listing_file.stat().st_size > MAX_LISTING_BYTES:
                raise ValueError('Saved listing exceeds size limit')
            page = args.listing_file.read_text(encoding='utf-8')
            listing_url = 'https://idogcam.com/idcviewcams.php'
        candidates = facility_candidates(page, facility, listing_url=listing_url)
        already = {v for f in settings.facilities for v in (*f.viewer_ids, *f.pending_viewer_ids)}
        added = [(cam, name) for cam, name in candidates if cam not in already]
        print(f'Officially linked viewers for {facility.name}: {len(candidates)}')
        print(f'New verified viewers: {len(added)}')
        for camera_id, name in added:
            print(f'  {camera_id}: {name}')
        if args.apply and added:
            apply_import(args.config, facility.key, [cam for cam, _ in added])
            print('Staged discovered viewers in pending_viewer_ids. They are NOT published to Jellyfin.')
        else:
            print('No configuration changes made.' if not args.apply else 'No new viewers to import.')
        return 0
    except (OSError, URLError, HTTPError, ValueError, StopIteration) as exc:
        print(f'Import failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
