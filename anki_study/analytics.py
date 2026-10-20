"""Focus analytics. Protected timer history and sampled screen time stay distinct."""
import datetime as dt
import json
import os
import re
import sqlite3
import time
from pathlib import Path

MOSCOW = dt.timezone(dt.timedelta(hours=3))
REPAIR_REASON = 'Остановка зависшего блока при ремонте службы по запросу пользователя'


def day(timestamp):
    return dt.datetime.fromtimestamp(timestamp, MOSCOW).date().isoformat()


def intervals(end, seconds):
    """Split earned timer seconds at Moscow midnight, including live blocks."""
    cursor = end - seconds
    while cursor < end:
        local = dt.datetime.fromtimestamp(cursor, MOSCOW)
        boundary = dt.datetime.combine(local.date() + dt.timedelta(days=1), dt.time(), MOSCOW).timestamp()
        stop = min(end, boundary)
        yield local.date().isoformat(), stop - cursor
        cursor = stop


def snapshot(state, usage=None, now=None, remaining=None, overtime=None):
    now = time.time() if now is None else now
    today = day(now)
    days = {}
    records = []
    seen = set()
    for record in (state or {}).get('history', []):
        if (record.get('id') and record.get('id') in seen) or record.get('outcome') not in ('completed', 'interrupted'):
            continue
        seen.add(record.get('id'))
        item = dict(record)
        if item.get('reason') == REPAIR_REASON:
            item['outcome'] = 'repair'
        records.append(item)
    session = (state or {}).get('session')
    if session and session.get('phase') == 'focus' and session.get('id') not in seen:
        extra = max(0, overtime if overtime is not None else session.get('overtime_seconds', 0)) if session.get('awaiting_rest') else 0
        duration = session.get('focus_seconds', 4800)
        seconds = max(0, min(duration, duration - (remaining if remaining is not None else session.get('remaining', duration)))) + extra
        records.append(dict(session, seconds=seconds, overtime_seconds=extra, ended_at=now, outcome='live'))
    for item in records:
        if item['outcome'] == 'repair':
            continue  # Service repair is visible in history, not a failed study attempt.
        seconds = max(0, float(item.get('seconds', 0)))
        extra = max(0, min(seconds, float(item.get('overtime_seconds', 0))))
        end = item.get('ended_at', now)
        for key, portion in intervals(end, seconds):
            days.setdefault(key, {'seconds': 0, 'overtime_seconds': 0, 'completed': 0, 'interrupted': 0, 'attempts': 0})['seconds'] += portion
        for key, portion in intervals(end, extra):
            days[key]['overtime_seconds'] += portion
        row = days.setdefault(day(end), {'seconds': 0, 'overtime_seconds': 0, 'completed': 0, 'interrupted': 0, 'attempts': 0})
        if item['outcome'] != 'live':
            row[item['outcome']] += 1
        row['attempts'] += sum(item.get('attempts', {}).values()) + sum(item.get('app_attempts', {}).values())
    streak_date = dt.date.fromisoformat(today)
    if not days.get(today, {}).get('completed'):
        streak_date -= dt.timedelta(days=1)
    streak = 0
    while days.get(streak_date.isoformat(), {}).get('completed', 0):
        streak += 1
        streak_date -= dt.timedelta(days=1)
    return {'today': today, 'days': days, 'streak': streak, 'records': [
        {'id': item.get('id'), 'day': day(item.get('ended_at', now)),
         'ended_at': item.get('ended_at', now), 'seconds': item.get('seconds', 0),
         'outcome': item['outcome'], 'overtime_seconds': item.get('overtime_seconds', 0),
         'title': item.get('task', {}).get('title', 'Учебный блок'),
         'tasks': [task.get('title', '') for task in item.get('task', {}).get('items', [item.get('task', {})])],
         'attempts': sum(item.get('attempts', {}).values()) + sum(item.get('app_attempts', {}).values())}
        for item in records], 'usage': usage or {}}


