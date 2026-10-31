#!/usr/bin/python3 -I
"""Install the complete focus service without resetting protected quotas."""
import copy
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

SOURCE = Path(__file__).resolve().parent
BASE = Path('/Library/PrivilegedHelperTools/focus-open')
PLIST = Path('/Library/LaunchDaemons/local.focus-open.timer.plist')
RULE = Path('/private/etc/sudoers.d/focus-open')
HOSTS = Path('/private/etc/hosts')
ROOT_FILES = ('focus-helper', 'focus_youtube_guard.py', 'focus_service_policy.py', 'focus_playlist_sync.py', 'focus_study.py', 'focus_series_policy.py', 'focus_series_schedule.py')
ANKI_FILES = ('__init__.py', 'bridge.py', 'dashboard.py', 'analytics.py', 'activity_detail.py', 'review_health.py', 'focus-stats.js', 'focus-stats.css', 'manifest.json') + tuple(
    f'wallpaper-{scene}.png' for scene in ('dawn', 'day', 'evening', 'night')) + tuple(
    f'wallpaper-{theme}-{scene}.png'
    for theme in ('skyline', 'sea', 'architecture', 'amg', 'rolls', 'goggins')
    for scene in ('predawn', 'dawn', 'morning', 'day', 'golden', 'evening', 'night'))
YOUTUBE_DOMAINS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be',
                   'youtube-nocookie.com', 'www.youtube-nocookie.com', 'googlevideo.com', 'www.googlevideo.com',
                   'ytimg.com', 'www.ytimg.com', 'i.ytimg.com', 's.ytimg.com', 'youtubei.googleapis.com',
                   'music.youtube.com', 'studio.youtube.com', 'kids.youtube.com')
INSTAGRAM_DOMAINS = ('instagram.com', 'www.instagram.com', 'm.instagram.com', 'i.instagram.com',
                     'api.instagram.com', 'graph.instagram.com', 'help.instagram.com', 'business.instagram.com',
                     'about.instagram.com', 'creators.instagram.com', 'l.instagram.com', 'static.cdninstagram.com',
                     'scontent.cdninstagram.com', 'cdninstagram.com', 'www.cdninstagram.com', 'ig.me', 'www.ig.me')
TELEGRAM_DOMAINS = ('web.telegram.org',) + tuple(
    f'{name}{suffix}.web.telegram.org'
    for name in ('pluto', 'venus', 'aurora', 'vesta', 'flora', 'kws1', 'kws2', 'kws3', 'kws4', 'kws5')
    for suffix in ('', '-1')) + ('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me')
VKVIDEO_DOMAINS = ('vkvideo.ru', 'www.vkvideo.ru', 'm.vkvideo.ru', 'api.vkvideo.ru',
                   'live.vkvideo.ru', 'api.live.vkvideo.ru', 'video.vk.com')


def hosts_block(text, name, domains):
    start, end = '# === FOCUS_BLOCK:' + name + ' START ===', '# === FOCUS_BLOCK:' + name + ' END ==='
    if text.count(start) > 1 or text.count(end) > 1:
        raise RuntimeError('Duplicate hosts block: ' + name)
    block = start + '\n' + ''.join(f'{ip} {domain}\n' for ip in ('127.0.0.1', '::1') for domain in domains) + end + '\n'
    if start in text or end in text:
        if start not in text or end not in text or text.index(end) < text.index(start):
            raise RuntimeError('Incomplete hosts block: ' + name)
        a, b = text.index(start), text.index(end) + len(end)
        return text[:a] + block.rstrip('\n') + text[b:]
    return text + ('\n' if text.endswith('\n') else '\n\n') + block


def tiktok_hosts_block(text, domains):
    """Fold the retired timer's block into the ordinary, initially blocked one."""
    start, end = '# >>> block-tiktok', '# <<< block-tiktok'
    if start in text or end in text:
        if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
            raise RuntimeError('Legacy TikTok block missing or ambiguous')
        a, b = text.index(start), text.index(end) + len(end)
        prior = []
        for line in text[a:b].splitlines():
            match = re.match(r'^(?:# TEMP_(?:TIKTOK_HOUR|FOCUS_UNLOCK) )?(?:127\.0\.0\.1|0\.0\.0\.0|::1)\s+([a-zA-Z0-9.-]+)\s*$', line.strip())
            if match:
                prior.append(match[1])
        domains = tuple(dict.fromkeys(tuple(domains) + tuple(prior)))
        text = text[:a] + text[b:]
    return hosts_block(text, 'TIKTOK', domains)


