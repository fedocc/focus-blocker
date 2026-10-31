"""Whole-site weekly series access, enforced by the system daemon, no extension."""
import copy
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from focus_series_schedule import window_at, LABEL as SCHEDULE_LABEL

BASE = Path('/Library/PrivilegedHelperTools/focus-open')
ENABLED = BASE / 'series-schedule-enabled'
# Kept as read-only legacy metadata. Scheduling never resets episode progress.
STATE = BASE / 'series-state.json'
CATALOG_ID = 'breaking-bad'
DOMAINS = ('mirabreakingbad.com', 'www.mirabreakingbad.com', 'plplayer.online',
           'kalarona.org', 'www.kalarona.org')
SITE_URL = 'https://www.mirabreakingbad.com/'
PREFIX = '# TEMP_FOCUS_UNLOCK '


def is_surface(url):
    try:
        host = (urlparse(url).hostname or '').lower().rstrip('.')
        return any(host == domain or host.endswith('.' + domain) for domain in DOMAINS)
    except ValueError:
        return False


def public_status(value, now, studying=False):
    window = window_at(now)
    active = window['open'] and not studying
    return {'active': active, 'can_start': active, 'schedule_open': window['open'],
            'studying': studying, 'schedule': SCHEDULE_LABEL, 'site_url': SITE_URL,
            'next_at': window['start'], 'window_start': window['start'],
            'window_end': window['end'], 'server_time': now,
            'remaining': max(0, window['end'] - now) if active else max(0, window['start'] - now)}


def allowed_url(url, value, now, studying=False):
    return is_surface(url) and public_status(None, now, studying)['active']


def transition(value, request, now, studying=False):
    if not isinstance(request, dict) or set(request) != {'op'} or request['op'] not in ('status', 'start'):
        raise ValueError('Сериал открывается целиком по расписанию. Расширение больше не используется.')
    status = public_status(None, now, studying)
    if request['op'] == 'start' and not status['active']:
        raise RuntimeError('Идёт учебный блок.' if studying else 'Сайт доступен только: ' + SCHEDULE_LABEL)
    return copy.deepcopy(value), status


def handle(request, studying=False):
    import time
    if os.geteuid() != 0 or not ENABLED.is_file():
        raise RuntimeError('Расписание сериала ещё не установлено.')
    # No lease, watch counters, resets or extension heartbeats are used.
    return transition(None, request, time.time(), studying)[1]


def hosts_text(text, now, studying=False):
    if not ENABLED.exists():
        return text
    start, end = '# === FOCUS_BLOCK:SERIES START ===', '# === FOCUS_BLOCK:SERIES END ==='
    lines = text.splitlines(keepends=True)
    if sum(line.strip() == start for line in lines) != 1 or sum(line.strip() == end for line in lines) != 1:
        raise RuntimeError('Нет защищённого сетевого блока сериала.')
    a = next(i for i, line in enumerate(lines) if line.strip() == start)
    b = next(i for i, line in enumerate(lines) if line.strip() == end)
    if b <= a:
        raise RuntimeError('Повреждён сетевой блок сериала.')
    active = public_status(None, now, studying)['active']
    for i in range(a + 1, b):
        stripped = lines[i].lstrip()
        if active and stripped.startswith(('127.0.0.1 ', '0.0.0.0 ', '::1 ')):
            lines[i] = PREFIX + stripped
        elif not active and stripped.startswith(PREFIX):
            lines[i] = stripped[len(PREFIX):]
    return ''.join(lines)


def denied_targets(report, studying=False):
    import time
    if not ENABLED.exists() or public_status(None, time.time(), studying)['active']:
        return []
    from focus_youtube_guard import targets_for
    return targets_for(report, is_surface)


def publish_preview_permission(now, studying=False):
    window = window_at(now)
    value = {'allowed_until': min(window['end'], now + 15) if window['open'] and not studying else 0}
    fd, name = tempfile.mkstemp(prefix='series-preview.', dir=str(BASE))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o444)
        os.replace(name, BASE / 'series-preview-permit.json')
    finally:
        Path(name).unlink(missing_ok=True)
