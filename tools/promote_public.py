#!/usr/bin/env python3
"""Publish reviewed public or household-shared cameras in the shared Jellyfin tuner.

Never use for private, boarding-room, customer-specific or individually
restricted cameras. Such cameras require a separate per-user authorization
mechanism that the standard Jellyfin M3U tuner lacks.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import load_settings


def promote(config_path: Path, facility_key: str, ids: tuple[int, ...],
            scope: str = 'public') -> None:
    if scope not in ('public', 'shared_customer'):
        raise ValueError('Only public or reviewed shared customer cameras may be published')
    settings = load_settings(config_path)
    facility = next((f for f in settings.facilities if f.key == facility_key), None)
    if facility is None:
        raise ValueError('Unknown facility')
    if not ids or len(ids) != len(set(ids)) or any(type(v) is not int or v <= 0 for v in ids):
        raise ValueError('Provide unique positive viewer IDs')
    if not set(ids).issubset(facility.pending_viewer_ids):
        raise ValueError('Every selected ID must already be pending for this facility')
    if any(facility.access_scope(camera_id) == 'private' for camera_id in ids):
        raise ValueError('Private cameras can not be published on a shared tuner')

    raw = json.loads(config_path.read_text(encoding='utf-8'))
    target = next(f for f in raw['facilities'] if f['key'] == facility_key)
    target['viewer_ids'].extend(ids)
    target['pending_viewer_ids'] = [v for v in target.get('pending_viewer_ids', []) if v not in ids]
    target.setdefault('access_scopes', {}).update({str(v): scope for v in ids})
    fd, path = tempfile.mkstemp(prefix='.idogcam-config-', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(raw, output, indent=2)
            output.write('\n')
        os.chmod(path, stat.S_IMODE(config_path.stat().st_mode))
        load_settings(path)
        os.replace(path, config_path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--facility', required=True)
    parser.add_argument('--ids', type=int, nargs='+', required=True)
    parser.add_argument('--scope', choices=('public', 'shared_customer'), default='public')
    parser.add_argument('--confirm-public', action='store_true',
                        help='Confirm that each camera is public')
    parser.add_argument('--confirm-shared-household', action='store_true',
                        help='Confirm all Jellyfin users can view these customer-shared cameras')
    args = parser.parse_args()
    if args.scope == 'public' and not args.confirm_public:
        parser.error('Public publication requires --confirm-public')
    if args.scope == 'shared_customer' and not args.confirm_shared_household:
        parser.error('Shared customer publication requires --confirm-shared-household')
    try:
        promote(args.config, args.facility, tuple(args.ids), scope=args.scope)
    except (ValueError, OSError) as exc:
        print('Promotion refused:', type(exc).__name__, file=sys.stderr)
        return 1
    print(f'Published {args.scope} cameras. Restart the bridge to load the channels.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
