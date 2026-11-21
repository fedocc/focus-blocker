#!/usr/bin/env python3
"""Open the series site; enforce the schedule only while it is installed."""
import json
import subprocess
import sys
import datetime as dt
from zoneinfo import ZoneInfo
from pathlib import Path

from focus_open import default_browser_app, open_url

HELPER = '/Library/PrivilegedHelperTools/focus-open/focus-helper'
SCHEDULE_ENABLED = Path(HELPER).parent / 'series-schedule-enabled'
SITE_URL = 'https://www.mirabreakingbad.com/'

def main():
    if sys.argv[1:] not in ([], ['--status']):
        raise RuntimeError('Использование: series [--status]')
    if not SCHEDULE_ENABLED.exists():
        print('Блокировка сериала отключена.')
        if '--status' not in sys.argv:
            open_url(SITE_URL, default_browser_app())
        return 0
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
        open_url(state['site_url'], default_browser_app())
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error: print('Ошибка: ' + str(error), file=sys.stderr); raise SystemExit(1)
