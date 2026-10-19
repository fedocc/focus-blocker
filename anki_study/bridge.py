"""Anki-side read-only planner client and fixed-scope helper transport."""
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import urllib.request
from xml.parsers.expat import ExpatError

ORIGIN = 'https://slovo.45-80-228-215.sslip.io'
HELPER = '/Library/PrivilegedHelperTools/focus-open/focus-helper'
COMMANDS = {'study-status', 'study-start', 'study-emergency-request', 'study-emergency-confirm'}


def helper(command, payload=None):
    if command not in COMMANDS:
        raise ValueError('Unsupported study command')
    result = subprocess.run(['/usr/bin/sudo', '-n', HELPER, command],
                            input=json.dumps(payload, ensure_ascii=False) if payload is not None else '',
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:500] or 'Обнови системную службу Focus Blocker.')
    return json.loads(result.stdout)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Слово перенаправляет запрос. Ключ никуда не передан.')


def plan(token, date=None):
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date().isoformat()
    date = date or today
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise ValueError('Некорректный день')
    if len(token) != 43 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in token):
        raise ValueError('Некорректный ключ Слова')
    request = urllib.request.Request(ORIGIN + '/api/focus/plan?date=' + date,
                                     headers={'Authorization': 'Bearer ' + token})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
        data = response.read(512001)
    if len(data) > 512000:
        raise RuntimeError('План слишком большой')
    value = json.loads(data)
    if value.get('schema') != 1 or not isinstance(value.get('tasks'), list) or len(value['tasks']) > 200:
        raise RuntimeError('Слово вернуло неизвестный формат плана')
    for task in value['tasks']:
        if (not isinstance(task, dict) or not isinstance(task.get('title'), str) or
                not 1 <= len(task['title']) <= 300 or not isinstance(task.get('occurrence_id'), str) or
                not isinstance(task.get('url'), str) or not task['url'].startswith(ORIGIN + '/')):
            raise RuntimeError('Слово вернуло некорректную задачу')
    return value


def applications(roots=None):
    found = {}
    if roots is None:
        roots = [Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications']
    for root in roots:
        try:
            paths = list(root.glob('*.app')) + list(root.glob('*/*.app'))
        except OSError:
            continue
        for path in paths:
            try:
                with (path / 'Contents/Info.plist').open('rb') as stream:
                    info = plistlib.load(stream)
                if not isinstance(info, dict):
                    continue
                bundle = info.get('CFBundleIdentifier')
                if not isinstance(bundle, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,199}', bundle):
                    continue
                name = info.get('CFBundleDisplayName') or info.get('CFBundleName')
                found[bundle] = name if isinstance(name, str) and name.strip() else path.stem
            except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
                continue
    return sorted(found.items(), key=lambda entry: entry[1].casefold())


def save_preferences(path, value):
    path.parent.mkdir(exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)
