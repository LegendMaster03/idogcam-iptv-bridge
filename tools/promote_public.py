#!/usr/bin/env python3
"""Publish pending cameras only after verifying they are public to all Jellyfin users."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from idogcam_bridge.config import load_settings

def promote(config_path: Path, facility_key: str, ids: tuple[int, ...]) -> None:
    settings = load_settings(config_path)
    facility = next((f for f in settings.facilities if f.key == facility_key), None)
    if facility is None or not ids or len(set(ids)) != len(ids) or any(type(v) is not int or v <= 0 for v in ids):
        raise ValueError('Invalid selection')
    if not set(ids).issubset(set(facility.pending_viewer_ids)):
        raise ValueError('Cameras must be staged before publication')
    data = json.loads(config_path.read_text(encoding='utf-8'))
    item = next(row for row in data['facilities'] if row['key'] == facility_key)
    item['viewer_ids'].extend(ids)
    item['pending_viewer_ids'] = [v for v in item.get('pending_viewer_ids', []) if v not in ids]
    fd, tmp = tempfile.mkstemp(prefix='.idogcam-config-', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, indent=2)
            output.write('\n')
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
    cli.add_argument('--confirm-public', action='store_true')
    args = cli.parse_args()
    if not args.confirm_public:
        cli.error('Explicit --confirm-public is required; all Jellyfin users can view published channels')
    try:
        promote(args.config, args.facility, tuple(args.ids))
    except (OSError, ValueError) as exc:
        print('Publication refused:', type(exc).__name__, file=sys.stderr)
        return 1
    print('Published reviewed public viewers; restart the bridge.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