class UsageTracker:
    """Aggregate foreground time and optional sanitized metadata, locally."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=.1)
        os.chmod(path, 0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT, app TEXT, name TEXT, seconds REAL, PRIMARY KEY(day,app))')
        self.db.execute('CREATE TABLE IF NOT EXISTS usage_details (day TEXT, app TEXT, kind TEXT, context_id TEXT, group_name TEXT, name TEXT, seconds REAL, PRIMARY KEY(day,app,kind,context_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS blocks (id TEXT PRIMARY KEY, record TEXT)')
        self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('since', str(time.time())))
        self.db.commit()
        self.previous = None
        self.context_previous = None
        self.pending = {}
        self.pending_details = {}
        self.last_flush = time.monotonic()
        self.blocks = {key: json.loads(record) for key, record in self.db.execute('SELECT * FROM blocks')}

    def remember(self, records):
        """Keep long-term stats after the service rotates its last 1,000 blocks."""
        changed = []
        for record in records:
            key = record.get('id')
            if not key or key in self.blocks:
                continue
            task = record.get('task', {})
            clean = {k: record[k] for k in ('id', 'seconds', 'focus_seconds', 'preset', 'overtime_seconds', 'ended_at', 'outcome', 'attempts', 'app_attempts', 'reason') if k in record}
            clean['task'] = {'title': task.get('title', 'Учебный блок')}
            if task.get('items'):
                clean['task']['items'] = [{'title': item.get('title', '')} for item in task['items']]
            self.blocks[key] = clean
            changed.append((key, json.dumps(clean, ensure_ascii=False)))
        if changed:
            with self.db:
                self.db.executemany('INSERT OR IGNORE INTO blocks VALUES (?,?)', changed)

    def sample(self, state, app, now, mono, fresh=True, detail=None, defer_detail=False):
        if not defer_detail:
            self.sample_detail(state, app, now, mono, fresh=fresh, detail=detail)
        session = (state or {}).get('session') or {}
        current = None
        if fresh and session.get('phase') == 'focus' and app and (session.get('remaining', 0) > 0 or session.get('awaiting_rest')):
            current = (session['id'], app[0], app[1], now, mono)
        prev = self.previous
        self.previous = current
        if not current or not prev or current[:2] != prev[:2]:
            return
        elapsed = mono - prev[4]
        if not 0 < elapsed <= 10 or abs((now - prev[3]) - elapsed) > 2:
            return  # Sleep, stalled UI, clock changes and unknown gaps earn nothing.
        for key, seconds in intervals(now, elapsed):
            bucket = self.pending.setdefault((key, app[0]), [app[1], 0])
            bucket[1] += seconds
        if mono - self.last_flush >= 30:
            self.flush()

    def sample_detail(self, state, app, now, mono, fresh=True, detail=None):
        """Metadata may finish asynchronously; app totals never wait for it."""
        session = (state or {}).get('session') or {}
        prev = self.context_previous
        if prev and mono <= prev[4]:
            return  # An older worker cannot rewrite the newest observation.
        valid = (isinstance(detail, dict) and detail.get('kind') in ('site', 'window', 'private') and
                 isinstance(detail.get('key'), str) and re.fullmatch(r'[a-f0-9]{32}', detail['key']) and
                 all(isinstance(detail.get(key), str) and 1 <= len(detail[key]) <= 180 for key in ('group', 'name')))
        current = None
        if fresh and valid and app and session.get('phase') == 'focus' and (session.get('remaining', 0) > 0 or session.get('awaiting_rest')):
            current = (session['id'], app[0], app[1], now, mono, detail)
        self.context_previous = current
        if not current or not prev or current[:2] != prev[:2] or (detail['kind'], detail['key']) != (prev[5]['kind'], prev[5]['key']):
            return
        elapsed = mono - prev[4]
        if not 0 < elapsed <= 10 or abs((now - prev[3]) - elapsed) > 2:
            return
        for key, seconds in intervals(now, elapsed):
            bucket = self.pending_details.setdefault((key, app[0], detail['kind'], detail['key']),
                                                      [detail['group'], detail['name'], 0])
            bucket[:2] = [detail['group'], detail['name']]
            bucket[2] += seconds

    def flush(self):
        if self.pending:
            with self.db:
                self.db.executemany('INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(day,app) DO UPDATE SET seconds=seconds+excluded.seconds,name=excluded.name',
                                    [(key, app, name, seconds) for (key, app), (name, seconds) in self.pending.items()])
            self.pending.clear()
        if self.pending_details:
            with self.db:
                self.db.executemany('INSERT INTO usage_details VALUES (?,?,?,?,?,?,?) ON CONFLICT(day,app,kind,context_id) DO UPDATE SET seconds=seconds+excluded.seconds,name=excluded.name,group_name=excluded.group_name',
                                    [(*key, group, name, seconds) for key, (group, name, seconds) in self.pending_details.items()])
            self.pending_details.clear()
        self.last_flush = time.monotonic()

    def summary(self):
        result = {}
        for key, app, name, seconds in self.db.execute('SELECT * FROM usage'):
            result.setdefault(key, {})[app] = {'name': name, 'seconds': seconds}
        for (key, app), (name, seconds) in self.pending.items():
            bucket = result.setdefault(key, {}).setdefault(app, {'name': name, 'seconds': 0})
            bucket['seconds'] += seconds
        for key, app, kind, identity, group, name, seconds in self.db.execute('SELECT * FROM usage_details'):
            if app in result.get(key, {}):
                result[key][app].setdefault('details', {})[kind + ':' + identity] = {'kind': kind, 'group': group, 'name': name, 'seconds': seconds}
        for (key, app, kind, identity), (group, name, seconds) in self.pending_details.items():
            bucket = result[key][app].setdefault('details', {}).setdefault(kind + ':' + identity,
                     {'kind': kind, 'group': group, 'name': name, 'seconds': 0})
            bucket.update(group=group, name=name, seconds=bucket['seconds'] + seconds)
        return {'since': float(self.db.execute('SELECT value FROM meta WHERE key=?', ('since',)).fetchone()[0]), 'days': result}

    def close(self):
        self.flush()
        self.db.close()


class MacForeground:
    def __init__(self):
        import ctypes as c
        self.c = c
        self.objc = c.CDLL('/usr/lib/libobjc.A.dylib')
        self.appkit = c.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.cg = c.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self.objc.objc_getClass.argtypes = [c.c_char_p]
        self.objc.objc_getClass.restype = c.c_void_p
        self.objc.sel_registerName.argtypes = [c.c_char_p]
        self.objc.sel_registerName.restype = c.c_void_p
        self.send = c.CFUNCTYPE(c.c_void_p, c.c_void_p, c.c_void_p)(c.cast(self.objc.objc_msgSend, c.c_void_p).value)
        self.cg.CGMainDisplayID.restype = c.c_uint32
        self.cg.CGDisplayIsAsleep.argtypes = [c.c_uint32]
        self.cg.CGDisplayIsAsleep.restype = c.c_bool

    def call(self, obj, selector):
        return self.send(obj, self.objc.sel_registerName(selector.encode())) if obj else None

    def text(self, obj):
        raw = self.call(obj, 'UTF8String')
        return self.c.cast(raw, self.c.c_char_p).value.decode('utf-8') if raw else ''

    def current(self):
        if self.cg.CGDisplayIsAsleep(self.cg.CGMainDisplayID()):
            return None
        workspace = self.call(self.objc.objc_getClass(b'NSWorkspace'), 'sharedWorkspace')
        app = self.call(workspace, 'frontmostApplication')
        bundle = self.text(self.call(app, 'bundleIdentifier'))
        if not bundle or bundle in ('com.apple.loginwindow', 'com.apple.ScreenSaver.Engine', 'com.apple.SecurityAgent'):
            return None
        name = 'Visual Studio Code' if bundle == 'com.microsoft.VSCode' else self.text(self.call(app, 'localizedName'))
        return bundle, name or bundle

    def pid(self):
        workspace = self.call(self.objc.objc_getClass(b'NSWorkspace'), 'sharedWorkspace')
        app = self.call(workspace, 'frontmostApplication')
        send = self.c.CFUNCTYPE(self.c.c_int32, self.c.c_void_p, self.c.c_void_p)(self.c.cast(self.objc.objc_msgSend, self.c.c_void_p).value)
        return send(app, self.objc.sel_registerName(b'processIdentifier')) if app else 0
