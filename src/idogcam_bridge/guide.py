"""Conservative XMLTV schedules derived from official viewer *messages*.

The notices are not a structured provider schedule. A recognized daily window is
only a *published-hours estimate*, never proof of live availability.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time as clock_time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from .config import Facility
from .viewer import ViewerMetadata

# Match straightforward display messages; never interpret arbitrary numbers as times.
_CLOCK = r'(?:0?[1-9]|1[0-2])(?::[0-5][0-9])?\s*(?:a\.?m\.?|p\.?m\.?)'
_RANGE = re.compile(rf'(?<!\w)({_CLOCK})\s*(?:-|–|—|to)\s*({_CLOCK})(?!\w)', re.I)
_CLOCK_PARSE = re.compile(r'^(0?[1-9]|1[0-2])(?::([0-5][0-9]))?\s*(a\.?m\.?|p\.?m\.?)$', re.I)
_GUIDE_DAYS = 8
_NOTICE_TTL = 14 * 24 * 3600


class PublishedNoticeCache:
    """Remember official viewer notices, never camera URLs or credentials.

    Notices expire after two weeks without reconfirmation to avoid indefinitely
    presenting outdated operating hours.
    """

    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else None
        self.entries: dict[str, dict] = {}
        if self.path and self.path.is_file():
            try:
                if self.path.stat().st_size < 256_000:
                    raw = json.loads(self.path.read_text(encoding='utf-8'))
                    if isinstance(raw, dict):
                        self.entries = raw
            except (OSError, ValueError, TypeError):
                self.entries = {}

    def get(self, viewer_id: int) -> str | None:
        record = self.entries.get(str(viewer_id))
        if not isinstance(record, dict):
            return None
        notice = record.get('notice')
        timestamp = record.get('retrieved_at')
        if (not isinstance(notice, str) or not notice or len(notice) > 500 or
                not isinstance(timestamp, (int, float)) or
                not 0 <= clock_time.time() - timestamp < _NOTICE_TTL):
            return None
        return notice

    def remember(self, viewer_id: int, notice: str) -> None:
        if not notice or len(notice) > 500:
            return
        old = self.entries.get(str(viewer_id))
        if (isinstance(old, dict) and old.get('notice') == notice and
                isinstance(old.get('retrieved_at'), (int, float)) and
                clock_time.time() - old['retrieved_at'] < 24 * 3600):
            return
        self.entries[str(viewer_id)] = {'notice': notice, 'retrieved_at': clock_time.time()}
        if not self.path:
            return
        fd, temporary = tempfile.mkstemp(prefix='.notice-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(self.entries, output, ensure_ascii=True)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


@dataclass(frozen=True)
class PublishedHours:
    windows: tuple[tuple[int, int], ...]
    recurring_explicitly: bool


def _minute(value: str) -> int:
    match = _CLOCK_PARSE.fullmatch(value.strip())
    if not match:
        raise ValueError('Unrecognized clock format')
    hour = int(match[1]) % 12 + (12 if match[3].lower().startswith('p') else 0)
    return hour * 60 + int(match[2] or '0')


def parse_published_hours(message: str | None) -> PublishedHours | None:
    """Accept a simple daily/open interval with an optional contained break.

    Reject ambiguous, reversed, overnight, or multi-window notices rather than
    invent a timetable. The word 'daily' is tracked separately from an implicit
    repetition of an undated generic access notice.
    """
    if not message:
        return None
    normalized = ' '.join(message.split())
    if not re.search(r'\b(?:camera|cam|pool|groups|hours|access|open|online)\b', normalized, re.I):
        return None
    matches = list(_RANGE.finditer(normalized))
    if len(matches) not in (1, 2):
        return None
    windows = [(_minute(m[1]), _minute(m[2])) for m in matches]
    if any(start >= end for start, end in windows):
        return None
    open_start, open_end = windows[0]
    if len(matches) == 2:
        # The second range must explicitly describe an actual break.
        if not re.search(r'\bbreak\b', normalized[matches[0].end():matches[1].start()], re.I):
            return None
        break_start, break_end = windows[1]
        if not (open_start < break_start < break_end < open_end):
            return None
        available = ((open_start, break_start), (break_end, open_end))
    else:
        available = ((open_start, open_end),)
    return PublishedHours(available, bool(re.search(r'\bdaily\b|\bevery day\b', normalized, re.I)))



def published_schedule_status(notice: str | None, time_zone: str | None,
                              now: datetime | None = None) -> dict | None:
    """Describe published hours, not verified online status.

    `now` must be timezone-aware. Never infer a facility time zone.
    Missing or ambiguous notices cannot establish a viewing schedule.
    """
    published = parse_published_hours(notice)
    if published is None or not time_zone:
        return None
    current = now if now is not None else datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError('now must have timezone information')
    zone = ZoneInfo(time_zone)
    local = current.astimezone(zone)
    minute_of_day = local.hour * 60 + local.minute + local.second / 60
    today = local.date()
    inside = any(a <= minute_of_day < b for a, b in published.windows)
    next_start = None
    window_end = None
    for day_offset in range(3):
        day = today + timedelta(days=day_offset)
        for begin, end in published.windows:
            starts = datetime.combine(day, time(begin // 60, begin % 60), tzinfo=zone)
            ends = datetime.combine(day, time(end // 60, end % 60), tzinfo=zone)
            if day_offset == 0 and begin <= minute_of_day < end:
                window_end = ends.isoformat()
            if starts > local and next_start is None:
                next_start = starts.isoformat()
    return {
        'basis': 'official_viewer_notice',
        'time_zone': time_zone,
        'daily_recurrence_explicit': published.recurring_explicitly,
        'within_published_window': inside,
        'next_window_start': next_start,
        'current_window_end': window_end,
        'windows_local': [
            {'start': f'{begin // 60:02d}:{begin % 60:02d}',
             'end': f'{end // 60:02d}:{end % 60:02d}'}
            for begin, end in published.windows
        ],
    }

def _stamp(day, minutes: int, zone: ZoneInfo) -> str:
    if minutes == 1440:
        day = day + timedelta(days=1)
        minutes = 0
    local = datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=zone)
    return local.strftime('%Y%m%d%H%M%S %z')


def _programme(parent, channel_id: str, day, start: int, end: int,
               zone: ZoneInfo, title: str, desc: str) -> None:
    entry = ET.SubElement(parent, 'programme', {
        'channel': channel_id,
        'start': _stamp(day, start, zone),
        'stop': _stamp(day, end, zone),
    })
    ET.SubElement(entry, 'title', {'lang': 'en'}).text = title
    ET.SubElement(entry, 'desc', {'lang': 'en'}).text = desc


def make_xmltv(channels: list[tuple[int, Facility, ViewerMetadata | None]],
               now: datetime | None = None) -> bytes:
    """Build a short rolling guide. Only published cameras are passed in.

    Missing/uncertain hours produce an informational day block. Facility time
    zones are required for accurate windows; never infer a zone from host time.
    """
    current = now if now is not None else datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError('now must have timezone information')
    root = ET.Element('tv', {'generator-info-name': 'iDogCam IPTV Bridge'})
    for viewer_id, facility, metadata in channels:
        channel_id = str(viewer_id)
        name = metadata.name if metadata else f'Camera {viewer_id}'
        element = ET.SubElement(root, 'channel', {'id': channel_id})
        ET.SubElement(element, 'display-name').text = name

        notice = metadata.offline_message if metadata else None
        published = parse_published_hours(notice)
        zone = ZoneInfo(facility.time_zone) if facility.time_zone else None
        # UTC for timezone-free information placeholders only; do not assign
        # local operating windows to UTC when a facility zone is unknown.
        chosen_zone = zone or ZoneInfo('UTC')
        day0 = current.astimezone(chosen_zone).date()
        description = (f'Official viewer notice: {notice}' if notice else
                       'The official viewer has not published a usable operating-hours notice.')
        # EPG slots represent *possible viewing*, not programmes or confirmed
        # online state. Do not fabricate all-day offline programmes: Jellyfin
        # otherwise makes a closed camera look like a show that is airing.
        if published and zone:
            caution = ('The daily recurrence is explicitly stated.' if published.recurring_explicitly
                       else 'Daily recurrence is inferred from an undated viewer notice.')
            for delta in range(_GUIDE_DAYS):
                day = day0 + timedelta(days=delta)
                for start, end in published.windows:
                    # The guide's visible title identifies what the viewer can watch.
                    # The description, not the title, explains the schedule.
                    _programme(root, channel_id, day, start, end, chosen_zone,
                               f'{facility.name} — {name}',
                               description + ' ' + caution +
                               ' This window does not verify that the camera is online.')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