def merge_policy(existing, incoming):
    if existing is None:
        return copy.deepcopy(incoming)
    if existing.get('schema_version') != 1 or not isinstance(existing.get('playlists'), list):
        raise RuntimeError('Existing YouTube policy is invalid; refusing to reset it')
    result = copy.deepcopy(existing)
    for playlist in incoming['playlists']:
        matches = [p for p in result['playlists'] if p['id'] == playlist['id']]
        if not matches:
            result['playlists'].append(copy.deepcopy(playlist))
        else:
            target = matches[0]
            if target.get('owner_id') not in (None, playlist.get('owner_id')):
                raise RuntimeError('Approved playlist owner conflict')
            for key in ('owner_id', 'auto_refresh', 'refresh_interval_seconds'):
                if key in playlist:
                    target[key] = playlist[key]
            # Retain live video caches, custom aliases and titles on reinstall.
    for channel in incoming.get('channels', []):
        channels = result.setdefault('channels', [])
        if not any(c['id'] == channel['id'] for c in channels):
            channels.append(copy.deepcopy(channel))
    return result


def migrate_youtube_usage(state):
    result = {'last_regular_opened_at': None, 'counts': {}}
    dates = []
    last = state.get('services', {}).get('youtube', {}).get('last_opened_at')
    if last:
        dates.append(last)
    for entry in state.get('history', []):
        if entry.get('service') != 'youtube' or not entry.get('access_granted', True):
            continue
        parsed = urlparse(entry.get('url', ''))
        host = (parsed.hostname or '').lower()
        if host == 'youtu.be':
            video = parsed.path.strip('/')
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path == '/watch':
            video = parse_qs(parsed.query).get('v', [''])[0]
        else:
            video = ''
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            result['counts'][video] = result['counts'].get(video, 0) + 1
        if entry.get('opened_at'):
            dates.append(entry['opened_at'])
    times = []
    for value in dates:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise RuntimeError('Missing timezone in legacy YouTube history')
        times.append(parsed.timestamp())
    if times:
        result['last_regular_opened_at'] = max(times)
    return result


def sudoers_text(username):
    helper = str(BASE / 'focus-helper')
    commands = ('check', 'restore', 'stop', 'lease youtube', 'lease instagram', 'lease x', 'lease telegram', 'lease tiktok',
                'guard-check', 'youtube-status', 'youtube-policy', 'youtube-duration',
                'study-start', 'study-status', 'study-break-start', 'study-break-shorten', 'study-next-ready', 'study-next-update', 'study-next-start', 'study-emergency-request', 'study-emergency-confirm')
    if (BASE / 'series-enabled').exists() or (BASE / 'series-schedule-enabled').exists():
        commands += ('series-event',)
    return username + ' ALL=(root) NOPASSWD: ' + ', '.join(helper + ' ' + c for c in commands) + '\n'


