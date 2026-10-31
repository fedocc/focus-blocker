"""Root-owned YouTube policy and browser observer; no browser extension required."""
import json
import hashlib
import stat
import ctypes
import socket
import math
import os
import pwd
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
from focus_service_policy import service_for_url, day_at
import focus_series_policy as series

BASE = Path('/Library/PrivilegedHelperTools/focus-open')
POLICY = BASE / 'youtube-policy.json'
USAGE = BASE / 'youtube-usage.json'
HEALTH = BASE / 'youtube-guard-health.json'
LAST_EVENT = BASE / 'youtube-last-event.json'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
COOLDOWN = 6 * 60 * 60
SCRIPT_TIMEOUT = 5
BROWSERS = {
    'Arc': ('company.thebrowser.Browser', 'id'),
    'Google Chrome': ('com.google.Chrome', 'id'),
    'Google Chrome Canary': ('com.google.Chrome.canary', 'id'),
    'Microsoft Edge': ('com.microsoft.edgemac', 'id'),
    'Brave Browser': ('com.brave.Browser', 'id'),
    'Dia': ('company.thebrowser.dia', 'id'),
    'Safari': ('com.apple.Safari', 'index'),
}
UNSUPPORTED = {'firefox', 'Firefox', 'Tor Browser', 'TorBrowser', 'Chromium', 'Opera', 'Vivaldi', 'Yandex', 'Yandex Browser'}


class ForbiddenNavigation(RuntimeError):
    def __init__(self, targets):
        super().__init__('Переход на запрещённое видео завершил доступ к YouTube. Вернуться: youtube --course dl')
        self.browsers = sorted({target['application'] for target in targets})
        self.targets = targets


class BrowserReport(list):
    def __init__(self, values=(), errors=None):
        super().__init__(values)
        self.errors = dict(errors or {})


class BrowserObservationError(RuntimeError):
    def __init__(self, errors, report):
        super().__init__('; '.join(name + ': ' + str(error)[:200] for name, error in errors.items()))
        self.browsers = set(errors)
        self.report = report


class BrowserScriptTimeout(RuntimeError):
    pass


class SessionClosed(RuntimeError):
    pass


def atomic_json(path, value, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        tmp.chmod(mode)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_policy():
    value = json.loads(POLICY.read_text())
    if value.get('schema_version') != 1 or not isinstance(value.get('playlists'), list):
        raise RuntimeError('Invalid YouTube policy')
    for playlist in value['playlists']:
        if not isinstance(playlist.get('id'), str) or not playlist.get('videos'):
            raise RuntimeError('Invalid approved playlist')
        for video in playlist['videos']:
            seconds = video.get('duration_seconds')
            if not VIDEO_ID.fullmatch(video.get('id', '')) or (seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 86400)):
                raise RuntimeError('Invalid approved video')
    channels = value.get('channels', [])
    if not isinstance(channels, list):
        raise RuntimeError('Invalid approved channels')
    for channel in channels:
        channel_id = channel.get('id', '')
        handle = channel.get('handle', '')
        if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id) or not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,100}', handle):
            raise RuntimeError('Invalid approved channel identity')
        uploads = 'UU' + channel_id[2:]
        if channel.get('uploads_playlist_id') != uploads or not any(p['id'] == uploads and p.get('owner_id') == channel_id and p.get('auto_refresh') is True for p in value['playlists']):
            raise RuntimeError('Approved channel requires its verified uploads playlist')
    return value


def read_usage():
    return json.loads(USAGE.read_text()) if USAGE.exists() else {'last_regular_opened_at': None, 'counts': {}}


def study_scope(policy, playlist_id=None):
    playlists = policy['playlists']
    channels = policy.get('channels', [])
    uploads = {c['uploads_playlist_id'] for c in channels}
    paths = []
    for channel in channels:
        for base in ('/channel/' + channel['id'], '/@' + channel['handle'].lower()):
            paths.extend(base + suffix for suffix in ('', '/featured', '/videos', '/streams', '/shorts', '/playlists'))
    if not playlists:
        return {'mode': 'playlist', 'allowed_ids': [], 'playlist_ids': [], 'seconds': None, 'persistent_study': True}
    return {'mode': 'playlist', 'playlist_id': playlist_id or playlists[0]['id'],
            'playlist_ids': [p['id'] for p in playlists],
            'channel_ids': [c['id'] for c in channels], 'channel_paths': paths,
            'channel_video_ids': sorted({v['id'] for p in playlists if p['id'] in uploads for v in p['videos']}),
            'allowed_ids': sorted({v['id'] for p in playlists for v in p['videos']}),
            'seconds': None, 'persistent_study': True}


