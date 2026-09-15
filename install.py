#!/usr/bin/python3 -I
"""Install the fixed-scope helper and client. Run once with sudo."""
import datetime
import json
import os
from pathlib import Path
import plistlib
import pwd
import shutil
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parent
BASE = Path('/Library/PrivilegedHelperTools/focus-open')
PLIST = Path('/Library/LaunchDaemons/local.focus-open.timer.plist')
RULE = Path('/private/etc/sudoers.d/focus-open')
# https://core.telegram.org/mtproto/transports
TELEGRAM_DOMAINS = ['web.telegram.org'] + [
    f'{name}{suffix}.web.telegram.org'
    for name in ('pluto', 'venus', 'aurora', 'vesta', 'flora', 'kws1', 'kws2', 'kws3', 'kws4', 'kws5')
    for suffix in ('', '-1')
]
VKVIDEO_DOMAINS = [
    'vkvideo.ru',
    'www.vkvideo.ru',
    'm.vkvideo.ru',
    'api.vkvideo.ru',
    'live.vkvideo.ru',
    'api.live.vkvideo.ru',
    'video.vk.com',
]


def hosts_block(text, block_name, domains):
    start = f'# === FOCUS_BLOCK:{block_name} START ==='
    end = f'# === FOCUS_BLOCK:{block_name} END ==='
    block = start + '\n' + ''.join(
        f'{ip} {domain}\n'
        for ip in ('127.0.0.1', '::1')
        for domain in domains
    ) + end + '\n'
    if start in text or end in text:
        if start not in text or end not in text:
            raise RuntimeError(f'Incomplete hosts block: {block_name}')
        a = text.index(start)
        b = text.index(end, a) + len(end)
        return text[:a] + block.rstrip('\n') + text[b:]
    return text + ('\n' if text.endswith('\n') else '\n\n') + block

def telegram_hosts(text):
    return hosts_block(text, 'TELEGRAM', TELEGRAM_DOMAINS)


def permanent_hosts(text):
    return hosts_block(text, 'VKVIDEO', VKVIDEO_DOMAINS)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run: sudo /usr/bin/python3 -I ' + str(Path(__file__).resolve()))
    username = os.environ.get('SUDO_USER', '')
    if not username or username == 'root':
        raise SystemExit('Run sudo from your regular user account.')
    user = pwd.getpwnam(username)
    if not all(c.isalnum() or c in '_-' for c in username):
        raise SystemExit('Unsupported account name')
    for name in ('focus-helper', 'focus_open.py'):
        compile((SOURCE / name).read_text(), name, 'exec')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    client = Path(user.pw_dir) / '.local/bin/focus_open.py'
    client_backup = client.with_name('focus_open.py.backup-' + stamp)
    if client.exists():
        shutil.copy2(client, client_backup)
    backup = Path('/private/etc/hosts.backup.focus_open.install-' + stamp)
    shutil.copy2('/private/etc/hosts', backup)
    hosts = Path('/private/etc/hosts')
    updated_hosts = permanent_hosts(telegram_hosts(hosts.read_text()))
    hosts_tmp = Path('/private/etc/hosts.focus-open.install.tmp')
    hosts_tmp.write_text(updated_hosts)
    hosts_tmp.chmod(0o644)
    hosts_tmp.replace(hosts)
    # User cannot replace any code that the sudo exception executes.
    BASE.mkdir(parents=True, exist_ok=True)
    if BASE.is_symlink():
        raise SystemExit('Refusing symlink helper directory')
    os.chown(BASE, 0, 0)
    BASE.chmod(0o755)
    for name in ('focus-helper',):
        dest = BASE / name
        if dest.is_symlink():
            raise SystemExit('Refusing helper symlink')
        shutil.copyfile(SOURCE / name, dest)
        os.chown(dest, 0, 0)
        dest.chmod(0o755)
    (BASE / 'config.json').write_text(json.dumps({'username': username}))
    (BASE / 'config.json').chmod(0o600)
    data = {
        'Label': 'local.focus-open.timer',
        'ProgramArguments': ['/usr/bin/python3', '-I', str(BASE / 'focus-helper'), 'daemon'],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 1,
        'StandardOutPath': '/var/log/focus-open.log',
        'StandardErrorPath': '/var/log/focus-open.log',
    }
    PLIST.write_bytes(plistlib.dumps(data))
    os.chown(PLIST, 0, 0)
    PLIST.chmod(0o644)
    subprocess.run(['/bin/launchctl', 'bootout', 'system/local.focus-open.timer'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/bin/launchctl', 'bootstrap', 'system', str(PLIST)], check=True)
    for _ in range(30):
        result = subprocess.run([str(BASE / 'focus-helper'), 'check'], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise SystemExit('Timer failed to start. Client and sudoers were not changed.')
    RULE.parent.mkdir(mode=0o755, exist_ok=True)
    rule_tmp = BASE / 'sudoers.new'
    helper = str(BASE / 'focus-helper')
    rule_tmp.write_text(
        f'{username} ALL=(root) NOPASSWD: {helper} check, {helper} restore, '
        f'{helper} lease youtube, {helper} lease instagram, {helper} lease telegram\n'
    )
    rule_tmp.chmod(0o440)
    subprocess.run(['/usr/sbin/visudo', '-cf', str(rule_tmp)], check=True)
    previous_rule = RULE.read_bytes() if RULE.exists() else None
    if previous_rule is not None:
        shutil.copy2(RULE, BASE / ('sudoers.backup-' + stamp))
    try:
        shutil.copyfile(rule_tmp, RULE)
        os.chown(RULE, 0, 0)
        RULE.chmod(0o440)
        subprocess.run(['/usr/sbin/visudo', '-c'], check=True)
        # Verify the actual user's noninteractive path before replacing the client.
        subprocess.run(['/usr/bin/sudo', '-u', username, '/usr/bin/sudo', '-n', helper, 'check'], check=True)
    except BaseException:
        if previous_rule is None:
            RULE.unlink(missing_ok=True)
        else:
            RULE.write_bytes(previous_rule)
            RULE.chmod(0o440)
        raise
    shutil.copyfile(SOURCE / 'focus_open.py', client)
    os.chown(client, user.pw_uid, user.pw_gid)
    client.chmod(0o755)
    for name in ('test_focus_open.py',):
        target = client.with_name(name)
        shutil.copyfile(SOURCE / name, target)
        os.chown(target, user.pw_uid, user.pw_gid)
    shell_config = Path(user.pw_dir) / '.zshrc'
    alias = "alias telegram='python3 \"$HOME/.local/bin/focus_open.py\" telegram'"
    existing_aliases = [line.strip() for line in shell_config.read_text().splitlines() if line.strip().startswith('alias telegram=')] if shell_config.exists() else []
    if alias not in existing_aliases:
        with shell_config.open('a') as file:
            file.write('\n' + alias + '\n')
    os.chown(shell_config, user.pw_uid, user.pw_gid)
    print('Installed. Commands youtube, instagram and telegram are ready; no further sudo password needed.')
    print('Previous client: ' + str(client_backup))
    print('Hosts backup: ' + str(backup))


if __name__ == '__main__':
    main()
