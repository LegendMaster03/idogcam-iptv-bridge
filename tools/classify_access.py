#!/usr/bin/env python3
"""Classify existing published cameras without changing the tuner channel list.

This does not authenticate with iDogCam or grant any provider permissions.
Every Jellyfin user can view the server's published channels.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import load_settings


def classify(config_path: Path, facility_key: str,
             ids: tuple[int, ...], scope: str) -> None:
    if scope not in ('public', 'shared_customer'):
        raise ValueError('Private cameras can not be classified for shared publication')
    settings = load_settings(config_path)
    facility = next((f for f in settings.facilities if f.key == facility_key), None)
    if facility is None or not ids or len(set(ids)) != len(ids):
        raise ValueError('Invalid facility or viewer ID selection')
    if any(type(value) is not int or value not in facility.viewer_ids for value in ids):
        raise ValueError('Can only classify existing published cameras')
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    item = next(f for f in raw['facilities'] if f['key'] == facility_key)
    item.setdefault('access_scopes', {}).update({str(value): scope for value in ids})
    fd, tmp = tempfile.mkstemp(prefix='.idogcam-config-', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(raw, out, indent=2)
            out.write('\n')
        os.chmod(tmp, 0o600)
        load_settings(tmp)
        os.replace(tmp, config_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, default=Path('config.json'))
    cli.add_argument('--facility', required=True)
    cli.add_argument('--ids', type=int, nargs='+', required=True)
    cli.add_argument('--scope', choices=('public', 'shared_customer'), required=True)
    cli.add_argument('--confirm-public', action='store_true')
    cli.add_argument('--confirm-shared-household', action='store_true')
    args = cli.parse_args()
    if args.scope == 'public' and not args.confirm_public:
        cli.error('Public classification requires --confirm-public')
    if args.scope == 'shared_customer' and not args.confirm_shared_household:
        cli.error('All Jellyfin users must be authorized: --confirm-shared-household')
    try:
        classify(args.config, args.facility, tuple(args.ids), args.scope)
    except (ValueError, OSError) as exc:
        print('Classification refused:', type(exc).__name__, file=sys.stderr)
        return 1
    print('Access classification saved. Restart the bridge to reload metadata.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
