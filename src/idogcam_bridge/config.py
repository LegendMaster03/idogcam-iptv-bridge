"""Validated bridge configuration. Only nonsecret facility information is stored."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Facility:
    key: str
    name: str
    viewer_ids: tuple[int, ...]
    stream_origin: str | None = None
    pending_viewer_ids: tuple[int, ...] = ()
    time_zone: str | None = None


@dataclass(frozen=True)
class Settings:
    facilities: tuple[Facility, ...]
    metadata_ttl_seconds: int = 300

    @property
    def cameras(self) -> dict[int, Facility]:
        return {viewer_id: facility for facility in self.facilities for viewer_id in facility.viewer_ids}


def _is_idogcam_stream_host(host: str | None) -> bool:
    return bool(host) and host.endswith('.viewidogcam.com') and host != '.viewidogcam.com'


def validate_stream_origin(origin: str) -> str:
    parsed = urlsplit(origin.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Invalid stream port') from exc
    if (parsed.scheme != 'https' or not _is_idogcam_stream_host(parsed.hostname)
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or port == 0):
        raise ValueError('Stream origin must be an HTTPS *.viewidogcam.com origin without a path or credentials')
    return origin.strip().rstrip('/')


def validate_manifest_url(url: str) -> str:
    url = url.strip()
    parsed = urlsplit(url)
    validate_stream_origin(f'{parsed.scheme}://{parsed.netloc}')
    if (parsed.fragment or not re.search(r'(?:^|&)cameraNum=\d+(?:&|$)', parsed.query)
            or not parsed.path.endswith('/++hls_mediaplaylist')):
        raise ValueError('Only Gingr V2 ++hls_mediaplaylist sources with numeric cameraNum are supported')
    return url


def load_settings(path: str | Path) -> Settings:
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if raw.get('schema_version') != 2:
        raise ValueError('Unsupported schema_version (expected 2; captured manifest URL secrets are no longer used)')
    facilities = []
    keys: set[str] = set()
    ids: set[int] = set()
    for item in raw['facilities']:
        if item.get('adapter') != 'gingr_v2':
            raise ValueError('Only the verified gingr_v2 adapter is currently supported')
        key = item['key']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', key) or key in keys:
            raise ValueError('Facility key must be a unique lowercase slug')
        keys.add(key)
        name = item['name'].strip()
        if not name or len(name) > 120 or any(c in name for c in '\r\n"'):
            raise ValueError('Invalid facility name')
        viewer_ids = tuple(item['viewer_ids'])
        pending_ids = tuple(item.get('pending_viewer_ids', []))
        if not viewer_ids or len(viewer_ids) != len(set(viewer_ids)):
            raise ValueError('Facility must have unique, nonempty published camera IDs')
        if len(pending_ids) != len(set(pending_ids)):
            raise ValueError('Facility pending camera IDs must be unique')
        if set(viewer_ids) & set(pending_ids):
            raise ValueError('Camera can not be both published and pending')
        for viewer_id in (*viewer_ids, *pending_ids):
            if type(viewer_id) is not int or viewer_id <= 0 or viewer_id in ids:
                raise ValueError('Camera IDs must be unique positive integers across facilities')
            ids.add(viewer_id)
        origin = validate_stream_origin(item['stream_origin']) if item.get('stream_origin') else None
        timezone = item.get('time_zone')
        if timezone is not None:
            if not isinstance(timezone, str) or not timezone or len(timezone) > 80:
                raise ValueError('Invalid facility time zone')
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
                raise ValueError('Unknown facility time zone') from exc
        facilities.append(Facility(key, name, viewer_ids, origin, pending_ids, timezone))
    if not facilities:
        raise ValueError('At least one facility is required')
    ttl = raw.get('metadata_ttl_seconds', 300)
    if type(ttl) is not int or not 30 <= ttl <= 3600:
        raise ValueError('metadata_ttl_seconds must be 30..3600')
    return Settings(tuple(facilities), ttl)