def replace(path, data, mode, uid=0, gid=0):
    if path.is_symlink():
        raise RuntimeError('Refusing destination symlink: ' + str(path))
    fd, name = tempfile.mkstemp(prefix=path.name + '.install.', dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def root_json(path, value, mode=0o600):
    replace(path, (json.dumps(value, ensure_ascii=False) + '\n').encode(), mode)


def secure_directory(path):
    if path.is_symlink():
        raise RuntimeError('Refusing directory symlink: ' + str(path))
    path.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise RuntimeError('Root-owned directory required: ' + str(path))


def anki_destination(user):
    base = Path(user.pw_dir).resolve() / 'Library/Application Support/Anki2/addons21'
    if not base.is_dir() or any(path.is_symlink() for path in (base, *base.parents)):
        raise RuntimeError('Не найден обычный каталог дополнений Anki: ' + str(base))
    target = base / 'slovo_study_blocks'
    if target.is_symlink():
        raise RuntimeError('Refusing an Anki add-on symlink')
    return target


def interrupt_for_repair(value, now):
    """Only the explicitly invoked, administrator-authorized installer uses this.

    There is no new sudoers/helper escape command. Preserve all history and
    charge the ordinary daily emergency exit for this interrupted block.
    """
    value = copy.deepcopy(value)
    session = value.get('session')
    if session:
        if session.get('phase') != 'focus':
            raise ValueError('Ремонтная остановка предназначена для активного учебного блока.')
        entry = {k: copy.deepcopy(session[k]) for k in ('id', 'task', 'apps', 'started_at', 'attempts', 'app_attempts')}
        entry.update(ended_at=now, outcome='interrupted', seconds=max(0, session.get('focus_seconds', 4800) - session['remaining']) + session.get('overtime_seconds', 0),
                     reason='Остановка зависшего блока при ремонте службы по запросу пользователя')
        value['history'] = (value['history'] + [entry])[-1000:]
        value['last_emergency'] = now
        value['session'] = None
    return value


def main(install_anki=False, stop_current_study=False):
    if os.geteuid() != 0:
        raise SystemExit('Run: sudo /usr/bin/python3 -I ' + str(SOURCE / 'install.py'))
    username = os.environ.get('SUDO_USER', '')
    if not username or username == 'root' or not re.fullmatch(r'[A-Za-z0-9_-]+', username):
        raise SystemExit('Run sudo from your regular user account.')
    user = pwd.getpwnam(username)
    addon = anki_destination(user) if install_anki else None
    for name in ROOT_FILES + ('focus_open.py',):
        compile((SOURCE / name).read_text(), name, 'exec')
    if addon:
        for name in ANKI_FILES:
            if name.endswith('.py'):
                compile((SOURCE / 'anki_study' / name).read_text(), name, 'exec')
    incoming = json.loads((SOURCE / 'youtube-policy.json').read_text())
    secure_directory(BASE)
    secure_directory(RULE.parent)
    local = Path(user.pw_dir) / '.local'
    if local.is_symlink() or (local / 'bin').is_symlink():
        raise SystemExit('Refusing a symlink client directory')
    local.mkdir(exist_ok=True)
    (local / 'bin').mkdir(exist_ok=True)
    os.chown(local, user.pw_uid, user.pw_gid)
    os.chown(local / 'bin', user.pw_uid, user.pw_gid)
    client = local / 'bin/focus_open.py'
    state_path = Path(user.pw_dir) / '.focus_open/state.json'
    if state_path.is_symlink() or state_path.parent.is_symlink():
        raise SystemExit('Refusing symlink history')
    state = json.loads(state_path.read_text()) if state_path.exists() else {'services': {}, 'history': []}
    if not isinstance(state, dict) or not isinstance(state.get('services', {}), dict) or not isinstance(state.get('history', []), list):
        raise SystemExit('Legacy client history is invalid; refusing to reset it')
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    secure_directory(BASE / 'backups')
    backup = BASE / 'backups' / ('install-' + stamp)
    backup.mkdir(mode=0o700)
    old = {}
    label = 'system/local.focus-open.timer'
    legacy_tiktok = PLIST.with_name('local.focus-open.tiktok-hour-20261017.plist')
    legacy_retired = False
    with (BASE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (BASE / 'lease.json').exists():
            raise SystemExit('A temporary session is active. Retry after it ends; its deadline is preserved.')
        if (BASE / 'study.json').exists():
            protected_study = json.loads((BASE / 'study.json').read_text())
            if protected_study.get('schema') != 1 or (protected_study.get('session') and not stop_current_study):
                raise SystemExit('Учебный блок или перерыв активен. Установка не меняет его условия; дождись окончания.')
            if stop_current_study:
                interrupt_for_repair(protected_study, time.time())  # Validate before changing files.
        configuration = json.loads((BASE / 'config.json').read_text()) if (BASE / 'config.json').exists() else {}
        if configuration.get('username', username) != username:
            raise SystemExit('The installed focus service belongs to a different account.')
        policy = merge_policy(json.loads((BASE / 'youtube-policy.json').read_text()) if (BASE / 'youtube-policy.json').exists() else None, incoming)
        if addon:
            addon.mkdir(mode=0o700, exist_ok=True)
            os.chown(addon, user.pw_uid, user.pw_gid)
        destinations = [BASE / name for name in ROOT_FILES] + [BASE / name for name in ('config.json', 'youtube-policy.json', 'youtube-usage.json', 'service-usage.json')] + [HOSTS, PLIST, RULE, client]
        if legacy_tiktok.exists():
            destinations.append(legacy_tiktok)
        if stop_current_study:
            destinations.append(BASE / 'study.json')
        if addon:
            destinations += [addon / name for name in ANKI_FILES]
        launchers = ('focus-open', 'youtube', 'instagram', 'x', 'telegram', 'tiktok')
        destinations += [local / 'bin' / name for name in launchers]
        for path in destinations:
            if path.is_symlink():
                raise RuntimeError('Refusing destination symlink: ' + str(path))
            if path.exists():
                info = path.stat()
                old[path] = (path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
                replace(backup / ('file-' + str(len(old))), old[path][0], 0o600)
            else:
                old[path] = None
        root_json(backup / 'metadata.json', {str(p): {'mode': v[1], 'uid': v[2], 'gid': v[3]} if v else None for p, v in old.items()})
        subprocess.run(['/bin/launchctl', 'bootout', label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if legacy_tiktok.exists():
                subprocess.run(['/bin/launchctl', 'bootout', 'system/local.focus-open.tiktok-hour-20261017'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                legacy_tiktok.unlink()  # Recoverable from the installation backup.
                legacy_retired = True
            for name in ROOT_FILES:
                replace(BASE / name, (SOURCE / name).read_bytes(), 0o755 if name == 'focus-helper' else 0o644)
            sys.path.insert(0, str(BASE))
            import focus_service_policy as socials
            import focus_youtube_guard as youtube
            import focus_study as study
            configuration.update(username=username, uid=user.pw_uid)
            root_json(BASE / 'config.json', configuration)
            root_json(youtube.POLICY, policy, 0o644)
            youtube.read_policy()
            # Reinstall never deletes, migrates or resets existing protected usage.
            if not youtube.USAGE.exists():
                root_json(youtube.USAGE, migrate_youtube_usage(state))
            else:
                youtube.read_usage()
            if not socials.USAGE.exists():
                root_json(socials.USAGE, socials.migrate_client_state(state))
            else:
                socials.read_usage()
            if stop_current_study and study.STATE.exists():
                study.save(interrupt_for_repair(study.read(), time.time()))
            text = HOSTS.read_text()
            text = tiktok_hosts_block(text, socials.TIKTOK_HOSTS)
            for name, domains in (('YOUTUBE', YOUTUBE_DOMAINS), ('INSTAGRAM', INSTAGRAM_DOMAINS), ('X', socials.X_HOSTS), ('TELEGRAM', TELEGRAM_DOMAINS), ('VKVIDEO', VKVIDEO_DOMAINS)):
                text = hosts_block(text, name, domains)
            text = hosts_block(text, 'STUDY_WEB', study.DISTRACTION_DOMAINS)
            replace(HOSTS, text.encode(), 0o644)
            data = {'Label': 'local.focus-open.timer', 'ProgramArguments': ['/usr/bin/python3', '-I', str(BASE / 'focus-helper'), 'daemon'],
                    'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 1,
                    'StandardOutPath': '/var/log/focus-open.log', 'StandardErrorPath': '/var/log/focus-open.log'}
            replace(PLIST, plistlib.dumps(data), 0o644)
            rule_tmp = BASE / 'sudoers.new'
            replace(rule_tmp, sudoers_text(username).encode(), 0o440)
            subprocess.run(['/usr/sbin/visudo', '-c', '-f', str(rule_tmp)], check=True)
            replace(RULE, rule_tmp.read_bytes(), 0o440)
            replace(client, (SOURCE / 'focus_open.py').read_bytes(), 0o755, user.pw_uid, user.pw_gid)
            if addon:
                for name in ANKI_FILES:
                    replace(addon / name, (SOURCE / 'anki_study' / name).read_bytes(), 0o600, user.pw_uid, user.pw_gid)
            for name in launchers:
                args = '' if name == 'focus-open' else ' ' + name
                script = '#!/bin/sh\nexec python3 ' + shlex.quote(str(client)) + args + ' "$@"\n'
                replace(local / 'bin' / name, script.encode(), 0o755, user.pw_uid, user.pw_gid)
            subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(PLIST)], check=True)
        except BaseException:
            subprocess.run(['/bin/launchctl', 'bootout', label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for path, prior in old.items():
                if prior:
                    replace(path, *prior)
                else:
                    path.unlink(missing_ok=True)
            if legacy_retired and legacy_tiktok.exists():
                subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(legacy_tiktok)], check=False)
            if PLIST.exists():
                subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(PLIST)], check=False)
            raise
    for _ in range(30):
        check = subprocess.run([str(BASE / 'focus-helper'), 'check'], capture_output=True)
        if check.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise SystemExit('Files installed; timer startup unconfirmed. Access stays blocked. Backup: ' + str(backup))
    subprocess.run(['/usr/bin/sudo', '-u', username, '/usr/bin/sudo', '-n', str(BASE / 'focus-helper'), 'check'], check=True)
    shell = Path(user.pw_dir) / '.zshrc'
    line = 'export PATH="$HOME/.local/bin:$PATH"'
    if shell.is_symlink():
        raise SystemExit('Installed. Add ~/.local/bin to PATH manually; .zshrc is a symlink.')
    if not shell.exists() or line not in shell.read_text().splitlines():
        with shell.open('a') as stream:
            stream.write('\n' + line + '\n')
        os.chown(shell, user.pw_uid, user.pw_gid)
    print('Installed: youtube, instagram, x, telegram, tiktok. Daily reset: midnight Europe/Moscow.')
    print('Approve macOS Automation requests for your browser if prompted. Restart Terminal for new commands.')
    print('Backup: ' + str(backup))
    if addon:
        print('Установлены «Учебные блоки · Слово». Перезапусти Anki; учебный блок сам не запускается.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anki', action='store_true', help='Установить панель учебных блоков в существующий Anki')
    parser.add_argument('--stop-current-study', action='store_true', help='Явно прервать зависший учебный блок при ремонте; потратит дневной экстренный выход')
    options = parser.parse_args()
    main(options.anki, options.stop_current_study)
