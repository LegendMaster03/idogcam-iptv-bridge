#!/usr/bin/env python3
"""Stage a NEW iDogCam Gingr V2 facility from explicitly supplied official links.

Dry-run by default. This does not crawl camera IDs, authenticate customer
accounts, or publish cameras to the shared Jellyfin tuner.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import Facility, load_settings, validate_stream_origin
from idogcam_bridge.discovery import (
    MAX_LISTING_BYTES, MAX_IMPORT_CANDIDATES, extract_viewer_ids,
    validate_official_listing_url,
)
from import_listing import facility_candidates, fetch_page


LISTING_BASE = 'https://idogcam.com/idcviewcams.php'


def listed_viewer_links(official_urls: list[str]) -> str:
    """Treat only explicitly supplied official Gingr V2 viewer URLs as links.

    No guessed IDs, tokens or customer login query arguments are accepted.
    """
    if not 0 < len(official_urls) <= MAX_IMPORT_CANDIDATES:
        raise ValueError('Supply one to fifty official viewer links')
    snippets = []
    for url in official_urls:
        if len(url) > 512:
            raise ValueError('Viewer link is too long')
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {'id'} or len(query['id']) != 1:
            raise ValueError('Viewer link must contain only one viewer ID')
        snippet = '<a href="' + html.escape(url, quote=True) + '">Camera</a>'
        result = extract_viewer_ids(snippet, listing_url=LISTING_BASE)
        if len(result) != 1:
            raise ValueError('Viewer link must be an official iDogCam Gingr V2 URL')
        snippets.append(snippet)
    return '\n'.join(snippets)


def read_listing(*, listing_file: Path | None = None, listing_url: str | None = None,
                 viewer_urls: list[str] | None = None) -> tuple[str, str]:
    if viewer_urls:
        return listed_viewer_links(viewer_urls), LISTING_BASE
    if listing_url:
        validated = validate_official_listing_url(listing_url)
        return fetch_page(validated), validated
    if listing_file:
        if listing_file.stat().st_size > MAX_LISTING_BYTES:
            raise ValueError('Saved listing exceeds size limit')
        return listing_file.read_text(encoding='utf-8'), LISTING_BASE
    raise ValueError('Supply an official listing or explicit viewer links')


def stage_new_facility(config_path: Path, key: str, name: str,
                       time_zone: str, candidates: list[tuple[int, str]],
                       stream_origin: str | None = None) -> None:
    """Atomically add a pending-only facility; never automatically publish."""
    settings = load_settings(config_path)
    if any(f.key == key for f in settings.facilities):
        raise ValueError('Facility key already exists; use import_listing.py instead')
    camera_ids = [cam for cam, _ in candidates]
    if not camera_ids or len(camera_ids) != len(set(camera_ids)):
        raise ValueError('No unique verified camera IDs to stage')
    if any(cam in {*f.viewer_ids, *f.pending_viewer_ids}
           for f in settings.facilities for cam in camera_ids):
        raise ValueError('One or more viewer IDs already belong to another facility')

    new_facility = {
        'key': key,
        'name': name,
        'adapter': 'gingr_v2',
        'viewer_ids': [],
        'pending_viewer_ids': camera_ids,
        'time_zone': time_zone,
    }
    if stream_origin:
        new_facility['stream_origin'] = validate_stream_origin(stream_origin)
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    raw['facilities'].append(new_facility)

    fd, temporary = tempfile.mkstemp(prefix='.idogcam-onboard-', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(raw, f, indent=2)
            f.write('\n')
        os.chmod(temporary, stat.S_IMODE(config_path.stat().st_mode))
        load_settings(temporary)  # Reject invalid names, keys, time zones, IDs.
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--key', required=True, help='New facility slug (e.g., example-pet-care)')
    parser.add_argument('--name', required=True, help='Exact facility prefix in official viewer titles')
    parser.add_argument('--time-zone', required=True, help='IANA zone (e.g., America/New_York)')
    parser.add_argument('--stream-origin', help='Optional HTTPS *.viewidogcam.com origin without credentials')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--listing-url', help='Public official iDogCam PHP listing page')
    choice.add_argument('--listing-file', type=Path, help='Authorized saved listing HTML')
    choice.add_argument('--viewer-url', action='append', help='Exact official viewer link; repeat per camera')
    parser.add_argument('--apply', action='store_true', help='Stage cameras in config.json without publishing')
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        if any(f.key == args.key for f in settings.facilities):
            raise ValueError('Facility key already exists; use import_listing.py')
        # Validate metadata before making remote calls.
        proposed = Facility(args.key, args.name, (), stream_origin=args.stream_origin)
        if args.stream_origin:
            validate_stream_origin(args.stream_origin)
        listing, listing_url = read_listing(
            listing_file=args.listing_file, listing_url=args.listing_url,
            viewer_urls=args.viewer_url)
        candidates = facility_candidates(listing, proposed, listing_url=listing_url)
        if not candidates:
            raise ValueError('No linked Gingr V2 viewers matched the exact facility title')
        existing = {cam for f in settings.facilities for cam in (*f.viewer_ids, *f.pending_viewer_ids)}
        if any(cam in existing for cam, _ in candidates):
            raise ValueError('Some viewer IDs are already registered with another facility')
        print(f'Facility: {args.name} ({args.key})')
        print(f'Verified, unpublished camera IDs: {len(candidates)}')
        for viewer_id, name in candidates:
            print(f'  {viewer_id}: {name}')
        print('No cameras will be visible in Jellyfin until explicitly promoted.')
        if args.apply:
            stage_new_facility(args.config, args.key, args.name, args.time_zone,
                               candidates, args.stream_origin)
            print('Staged pending-only facility. Existing Jellyfin channels were not modified.')
        else:
            print('Dry run only. Add --apply to stage this new facility.')
        return 0
    except (OSError, HTTPError, URLError, UnicodeError, ValueError, KeyError,
            json.JSONDecodeError) as exc:
        print(f'Onboarding refused: {type(exc).__name__}: {str(exc)[:150]}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
