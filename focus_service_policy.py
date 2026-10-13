"""Root-owned limits for temporary social access; user state is display-only."""
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

BASE = Path('/Library/PrivilegedHelperTools/focus-open')
USAGE = BASE / 'service-usage.json'
DURATIONS = {'instagram': 120, 'x': 120, 'telegram': 420}
SERVICE_NAMES = {'instagram': 'Instagram', 'x': 'X', 'telegram': 'Telegram'}
X_HOSTS = ('x.com', 'www.x.com', 'm.x.com', 'mobile.x.com', 'api.x.com',
           'twitter.com', 'www.twitter.com', 'm.twitter.com', 'mobile.twitter.com',
           'api.twitter.com', 'tweetdeck.x.com', 'tweetdeck.twitter.com',
           'platform.twitter.com', 'syndication.twitter.com', 'twimg.com',
           'pbs.twimg.com', 'video.twimg.com', 'abs.twimg.com', 'ton.twimg.com',
           'cdn.syndication.twimg.com')
TIMEZONE = ZoneInfo('Europe/Moscow')


def read_usage():
    value = json.loads(USAGE.read_text())
    if value.get('schema_version') != 1 or not isinstance(value.get('services'), dict):
        raise RuntimeError('Protected service limits unavailable')
    return value


def day_at(now):
    return dt.datetime.fromtimestamp(now, TIMEZONE).date().isoformat()


def validate(service, seconds, usage, now):
    if service not in DURATIONS or isinstance(seconds, bool) or not isinstance(seconds, int) or seconds != DURATIONS[service]:
        raise ValueError('Защищённый лимит: Instagram и X 2 минуты, Telegram 7 минут.')
    previous = usage['services'].get(service, {})
    last_day = previous.get('last_day')
    if not last_day and previous.get('last_started_at') is not None:
        last_day = day_at(float(previous['last_started_at']))
    if last_day and day_at(now) <= last_day:
        raise RuntimeError(SERVICE_NAMES[service] + ' уже открывался сегодня; повторный доступ после полуночи по Москве.')


def reserve(service, usage, now):
    value = json.loads(json.dumps(usage))
    value['services'][service] = {'last_started_at': now, 'last_day': day_at(now)}
    return value


def migrate_client_state(state):
    result = {'schema_version': 1, 'services': {}}
    for name in DURATIONS:
        dates = []
        value = state.get('services', {}).get(name, {}).get('last_opened_at')
        if value:
            dates.append(value)
        for entry in state.get('history', []):
            if entry.get('service') == name and entry.get('access_granted', True) and entry.get('opened_at'):
                dates.append(entry['opened_at'])
        parsed = []
        for value in dates:
            try:
                date = dt.datetime.fromisoformat(value)
                if date.tzinfo is None:
                    raise ValueError('Missing timezone in legacy service use')
                parsed.append(date.timestamp())
            except (ValueError, TypeError):
                raise RuntimeError('Cannot safely migrate existing service limits')
        if parsed:
            last = max(parsed)
            result['services'][name] = {'last_started_at': last, 'last_day': day_at(last)}
    return result


def service_for_url(value):
    host = (urlparse(value).hostname or '').lower().rstrip('.')
    if any(host == d or host.endswith('.' + d) for d in ('instagram.com', 'cdninstagram.com', 'ig.me')):
        return 'instagram'
    if any(host == d or host.endswith('.' + d) for d in ('x.com', 'twitter.com', 'twimg.com')):
        return 'x'
    if any(host == d or host.endswith('.' + d) for d in ('web.telegram.org', 't.me', 'telegram.me')):
        return 'telegram'
    return None
