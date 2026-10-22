#!/usr/bin/env python3
"""Create a clean, separate Mac installation for an explicitly chosen planner."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit, urlunsplit
import zipfile

ROOT = Path(__file__).resolve().parent
ORIGINAL_ORIGIN = 'https://slovo.45-80-228-215.sslip.io'
ADDON_FILES = ('__init__.py', 'bridge.py', 'dashboard.py', 'analytics.py', 'activity_detail.py', 'review_health.py',
               'focus-stats.js', 'focus-stats.css', 'manifest.json') + tuple(
                   'wallpaper-' + scene + '.png' for scene in ('dawn', 'day', 'evening', 'night')) + tuple(
                   f'wallpaper-{theme}-{scene}.png'
                   for theme in ('skyline', 'sea', 'architecture', 'amg', 'rolls', 'goggins')
                   for scene in ('predawn', 'dawn', 'morning', 'day', 'golden', 'evening', 'night'))
SOURCE_FILES = ('README.md', 'AGENTS.md', 'START_HERE.md', 'CODEX_SETUP.md', 'doctor.py',
                'install.py', 'install-study.command', 'wait_study_idle.py', 'repair-study.command', 'repair-study.applescript',
                'focus-helper', 'focus_open.py', 'focus_youtube_guard.py', 'focus_service_policy.py',
                'focus_playlist_sync.py', 'focus_study.py', 'youtube-policy.json',
                'theme/focus-study.onigiri')


def origin(value):
    p = urlsplit(value)
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password
            or p.path not in ('', '/') or p.query or p.fragment or '\\' in value
            or any(c.isspace() for c in value)):
        raise ValueError('Нужен адрес Слова без пути, пароля и параметров: https://your-domain.example')
    host = p.hostname.encode('idna').decode('ascii').lower()
    if not re.fullmatch(r'[a-z0-9.-]+|::1', host) or '..' in host:
        raise ValueError('Некорректное имя сервера')
    if p.scheme == 'http' and host not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Для удалённого Слова требуется HTTPS. HTTP разрешён только на этом Mac.')
    port = p.port  # Invalid port syntax is rejected here.
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Некорректный порт')
    netloc = ('[' + host + ']') if ':' in host else host
    if port is not None:
        netloc += ':' + str(port)
    return urlunsplit((p.scheme, netloc, '', '', ''))


def prepare(planner, destination, source=ROOT):
    planner = origin(planner)
    destination = Path(destination).resolve()
    source = Path(source).resolve()
    if destination == source or destination in source.parents or destination.exists():
        raise ValueError('Нужен новый отдельный каталог. Существующие файлы не перезаписываются.')
    names = list(SOURCE_FILES) + ['anki_study/' + name for name in ADDON_FILES]
    # Deliberate allowlist: no user_files, collection, .git, .env, logs or private state.
    for name in names:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Отсутствует обычный файл: ' + name)
    destination.mkdir(mode=0o700, parents=True)
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        target.chmod(0o755 if name.endswith('.command') or name == 'focus-helper' else 0o600)
    for name in ('anki_study/bridge.py', 'focus_study.py'):
        path = destination / name
        text = path.read_text()
        if text.count(ORIGINAL_ORIGIN) != 1:
            raise ValueError('Изменился шаблон адреса: ' + name)
        path.write_text(text.replace(ORIGINAL_ORIGIN, planner))
    for name in ('anki_study/bridge.py', 'focus_study.py', 'install.py'):
        compile((destination / name).read_text(), name, 'exec')
    (destination / 'instance.json').write_text(json.dumps({'schema': 1, 'planner_origin': planner,
                                                         'personal_data_included': False}, indent=2) + '\n')
    addon = destination / 'slovo-study-blocks.ankiaddon'
    with zipfile.ZipFile(addon, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ADDON_FILES:
            archive.write(destination / 'anki_study' / name, name)
    entries = sorted(p for p in destination.rglob('*') if p.is_file())
    (destination / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(destination).as_posix() + '\n'
        for path in entries))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slovo-origin', required=True, help='Свой HTTPS-адрес Слова или http://127.0.0.1:3000 для локального запуска')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/friend-setup')
    args = parser.parse_args()
    try:
        destination = prepare(args.slovo_origin, args.output)
    except ValueError as error:
        parser.error(str(error))
    print('Готов чистый комплект:', destination)
    print('Слово:', origin(args.slovo_origin))
    print('Ничего не установлено. Следующий шаг: CODEX_SETUP.md.')


if __name__ == '__main__':
    main()
