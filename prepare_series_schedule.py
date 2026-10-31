#!/usr/bin/env python3
"""Prepare a hash-pinned local schedule update, preserving live helper changes."""
from pathlib import Path
import hashlib
import json
import shlex
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
BASE = Path('/Library/PrivilegedHelperTools/focus-open')
APP = Path('/Applications/Focus Series Guard.app')


def prepare(destination):
    stage=Path(destination).resolve()
    if stage.exists():raise ValueError('Нужен новый каталог; существующие файлы сохраняются.')
    if not APP.is_dir():raise ValueError('Сначала собери Focus Series Guard.app с build_window_guard.py --series и помести в /Applications.')
    stage.mkdir(parents=True)
    for name in ('focus_series_policy.py','focus_series_schedule.py','series_open.py','install_series_schedule.py','Install Series Schedule.command'):
        shutil.copy2(SOURCE/name,stage/name)
    helper=(BASE/'focus-helper').read_text()
    old="    try:\n        report = youtube.browser_report(tolerate_errors=True)\n        series_denied"
    new="    try:\n        if series.ENABLED.exists():\n            series.publish_preview_permission(time.time(), studying=bool(focus))\n        series_hosts = series.hosts_text(HOSTS.read_text(), time.time(), studying=bool(focus))\n        if series_hosts != HOSTS.read_text():\n            write_hosts(series_hosts)\n            flush()\n        report = youtube.browser_report(tolerate_errors=True)\n        series_denied"
    if 'series_hosts = series.hosts_text' not in helper:
        if helper.count(old)!=1:raise ValueError('Не распознан установленный наблюдатель; автоматическая замена запрещена.')
        helper=helper.replace(old,new)
    (stage/'focus-helper').write_text(helper)
    guard=(BASE/'focus_youtube_guard.py').read_text()
    if 'def check_native_previews' not in guard:
        start=guard.index('    identity_info = identity_path.lstat()',guard.index('def guard_arc_previews'))
        guard=guard[:start]+"    check_native_previews(executable, identity_path)\n    if (BASE / 'series-schedule-enabled').exists():\n        check_native_previews(Path('/Applications/Focus Series Guard.app/Contents/MacOS/FocusWindowGuard'),\n                              BASE / 'series-window-guard-identity.json')\n\n\ndef check_native_previews(executable, identity_path):\n"+guard[start:]
    if 'tabUrl contains "mirabreakingbad.com"' not in guard:
        needle='if tabUrl contains "tiktok" or'
        if needle not in guard:raise ValueError('Не распознан фильтр адресов браузера.')
        guard=guard.replace(needle,'if tabUrl contains "mirabreakingbad.com" or tabUrl contains "plplayer.online" or tabUrl contains "kalarona.org" or tabUrl contains "tiktok" or')
    if 'Focus Series Guard.app' not in guard:raise ValueError('Не найден системный контроль предпросмотра сериала.')
    (stage/'focus_youtube_guard.py').write_text(guard)
    files=('focus-helper','focus_youtube_guard.py','focus_series_policy.py','focus_series_schedule.py','series_open.py')
    for name in files:compile((stage/name).read_text(),name,'exec')
    cs=subprocess.run(['/usr/bin/codesign','-dvvv',str(APP)],capture_output=True,text=True,check=True).stderr
    manifest={'old':{name:hashlib.sha256((BASE/name).read_bytes()).hexdigest() for name in files if (BASE/name).exists()},
              'new':{name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in files},
              'app_files':{str(p.relative_to(APP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in APP.rglob('*') if p.is_file()},
              'identity':{'sha256':hashlib.sha256((APP/'Contents/MacOS/FocusWindowGuard').read_bytes()).hexdigest(),
                          'cdhash':next(line.split('=',1)[1] for line in cs.splitlines() if line.startswith('CDHash='))}}
    m=stage/'schedule-manifest.json';m.write_text(json.dumps(manifest,indent=2)+'\n')
    p=stage/'install_series_schedule.py'
    code='import hashlib;from pathlib import Path;p=Path('+repr(str(p))+');m=Path('+repr(str(m))+');d=p.read_bytes();assert hashlib.sha256(d).hexdigest()=='+repr(hashlib.sha256(p.read_bytes()).hexdigest())+';assert hashlib.sha256(m.read_bytes()).hexdigest()=='+repr(hashlib.sha256(m.read_bytes()).hexdigest())+';exec(compile(d,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})'
    (stage/'install_schedule.applescript').write_text('do shell script '+json.dumps('/usr/bin/python3 -I -c '+shlex.quote(code))+' with administrator privileges\n')
    return stage/'Install Series Schedule.command'

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: prepare_series_schedule.py /new/output/directory')
    print(prepare(sys.argv[1]))
