#!/usr/bin/env python3
"""Wait using the ordinary status interface; never stops or edits study state."""
import json
import subprocess
import sys
import time

HELPER = '/Library/PrivilegedHelperTools/focus-open/focus-helper'


def wait_idle():
    previous = None
    while True:
        result = subprocess.run(['/usr/bin/sudo', '-n', HELPER, 'study-status'],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Не удалось проверить режим Anki. Установка не началась.')
        value = json.loads(result.stdout)
        if 'session' not in value:
            raise RuntimeError('Неизвестный ответ режима Anki. Установка не началась.')
        session = value['session']
        if session is None:
            if previous is not None:print('Учебная серия завершена. Продолжаем установку.', flush=True)
            return
        if not isinstance(session, dict) or session.get('phase') not in ('prepare','focus','break','ready'):
            raise RuntimeError('Неизвестное состояние Anki. Установка не началась.')
        phase = session['phase']
        if phase != previous:
            print('Ожидаю завершения всей серии Anki и обязательного перерыва. Можно оставить терминал открытым; Ctrl+C отменит только установку.', flush=True)
            previous = phase
        time.sleep(5)


if __name__=='__main__':
    try:wait_idle()
    except KeyboardInterrupt:
        print('\nОжидание установки отменено. Режим Anki не менялся.');raise SystemExit(130)
    except Exception as error:print('Ошибка: '+str(error),file=sys.stderr);raise SystemExit(1)