def make_scope(request, policy, usage, now):
    if not isinstance(request, dict):
        raise ValueError('YouTube requires a specific video or an approved playlist')
    playlist_id = request.get('playlist_id')
    video_id = request.get('video_id')
    channel_id = request.get('channel_id')
    if channel_id is not None:
        if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError('Invalid YouTube channel ID')
        if video_id is not None or playlist_id:
            raise ValueError('Choose a channel, a playlist or a video, not a combination')
        if channel_id not in {c['id'] for c in policy.get('channels', [])}:
            raise ValueError('This channel is not approved')
        return study_scope(policy)
    if video_id is not None and not isinstance(video_id, str):
        raise ValueError('Invalid YouTube video ID')
    if video_id is not None and not VIDEO_ID.fullmatch(video_id):
        raise ValueError('Invalid YouTube video ID')
    if playlist_id and video_id:
        raise ValueError('Choose a playlist or a video, not both')
    approved = None
    for playlist in policy['playlists']:
        if playlist_id == playlist['id'] or (video_id and video_id in {v['id'] for v in playlist['videos']}):
            approved = playlist
            break
    if approved:
        return study_scope(policy, approved['id'])
    if playlist_id:
        raise ValueError('This playlist is not approved')
    if not video_id:
        raise ValueError('YouTube requires a specific video ID')
    last = usage.get('last_regular_opened_at')
    if last is not None and day_at(now) <= day_at(float(last)):
        raise RuntimeError('YouTube уже открывался сегодня; новое обычное видео доступно после полуночи по Москве.')
    if int(usage.get('counts', {}).get(video_id, 0)) >= 2:
        raise RuntimeError('Эта ссылка уже использована 2 раза. Повторный доступ закрыт.')
    seconds = request.get('seconds', 12 * 60)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= COOLDOWN:
        raise ValueError('Invalid YouTube session duration')
    study = study_scope(policy)
    return {'mode': 'video', 'allowed_ids': [video_id], 'seconds': seconds,
            'study_ids': study['allowed_ids'], 'playlist_ids': study['playlist_ids'],
            'channel_ids': study.get('channel_ids', []), 'channel_paths': study.get('channel_paths', []),
            'channel_video_ids': study.get('channel_video_ids', [])}


# Google video viewers embed YouTube without changing the top-level hostname.
# They are never a grant surface: URL fragments can be changed independently
# of an already-loaded player, so an apparent approved `vid` is not trusted.
GOOGLE_HOST = re.compile(r'^(?:[a-z0-9-]+\.)*google\.(?:[a-z]{2,3}|(?:co|com)\.[a-z]{2})$')


def is_video_preview_url(value):
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if not GOOGLE_HOST.fullmatch(host):
        return False
    params = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(unquote(parsed.fragment), keep_blank_values=True)
    if any(v in ('ive', 'v') for v in fragment.get('fpstate', [])) or 'vld' in fragment:
        return True
    if parsed.path.rstrip('/') in ('/videohp', '/video', '/videos'):
        return True
    return (parsed.path.rstrip('/') == '/search' and
            (any(v in ('vid', 'vids', '7') for v in params.get('tbm', []) + params.get('udm', []))
             or 'vld' in params or any(v in ('ive', 'v') for v in params.get('fpstate', []))))


def is_youtube_surface(value):
    return is_youtube_url(value) or is_video_preview_url(value)


def is_youtube_url(value):
    host = (urlparse(value).hostname or '').lower().rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in ('youtube.com', 'youtu.be', 'youtube-nocookie.com'))


