#!/usr/bin/python3 -I
"""Install a fixed weekly whole-site schedule without changing existing limits."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import types

BASE = Path('/Library/PrivilegedHelperTools/focus-open')
SOURCE = Path(__file__).resolve().parent
APP = Path('/Applications/Focus Series Guard.app')


def assert_current(manifest):
    for name, digest in manifest['old'].items():
        path = BASE / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Установленная защита изменилась; обновление остановлено: ' + name)
    if (BASE / 'lease.json').exists():
        raise RuntimeError('Дождись окончания временного доступа: установка не прерывает его.')
    study = json.loads((BASE / 'study.json').read_text())
    if study.get('schema') != 1 or study.get('session'):
        raise RuntimeError('Дождись окончания учебного блока и обязательного перерыва.')
    if not (BASE / 'arc-preview-guard-required').is_file():
        raise RuntimeError('Сначала требуется установленный системный помощник предпросмотра Arc.')
    if not (BASE / 'no-manual-overrides').is_file():
        raise RuntimeError('Не найдено прежнее ограничение ручных разблокировок.')


def replace(path, data, mode=0o644, uid=0, gid=0):
    if path.is_symlink(): raise RuntimeError('Недопустимая ссылка: ' + str(path))
    fd, name = tempfile.mkstemp(prefix='series-schedule-', dir=str(path.parent))
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chown(name,uid,gid);os.chmod(name,mode);os.replace(name,path)
    finally: Path(name).unlink(missing_ok=True)


def main():
    if os.geteuid()!=0:raise RuntimeError('Нужно подтверждение владельца Mac в окне установки.')
    manifest=json.loads((SOURCE/'schedule-manifest.json').read_text())
    data={name:(SOURCE/name).read_bytes() for name in manifest['new']}
    for name,digest in manifest['new'].items():
        if hashlib.sha256(data[name]).hexdigest()!=digest:raise RuntimeError('Исходник изменён: '+name)
    assert_current(manifest)
    for path in [APP]+list(APP.rglob('*')):
        if path.is_symlink():raise RuntimeError('Приложение содержит ссылку.')
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['app_files'][str(path.relative_to(APP))]:
            raise RuntimeError('Приложение изменилось.')
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(APP)],check=True)
    for path in [APP]+list(APP.rglob('*')):
        os.chown(path,0,0);path.chmod(0o755 if path.is_dir() or path.name=='FocusWindowGuard' else 0o644)
    replace(BASE/'series-window-guard-identity.json',json.dumps(manifest['identity']).encode(),0o444)
    schedule=types.ModuleType('focus_series_schedule');exec(compile(data['focus_series_schedule.py'],'schedule','exec'),schedule.__dict__)
    sys.modules['focus_series_schedule']=schedule
    policy=types.ModuleType('scheduled_series');exec(compile(data['focus_series_policy.py'],'policy','exec'),policy.__dict__)
    policy.publish_preview_permission(time.time())
    sys.path.insert(0,str(BASE))
    guard=types.ModuleType('series_preview_preflight');guard.__file__=str(BASE/'focus_youtube_guard.py')
    exec(compile(data['focus_youtube_guard.py'],guard.__file__,'exec'),guard.__dict__)
    guard.check_native_previews(APP/'Contents/MacOS/FocusWindowGuard',BASE/'series-window-guard-identity.json')
    username=json.loads((BASE/'config.json').read_text())['username'];user=pwd.getpwnam(username)
    client=Path(user.pw_dir)/'.local/bin/series_open.py';launcher=client.with_name('series')
    if client.parent.is_symlink() or client.parent.parent.is_symlink():raise RuntimeError('Некорректная папка пользовательских команд.')
    writes={BASE/name:(value,0o755 if name=='focus-helper' else 0o644,0,0) for name,value in data.items() if name!='series_open.py'}
    writes.update({client:(data['series_open.py'],0o755,user.pw_uid,user.pw_gid),
                   launcher:(('#!/bin/sh\nexec python3 '+shlex.quote(str(client))+' "$@"\n').encode(),0o755,user.pw_uid,user.pw_gid),
                   BASE/'series-schedule-enabled':(b'Mon Wed Fri Sun 20:30-22:00 Europe/Moscow; whole site; no browser extension.\n',0o444,0,0)})
    hosts=Path('/etc/hosts')
    with (BASE/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert_current(manifest)
        current=hosts.read_text();start='# === FOCUS_BLOCK:SERIES START ===';end='# === FOCUS_BLOCK:SERIES END ==='
        block=start+'\n'+''.join(ip+' '+host+'\n' for ip in ('127.0.0.1','::1') for host in policy.DOMAINS)+end
        if start in current or end in current:
            if current.count(start)!=1 or current.count(end)!=1 or current.index(end)<current.index(start):raise RuntimeError('Некорректный прежний блок сериала.')
            a,b=current.index(start),current.index(end)+len(end);current=current[:a]+block+current[b:]
        else:current=current.rstrip()+'\n\n'+block+'\n'
        writes[hosts]=(current.encode(),0o644,0,0)
        preserved={name:(BASE/name).read_bytes() for name in ('series-state.json','youtube-usage.json','service-usage.json','study.json','no-manual-overrides') if (BASE/name).exists()}
        backup=Path(tempfile.mkdtemp(prefix='series-schedule-',dir=str(BASE/'backups')));backup.chmod(0o700)
        old={}
        for index,path in enumerate(writes):
            if path.is_symlink():raise RuntimeError('Недопустимая ссылка: '+str(path))
            if path.exists():
                info=path.stat();old[path]=(path.read_bytes(),stat.S_IMODE(info.st_mode),info.st_uid,info.st_gid)
                replace(backup/str(index),old[path][0],0o600)
            else:old[path]=None
        try:
            for path,contents in writes.items():replace(path,*contents)
            for name,contents in preserved.items():
                if (BASE/name).read_bytes()!=contents:raise RuntimeError('Существующие ограничения изменились: '+name)
        except BaseException:
            for path,prior in old.items():
                if prior:replace(path,*prior)
                else:path.unlink(missing_ok=True)
            raise
    began=time.time();subprocess.run(['/bin/launchctl','kickstart','-k','system/local.focus-open.timer'],check=True,timeout=10)
    until=time.monotonic()+30
    while True:
        health=json.loads((BASE/'youtube-guard-health.json').read_text())
        if health.get('ok') and health.get('checked_at',0)>began:break
        if time.monotonic()>until:raise RuntimeError('Не подтверждена работа службы. Нужна проверка; сайт остаётся закрыт.')
        time.sleep(.5)
    print('Установлено: Пн, Ср, Пт, Вс, 20:30–22:00 по Москве. Доступ ко всему сайту, без выбора серии.')
    print('Отключи старое расширение «Focus · одна серия» в Arc. Команда: series; проверка: series --status.')

if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(str(error))
