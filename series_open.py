#!/usr/bin/env python3
"""User-facing launcher; reads the protected schedule and cannot grant time."""
import json
import subprocess
import sys
import datetime as dt
from zoneinfo import ZoneInfo

HELPER = '/Library/PrivilegedHelperTools/focus-open/focus-helper'

def main():
    if sys.argv[1:] not in ([], ['--status']):
        raise RuntimeError('Использование: series [--status]')
    result = subprocess.run(['/usr/bin/sudo', '-n', HELPER, 'series-event'],
                            input='{"op":"status"}', capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Помощник расписания недоступен')
    state = json.loads(result.stdout)
    print(state['schedule'])
    if not state['active']:
        when = dt.datetime.fromtimestamp(state['next_at'], ZoneInfo('Europe/Moscow'))
        print('Сайт закрыт на время учёбы.' if state['studying'] else 'Следующее окно: ' + when.strftime('%d.%m %H:%M'))
        return 1
    until = dt.datetime.fromtimestamp(state['window_end'], ZoneInfo('Europe/Moscow'))
    print('Доступ к сайту до ' + until.strftime('%H:%M') + ' по Москве.')
    if '--status' not in sys.argv:
        subprocess.run(['/usr/bin/open', '-a', 'Arc', state['site_url']], check=True)
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error: print('Ошибка: ' + str(error), file=sys.stderr); raise SystemExit(1)