def temporary_full_access_active(scope):
    # Only an administrator may place this window in the protected lease.
    # make_scope never copies it from an ordinary terminal request.
    window = scope.get('temporary_full_access')
    fields = ('started_at', 'deadline', 'started_monotonic', 'deadline_monotonic')
    if scope.get('mode') != 'video' or not isinstance(window, dict):
        return False
    seconds = window.get('seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= COOLDOWN:
        return False
    if any(isinstance(window.get(k), bool) or not isinstance(window.get(k), (int, float)) or
           not math.isfinite(window[k]) for k in fields):
        return False
    if (abs(window['deadline'] - window['started_at'] - seconds) > .01 or
            abs(window['deadline_monotonic'] - window['started_monotonic'] - seconds) > .01):
        return False
    now, mono = time.time(), time.clock_gettime(time.CLOCK_MONOTONIC)
    return (window['started_at'] <= now < window['deadline'] and
            window['started_monotonic'] <= mono < window['deadline_monotonic'])


def url_allowed(scope, value):
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if is_video_preview_url(value):
        return False
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port:
        return False
    if is_youtube_url(value) and temporary_full_access_active(scope):
        return True
    params = parse_qs(parsed.query)
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        path = parsed.path.rstrip('/')
        if path.startswith('/@'):
            path = path.lower()
        if path in set(scope.get('channel_paths', [])):
            return True
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path == '/playlist':
        ids = params.get('list', [])
        approved = set(scope.get('playlist_ids', []))
        if scope['mode'] == 'playlist' and scope.get('playlist_id'):
            approved.add(scope['playlist_id'])
        return len(ids) == 1 and ids[0] in approved
    if host == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path == '/watch':
        values = params.get('v', [])
        if len(set(values)) != 1 or not values:
            return False
        video_id = values[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path.startswith('/shorts/'):
        video_id = parsed.path[len('/shorts/'):].strip('/')
        return bool(VIDEO_ID.fullmatch(video_id)) and video_id in set(scope.get('channel_video_ids', []))
    elif host in ('youtube.com', 'www.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com') and parsed.path.startswith('/embed/'):
        video_id = parsed.path[len('/embed/'):].strip('/')
    else:
        return False
    return bool(VIDEO_ID.fullmatch(video_id)) and video_id in set(scope['allowed_ids']) | set(scope.get('study_ids', []))


def username():
    return json.loads((BASE / 'config.json').read_text())['username']


def browser_processes():
    uid = pwd.getpwnam(username()).pw_uid
    result = subprocess.run(['/bin/ps', '-axo', 'uid=,comm='], capture_output=True, text=True, timeout=3, check=True)
    found = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] == str(uid):
            name = Path(parts[1]).name
            if name in BROWSERS or name in UNSUPPORTED:
                found.add(name)
    return found


def user_script(script):
    owner = username()
    uid = pwd.getpwnam(owner).pw_uid
    command = ['/bin/launchctl', 'asuser', str(uid), '/usr/bin/sudo', '-n', '-u', owner,
               '/usr/bin/osascript', '-e', script]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BrowserScriptTimeout('Браузер не ответил за ' + str(SCRIPT_TIMEOUT) + ' секунд') from None
    if result.returncode:
        raise RuntimeError('Нет доступа macOS к вкладкам браузера: ' + result.stderr.strip()[:400])
    return result.stdout



# Only the dedicated native helper receives Accessibility permission. It reads
# window IDs and close controls, never page text, credentials or browser DOM.
def guard_arc_previews():
    if not (BASE / 'arc-preview-guard-required').exists():
        return
    executable = Path('/Applications/Focus Preview Guard.app/Contents/MacOS/FocusWindowGuard')
    identity_path = BASE / 'arc-peek-guard-identity.json'
    check_native_previews(executable, identity_path)
    if (BASE / 'series-schedule-enabled').exists():
        check_native_previews(Path('/Applications/Focus Series Guard.app/Contents/MacOS/FocusWindowGuard'),
                              BASE / 'series-window-guard-identity.json')


def check_native_previews(executable, identity_path):
    identity_info = identity_path.lstat()
    executable_info = executable.lstat()
    if (not stat.S_ISREG(identity_info.st_mode) or identity_info.st_uid != 0 or identity_info.st_mode & 0o022 or
            not stat.S_ISREG(executable_info.st_mode) or executable_info.st_uid != 0 or executable_info.st_mode & 0o022):
        raise RuntimeError('Focus Window Guard: нарушены права защищённого помощника')
    identity = json.loads(identity_path.read_text())
    if (not re.fullmatch(r'[a-f0-9]{40}', identity.get('cdhash', '')) or
            hashlib.sha256(executable.read_bytes()).hexdigest() != identity.get('sha256')):
        raise RuntimeError('Focus Window Guard: приложение изменено')
    owner = username()
    uid = pwd.getpwnam(owner).pw_uid
    # LaunchServices gives the signed helper its own Accessibility identity.
    # Authenticate responses using the kernel's Unix-socket peer PID and its
    # root-owned executable path; writable /tmp health files could be forged.
    with tempfile.TemporaryDirectory(prefix='focus-arc-', dir='/private/tmp') as directory:
        os.chmod(directory, 0o711)
        endpoint = str(Path(directory) / 'result.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(endpoint)
            os.chmod(endpoint, 0o622)
            server.listen(4)
            deadline = time.monotonic() + SCRIPT_TIMEOUT
            subprocess.run(['/bin/launchctl', 'asuser', str(uid), '/usr/bin/sudo', '-n', '-u', owner,
                            '/usr/bin/open', '-g', '-n', str(executable.parents[2]), '--args', '--result-socket', endpoint],
                           capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, check=True)
            library = ctypes.CDLL('/usr/lib/libproc.dylib')
            library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
            library.proc_pidpath.restype = ctypes.c_int
            code_library = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            code_library.csops.argtypes = (ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t)
            code_library.csops.restype = ctypes.c_int
            while True:
                server.settimeout(max(.01, deadline - time.monotonic()))
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    raise RuntimeError('Focus Window Guard не ответил; проверь Универсальный доступ') from None
                with connection:
                    peer_pid = connection.getsockopt(0, 2)  # SOL_LOCAL / LOCAL_PEERPID, from the kernel.
                    path_buffer = ctypes.create_string_buffer(4096)
                    length = library.proc_pidpath(peer_pid, path_buffer, len(path_buffer))
                    code_hash = ctypes.create_string_buffer(20)
                    signed_peer = code_library.csops(peer_pid, 5, code_hash, 20) == 0 and code_hash.raw.hex() == identity['cdhash']
                    if not length or Path(os.fsdecode(path_buffer.value)) != executable or not signed_peer:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Focus Window Guard: ответ не подтверждён')
                        continue
                    connection.settimeout(max(.01, deadline - time.monotonic()))
                    connection.sendall(b'1')
                    data = b''
                    while b'\n' not in data and len(data) < 4096:
                        chunk = connection.recv(4096 - len(data))
                        if not chunk:
                            break
                        data += chunk
                    break
    try:
        value = json.loads(data)
    except ValueError:
        raise RuntimeError('Не удалось проверить окна Little Arc') from None
    if value.get('ok') is not True:
        raise RuntimeError('Focus Window Guard: ' + str(value.get('error', 'проверка не пройдена')))


def _browser_report(strict_unsupported=True, only_browser=None):
    running = browser_processes()
    if only_browser is not None:
        if only_browser not in BROWSERS:
            raise RuntimeError('Invalid observer browser')
        running.intersection_update({only_browser})
    unsupported = running.intersection(UNSUPPORTED)
    if unsupported and strict_unsupported:
        raise RuntimeError('Для защищённой сессии закрой браузер: ' + ', '.join(sorted(unsupported)))
    running.intersection_update(BROWSERS)
    if not running:
        return []
    lines = ['set reportText to ""', 'set sep to ASCII character 31']
    for name in sorted(running):
        quoted = json.dumps(name)
        kind = BROWSERS[name][1]
        if name == 'Arc':
            guard_arc_previews()
        lines.extend([
            # Check outside `tell`: resolving a target inside a tell block can
            # launch a browser that exited after the process snapshot.
            'if application ' + quoted + ' is running then',
            'tell application ' + quoted,
            'set totalTabs to 0',
            'repeat with w in windows',
            'set windowId to (id of w) as text',
            'set tabIndex to 0',
            'repeat with t in tabs of w',
            'set totalTabs to totalTabs + 1',
            'set tabIndex to tabIndex + 1',
            'set tabUrl to (URL of t) as text',
            'set tabId to ' + ('(id of t) as text' if kind == 'id' else 'tabIndex as text'),
            'if tabUrl contains "mirabreakingbad.com" or tabUrl contains "plplayer.online" or tabUrl contains "kalarona.org" or tabUrl contains "tiktok" or tabUrl contains "musical.ly" or tabUrl contains "byteoversea.com" or tabUrl contains "ibytedtos.com" or tabUrl contains "ibyteimg.com" or tabUrl contains "youtube.com" or tabUrl contains "youtu.be" or tabUrl contains "youtube-nocookie.com" or tabUrl contains "instagram.com" or tabUrl contains "cdninstagram.com" or tabUrl contains "ig.me" or tabUrl contains "web.telegram.org" or tabUrl contains "t.me" or tabUrl contains "telegram.me" or tabUrl contains "google." or tabUrl contains "x.com" or tabUrl contains "twitter.com" or tabUrl contains "twimg.com" then',
            'set reportText to reportText & "T" & sep & ' + quoted + ' & sep & windowId & sep & tabId & sep & tabUrl & linefeed',
            'end if',
            'end repeat',
            'end repeat',
            'set reportText to reportText & "B" & sep & ' + quoted + ' & sep & (totalTabs as text) & linefeed',
            'end tell',
            'end if',
        ])
    lines.append('return reportText')
    output = user_script('\n'.join(lines))
    grouped = {}
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split(chr(31), 4)
        if fields[0] == 'B' and len(fields) == 3 and fields[1] in running:
            browser = grouped.setdefault(fields[1], {'name': fields[1], 'kind': BROWSERS[fields[1]][1], 'tabs': []})
            browser['total_tabs'] = int(fields[2])
        elif fields[0] == 'T' and len(fields) == 5 and fields[1] in running:
            if is_controlled_url(fields[4]):
                browser = grouped.setdefault(fields[1], {'name': fields[1], 'kind': BROWSERS[fields[1]][1], 'tabs': []})
                browser['tabs'].append({'window': fields[2], 'tab': fields[3], 'url': fields[4]})
        else:
            raise RuntimeError('Invalid browser observer output')
    return list(grouped.values())



def is_controlled_url(value):
    return is_youtube_surface(value) or service_for_url(value) is not None or series.is_surface(value)


def browser_report(strict_unsupported=True, browsers=None, tolerate_errors=False):
    # Give each browser its own timeout and retry budget. A slow Chrome must
    # not discard a completed Arc snapshot or cancel an authorized social timer.
    running = browser_processes()
    if browsers is not None:
        if not set(browsers).issubset(BROWSERS):
            raise RuntimeError('Invalid observer browser selection')
        running.intersection_update(browsers)
    errors = {}
    if strict_unsupported:
        for name in sorted(running.intersection(UNSUPPORTED)):
            errors[name] = 'Браузер не поддерживает проверку вкладок'
    report = BrowserReport()
    for name in sorted(running.intersection(BROWSERS)):
        for attempt in range(3):
            try:
                report.extend(_browser_report(False, only_browser=name))
                break
            except RuntimeError as error:
                race = any(code in str(error) for code in ('(-1719)', '(-1728)', '(-600)'))
                timeout = isinstance(error, BrowserScriptTimeout)
                retry = (race and attempt < 2) or (timeout and attempt < 1)
                if not retry:
                    errors[name] = str(error)
                    break
                time.sleep(0.15)
    report.errors = errors
    if errors and not tolerate_errors:
        raise BrowserObservationError(errors, report)
    return report


def _close_tabs(targets):
    if not targets:
        return
    lines = []
    for target in reversed(targets):
        # Targets originate in the observer, never in a caller's request.
        if target['application'] not in BROWSERS or not is_controlled_url(target['url']):
            raise RuntimeError('Invalid browser close target')
        quoted_name = json.dumps(target['application'])
        quoted_window = json.dumps(target['window'])
        quoted_tab = json.dumps(target['tab'])
        # Stable tab IDs identify the denied tab even if its URL changes
        # between observation and closing. Safari only exposes an index.
        url_check = (' and ((URL of t) as text) is ' + json.dumps(target['url'])
                     if target['kind'] == 'index' else '')
        lines.extend([
            'if application ' + quoted_name + ' is running then',
            'tell application ' + quoted_name,
            'repeat with w in windows',
            'if ((id of w) as text) is ' + quoted_window + ' then',
            'set tabIndex to 0',
            'repeat with t in tabs of w',
            'set tabIndex to tabIndex + 1',
            'set tabId to ' + ('(id of t) as text' if target['kind'] == 'id' else 'tabIndex as text'),
            'if tabId is ' + quoted_tab + url_check + ' then',
            'close t',
            'exit repeat',
            'end if',
            'end repeat',
            'end if',
            'end repeat',
            'end tell',
            'end if',
        ])
    lines.append('return "OK"')
    if user_script('\n'.join(lines)).strip() != 'OK':
        raise RuntimeError('Browser did not confirm closing the forbidden tab')



def close_tabs(targets):
    for attempt in range(3):
        try:
            return _close_tabs(targets)
        except RuntimeError as error:
            transient = any(code in str(error) for code in ('(-1719)', '(-1728)', '(-600)'))
            timeout = isinstance(error, BrowserScriptTimeout)
            if not ((transient and attempt < 2) or (timeout and attempt < 1)):
                raise
            time.sleep(0.15)

def targets_for(report, predicate):
    targets = []
    for browser in report:
        for tab in browser['tabs']:
            if predicate(tab['url']):
                targets.append(dict(tab, application=browser['name'], kind=browser['kind']))
    return targets


def prepare_session(scope):
    """Discard old/restored forbidden tabs while YouTube is still blocked."""
    report = browser_report()
    forbidden = targets_for(report, lambda value: is_youtube_surface(value) and not url_allowed(scope, value))
    if forbidden:
        close_tabs(forbidden)
        report = browser_report()
        if targets_for(report, lambda value: is_youtube_surface(value) and not url_allowed(scope, value)):
            raise RuntimeError('Не удалось закрыть прежние вкладки YouTube; доступ остаётся закрыт')
    return report


def inspect_session(scope, now):
    report = browser_report()
    forbidden = targets_for(report, lambda value: is_youtube_surface(value) and not url_allowed(scope, value))
    if forbidden:
        # Never reconsider a denial after another poll. Closing one tab while
        # keeping the global hosts lease open lets rapid navigation race us.
        raise ForbiddenNavigation(forbidden)
    any_allowed = any(url_allowed(scope, tab['url']) for browser in report for tab in browser['tabs'])
    if not any_allowed and not scope.get('persistent_study') and now >= scope.get('grace_until', 0):
        raise SessionClosed('Разрешённое видео или плейлист закрыты')
    return report


def close_youtube_tabs():
    report = browser_report()
    close_tabs(targets_for(report, is_youtube_surface))
    if targets_for(browser_report(), is_youtube_surface):
        raise RuntimeError('YouTube tabs are still open')


def force_stop_browsers(names):
    owner = username()
    running = browser_processes()
    if not set(names).issubset(BROWSERS):
        raise RuntimeError('Invalid browser reset target')
    running.intersection_update(names)
    for name in sorted(running):
        subprocess.run(['/usr/bin/killall', '-KILL', '-u', owner, name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    deadline = time.monotonic() + 2
    while browser_processes().intersection(running):
        if time.monotonic() >= deadline:
            raise RuntimeError('Не удалось остановить соединения браузера с YouTube')
        time.sleep(0.1)


def enforce_idle_youtube(scope=None):
    """The permanent whitelist applies even after an ordinary lease ended."""
    if scope is None:
        scope = study_scope(read_policy())
    report = browser_report()
    targets = targets_for(report, lambda value: is_youtube_surface(value) and not url_allowed(scope, value))
    if targets:
        close_tabs(targets)
        report = browser_report()
        if targets_for(report, lambda value: is_youtube_surface(value) and not url_allowed(scope, value)):
            raise RuntimeError('Не удалось закрыть запрещённую вкладку YouTube')
    return report


def refresh_tabs(targets):
    # A lecture may initially encounter blocked DNS. Reload only the exact
    # approved tab observed before activation, never a newly changed URL.
    lines = []
    for target in targets:
        if target['application'] not in BROWSERS or not is_youtube_url(target['url']):
            raise RuntimeError('Invalid YouTube reload target')
        quoted_name = json.dumps(target['application'])
        quoted_window = json.dumps(target['window'])
        quoted_tab = json.dumps(target['tab'])
        quoted_url = json.dumps(target['url'])
        lines.extend([
            'if application ' + quoted_name + ' is running then',
            'tell application ' + quoted_name,
            'repeat with w in windows',
            'if ((id of w) as text) is ' + quoted_window + ' then',
            'set tabIndex to 0',
            'repeat with t in tabs of w',
            'set tabIndex to tabIndex + 1',
            'set tabId to ' + ('(id of t) as text' if target['kind'] == 'id' else 'tabIndex as text'),
            'if tabId is ' + quoted_tab + ' and ((URL of t) as text) is ' + quoted_url + ' then',
            'reload t' if target['application'] == 'Arc' else 'set URL of t to ' + quoted_url,
            'exit repeat',
            'end if', 'end repeat', 'end if', 'end repeat', 'end tell', 'end if',
        ])
    if not targets:
        return
    lines.append('return "OK"')
    if user_script('\n'.join(lines)).strip() != 'OK':
        raise RuntimeError('Не удалось обновить разрешённую лекцию')


def record_event(kind, detail, browsers):
    atomic_json(LAST_EVENT, {'at': time.time(), 'kind': kind, 'detail': detail,
                            'browsers': sorted(browsers)}, mode=0o644)


def publish_health(ok, error=''):
    atomic_json(HEALTH, {'checked_at': time.time(), 'ok': bool(ok), 'error': error}, mode=0o644)
