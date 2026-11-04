#!/usr/bin/python3 -I
"""Apply only the four owner-approved fixes; preserve protected user state."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile
import time
import types

BASE=Path('/Library/PrivilegedHelperTools/focus-open')
SOURCE=Path(__file__).resolve().parent
CODE=('focus_youtube_guard.py','focus-helper','focus_open.py')
DATA=('study.json','lease.json','youtube-usage.json','service-usage.json','series-state.json','no-manual-overrides')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def idle():
    state=json.loads((BASE/'study.json').read_text())
    if state.get('schema')!=1 or state.get('session'):
        raise RuntimeError('Обновление ждёт полного завершения учебной серии и обязательного перерыва.')
    if (BASE/'lease.json').exists():raise RuntimeError('Обновление ждёт окончания текущего временного доступа.')
    if not (BASE/'no-manual-overrides').is_file():raise RuntimeError('Не найден запрет ручных исключений.')


def validate(manifest,targets):
    payload={}
    for name in CODE:
        incoming=(SOURCE/name).read_bytes();compile(incoming.decode(),name,'exec')
        if hashlib.sha256(incoming).hexdigest()!=manifest['new'][name]:raise RuntimeError('Изменён подготовленный файл: '+name)
        target=targets[name];info=target.lstat()
        if not stat.S_ISREG(info.st_mode) or (name!='focus_open.py' and (info.st_uid!=0 or info.st_mode&0o022)):
            raise RuntimeError('Недопустимые права компонента: '+name)
        if digest(target) not in (manifest['old'][name],manifest['new'][name]):
            raise RuntimeError('Защита изменилась параллельно; обновление остановлено: '+name)
        payload[name]=incoming
    return payload


def replace(path,data,mode,uid,gid):
    if path.is_symlink():raise RuntimeError('Недопустимая ссылка: '+str(path))
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.approved-fix-',delete=False) as out:
            temporary=Path(out.name);out.write(data);out.flush();os.fsync(out.fileno())
        os.chown(temporary,uid,gid);temporary.chmod(mode);os.replace(temporary,path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def main():
    if os.geteuid()!=0:raise RuntimeError('Нужно подтверждение владельца Mac в системном окне.')
    info=BASE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise RuntimeError('Недопустимый каталог защиты.')
    username=json.loads((BASE/'config.json').read_text())['username'];owner=pwd.getpwnam(username)
    client=Path(owner.pw_dir)/'.local/bin/focus_open.py'
    if client.parent.is_symlink() or client.parent.parent.is_symlink():raise RuntimeError('Недопустимая папка клиента.')
    targets={name:BASE/name for name in CODE};targets['focus_open.py']=client
    manifest_bytes=globals().get('_PINNED_MANIFEST')
    if manifest_bytes is None:manifest_bytes=(SOURCE/'approved-manifest.json').read_bytes()
    manifest=json.loads(manifest_bytes)
    payload=validate(manifest,targets);idle()
    # Test the installed signed apps with the updated asynchronous handshake.
    # No bundles, Accessibility permissions, native app shield or sudoers change.
    import sys
    sys.path.insert(0,str(BASE))
    guard=types.ModuleType('approved_guard');guard.__file__=str(BASE/'focus_youtube_guard.py')
    exec(compile(payload['focus_youtube_guard.py'],guard.__file__,'exec'),guard.__dict__)
    guard.guard_arc_previews()
    lock_path=BASE/'lock'
    if lock_path.is_symlink():raise RuntimeError('Недопустимая блокировка службы.')
    with lock_path.open('a') as lock:
        deadline=time.monotonic()+30
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>deadline:raise RuntimeError('Служба занята; повтори установку позже.')
                time.sleep(.1)
        payload=validate(manifest,targets);idle()
        before={name:digest(BASE/name) for name in DATA}
        backup_root=BASE/'backups';info=backup_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('Недопустимый каталог копий кода.')
        backup=Path(tempfile.mkdtemp(prefix='approved-1456-',dir=backup_root));backup.chmod(0o700)
        prior={}
        for name,target in targets.items():
            info=target.stat();prior[name]=(target.read_bytes(),stat.S_IMODE(info.st_mode),info.st_uid,info.st_gid)
            (backup/name).write_bytes(prior[name][0]);(backup/name).chmod(0o600)
        try:
            for name,target in targets.items():
                replace(target,payload[name],0o755 if name in ('focus-helper','focus_open.py') else 0o644,
                        owner.pw_uid if name=='focus_open.py' else 0,owner.pw_gid if name=='focus_open.py' else 0)
            if before!={name:digest(BASE/name) for name in DATA}:raise RuntimeError('Защищённые данные изменились параллельно.')
        except BaseException:
            for name,target in targets.items():replace(target,*prior[name])
            raise
        began=time.time()
        subprocess.run(['/bin/launchctl','kickstart','-k','system/local.focus-open.timer'],check=True,timeout=10)
        if before!={name:digest(BASE/name) for name in DATA}:raise RuntimeError('Неожиданное изменение данных при перезапуске.')
    deadline=time.monotonic()+40
    while True:
        health=json.loads((BASE/'youtube-guard-health.json').read_text())
        if health.get('ok') and health.get('checked_at',0)>began:break
        if time.monotonic()>deadline:raise RuntimeError('Код установлен; исправная работа наблюдателя пока не подтверждена.')
        time.sleep(.5)
    report={'approved_items':[1,5,6],'code_files':list(CODE),'protected_data_preserved':True,
            'guard_healthy':True,'native_apps_unchanged':True,'sudoers_unchanged':True,
            'series_schedule_unchanged':'Wed Fri Sun 20:30-22:00 Europe/Moscow'}
    (backup/'result.json').write_text(json.dumps(report,indent=2)+'\n');(backup/'result.json').chmod(0o600)
    print('Исправления 1, 5 и 6 установлены. Наблюдатель работает; лимиты и учебные данные сохранены.')

if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(str(error))
