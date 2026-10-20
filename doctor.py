#!/usr/bin/env python3
"""Read-only source and Mac prerequisites; never starts a protected block."""
import argparse
import json
from pathlib import Path
import platform
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='Проверить комплект без установленного Anki')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    errors = []
    for name in ('install.py', 'focus-helper', 'focus_open.py', 'focus_youtube_guard.py',
                 'focus_service_policy.py', 'focus_playlist_sync.py', 'focus_study.py',
                 'anki_study/__init__.py', 'anki_study/bridge.py', 'anki_study/dashboard.py', 'anki_study/analytics.py', 'anki_study/activity_detail.py'):
        try:
            compile((root / name).read_text(), name, 'exec')
        except (OSError, SyntaxError) as error:
            errors.append(str(error))
    for name in ('anki_study/focus-stats.js', 'anki_study/focus-stats.css') + tuple(
            'anki_study/wallpaper-' + scene + '.png' for scene in ('dawn', 'day', 'evening', 'night')) + tuple(
            f'anki_study/wallpaper-{theme}-{scene}.png'
            for theme in ('skyline', 'sea', 'architecture', 'amg', 'rolls', 'goggins')
            for scene in ('predawn', 'dawn', 'morning', 'day', 'golden', 'evening', 'night')):
        if not (root / name).is_file():
            errors.append('Отсутствует ' + name)
    try:
        with zipfile.ZipFile(root / 'theme/focus-study.onigiri') as archive:
            json.loads(archive.read('theme.json'))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        errors.append('Тема: ' + str(error))
    if not args.source_only:
        if platform.system() != 'Darwin':
            errors.append('Установщик предназначен только для macOS.')
        if not (Path.home() / 'Library/Application Support/Anki2/addons21').is_dir():
            errors.append('Сначала установи Anki и открой его один раз.')
    instance = root / 'instance.json'
    if instance.exists():
        print('Слово:', json.loads(instance.read_text())['planner_origin'])
    elif not args.source_only:
        errors.append('Сначала создай комплект командой prepare_friend.py с адресом своего Слова.')
    for error in errors:
        print('Нужно исправить:', error)
    if not errors:
        print('Проверка пройдена. Учебный блок не запускался; настройки системы не менялись.')
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
