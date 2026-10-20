"""Wait without administrator credentials; never stop or change a study block."""
import json
from pathlib import Path
import subprocess
import time

HELPER = Path('/Library/PrivilegedHelperTools/focus-open/focus-helper')


def current_session():
    result = subprocess.run(['/usr/bin/sudo', '-n', str(HELPER), 'study-status'],
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('Не удалось проверить блок. Обновление не запускалось; попробуй после отдыха.')
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or 'session' not in state:
        raise ValueError('Некорректное состояние блока; обновление отменено.')
    session = state['session']
    if session is not None and (not isinstance(session, dict) or session.get('phase') not in ('focus', 'break', 'ready', 'prepare')):
        raise ValueError('Неизвестное состояние блока; обновление отменено.')
    return session


def wait_until_idle(read=current_session, pause=time.sleep, report=print):
    previous = None
    while True:
        session = read()
        if session is None:
            report('Блок и отдых завершены. Можно установить обновление.')
            return
        phase = session['phase']
        if phase != previous:
            report('Ожидаю окончания ' + {'focus':'учебного блока', 'break':'отдыха', 'ready':'серии (подтверди возвращение в Anki)', 'prepare':'подготовки и серии'}[phase] +
                   '. Таймер и ограничения сохраняются. Ctrl+C — отменить обновление.')
            previous = phase
        pause(5)


def main():
    if not HELPER.exists():
        return 0
    try:
        wait_until_idle()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error))
        return 1
    except KeyboardInterrupt:
        print('\nОбновление отменено. Учебный блок не менялся.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
