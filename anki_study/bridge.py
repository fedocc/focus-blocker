"""Anki-side read-only planner client and fixed-scope helper transport."""
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time
import urllib.request
from xml.parsers.expat import ExpatError

ORIGIN = 'https://slovo.45-80-228-215.sslip.io'
HELPER = '/Library/PrivilegedHelperTools/focus-open/focus-helper'
COMMANDS = {'study-status', 'study-start', 'study-break-start', 'study-break-shorten', 'study-next-ready', 'study-next-update', 'study-next-start', 'study-emergency-request', 'study-emergency-confirm'}
# Legacy values only for migrating preferences and displaying old sessions.
PRESETS = {'deep80': (80, 10, 30, 2), 'pomo20': (20, 5, 30, 4),
           'pomo25': (25, 5, 30, 4), 'pomo30': (30, 5, 30, 4),
           'pomo50': (50, 7, 30, 2)}


def timing_settings(value=None):
    if isinstance(value, dict):
        if 'timing' in value:
            return dict(value['timing'])
        if set(value) == {'focus', 'short', 'long', 'every'}:
            return dict(value)
        preset = value.get('preset', value.get('focus_mode', 'deep80' if 'phase' in value else None))
    else:
        preset = value
    return dict(zip(('focus', 'short', 'long', 'every'), PRESETS.get(preset, (50, 7, 20, 2))))


def cycle_text(value):
    t = timing_settings(value)
    return f'{t["focus"]} мин учёбы · {t["short"]} мин отдыха · {t["long"]} мин после {t["every"]} блоков'


def series_plan(timing, blocks=4, auto=True, blackout=True, state=None):
    """Read-only preview of the service's committed phases and manual gates."""
    state = state or {}
    session = state.get('session')
    t = timing_settings(session or timing)
    prepare = state.get('prepare_seconds', 120 if state else 300)
    position, first_cycle = 0, 1
    if session:
        auto = session.get('auto_continue', False)
        blackout = session.get('blackout', False)
        blocks = session.get('blocks_total', 1) if auto else 1
        position = blocks - session.get('blocks_left', blocks)
        legacy_cycle = max(1,state.get('completed_today',0)+(session.get('phase')=='focus'))
        first_cycle = session.get('cycle_index', legacy_cycle) - position
    elif not auto:
        # Single-block mode continues the daily rest cadence for this timing.
        moscow = dt.timezone(dt.timedelta(hours=3))
        day = dt.datetime.fromtimestamp(state.get('now', time.time()), moscow).date()
        first_cycle += sum(item.get('outcome') == 'completed' and
                           dt.datetime.fromtimestamp(item['ended_at'], moscow).date() == day and
                           item.get('timing') == t for item in state.get('history', []))
    blocks = max(1, min(24, int(blocks))) if auto else 1
    nodes = []

    def add(key, kind, title, detail, minutes=0, active=False, **extra):
        nodes.append(dict(id=key, kind=kind, title=title, detail=detail,
                          minutes=minutes, active=active, **extra))

    add('start', 'manual', 'Старт — по кнопке',
        'Ты выбираешь задачи и приложения и подтверждаешь начало серии. До старта время свободное.')
    phase = (session or {}).get('phase')
    for i in range(blocks):
        current = bool(session and position == i)
        long_rest = (first_cycle + i) % t['every'] == 0
        rest = t['long'] if long_rest else t['short']
        shortened = bool(current and phase == 'break' and session.get('break_shortened'))
        if shortened:
            rest = t['short']
        focus_active = current and phase == 'focus' and not session.get('awaiting_rest')
        add(f'focus:{i}', 'focus', f'Бот {i+1} · {t["focus"]} мин',
            f'Приложение отсчитывает минимум {t["focus"]} мин. Задачи и разрешения зафиксированы. '
            'После сигнала можно продолжать: дополнительное время учитывается до кнопки «Начать отдых».',
            t['focus'], focus_active, block=i+1)
        add(f'rest-start:{i}', 'manual', 'Начать отдых — решаешь ты',
            'Автоматического ухода на отдых нет. После минимума бот продолжается с ограничениями, '
            'пока ты не нажмёшь «Начать отдых». Эта пауза не ограничена по времени.',
            active=bool(current and phase == 'focus' and session.get('awaiting_rest')))
        rest_kind = 'Сокращённый отдых' if shortened else 'Длинный отдых' if long_rest else 'Короткий отдых'
        detail = (f'Приложение отсчитывает {rest} мин. ' +
                  ('Чёрный экран и возврат в Anki включены. ' if blackout else
                   'Чёрный экран выключен: приложения доступны, обычная блокировка сайтов остаётся. '))
        if long_rest and not shortened and t['long'] > t['short']:
            detail += f'Можно сократить до {t["short"]} мин; уже прошедший отдых учитывается. '
        if shortened:
            detail += 'Уже прошедшее время зачтено; новый перерыв с нуля не запускается. '
        add(f'rest:{i}', 'rest', f'{rest_kind} · {rest} мин', detail,
            rest, current and phase == 'break', block=i+1, long=long_rest, shortened=shortened, protected=blackout)
        if i < blocks - 1:
            add(f'return:{i}', 'manual', 'Возвращение — без лимита времени',
                'После таймера отдых ждёт кнопки «Я вернулся». Следующий бот сам не начинается. ' +
                ('Чёрный экран остаётся до подтверждения.' if blackout else 'Чёрный экран выключен.'),
                active=bool(session and phase == 'ready' and position == i+1))
            add(f'prepare:{i+1}', 'prepare', f'Выбор задач · {prepare // 60} мин',
                f'На задачи и приложения — до {prepare // 60} мин. Можно нажать «Начать сейчас». '
                'Если ничего не менять, приложение само запустит следующий бот с прежним выбором. '
                'Это подготовка с ограничениями приложений, а не свободное экранное время.',
                prepare / 60, bool(session and phase == 'prepare' and position == i+1))
            add(f'auto:{i+1}', 'automatic', 'Следующий бот — автоматически',
                'По окончании подготовки приложение начинает следующий блок. Повторное подтверждение не продлевает выбор.')
        else:
            add('end', 'automatic', 'Конец серии — свободное время',
                'После последнего отдыха серия заканчивается автоматически. Чёрный экран и ограничения серии снимаются; '
                'обычные правила блокировщика сайтов сохраняются. Новая серия сама не запускается.')
    active = next((node for node in nodes if node['active']), None)
    if active and active['kind'] in ('focus', 'rest', 'prepare'):
        seconds = active['minutes'] * 60
        active['progress'] = min(1, max(0, 1 - remaining_seconds(state) / max(1, seconds)))
    return dict(nodes=nodes, blocks=blocks, focus_minutes=t['focus'] * blocks,
                timer_minutes=sum(node['minutes'] for node in nodes),
                active_id=active['id'] if active else None, committed=bool(session), blackout=blackout)


class PhaseAlerts:
    """Remember acknowledgements without ever writing protected timer state."""
    def __init__(self, preferences):
        self.preferences = preferences
        self.previous = None
        self.pending = None
        self.dirty = False
        self.initial = True

    def observe(self, state, now=None):
        now = time.time() if now is None else now
        session = state.get('session')
        if session and session['phase'] in ('ready', 'prepare'):
            self.previous = dict(session)
            self.pending = None
            self.initial = False
            return None
        previous = self.previous
        candidate = None
        if session and session.get('awaiting_rest'):
            candidate = {'id': session['id'], 'kind': 'overtime'}
        elif session and session['phase'] == 'break':
            candidate = {'id': session['id'], 'kind': 'break', 'ends_at': session['ends_at']}
            saved = self.preferences.get('alert_last_break', {})
            if saved.get('id') != session['id']:
                self.preferences['alert_last_break'] = dict(candidate)
                self.dirty = True
            if state.get('supports_confirmed_break'):
                # The user just explicitly started this break; don't alert twice.
                candidate = None
        elif not session:
            if previous and previous.get('phase') == 'break':
                candidate = {'id': previous['id'], 'kind': 'ready'}
            elif self.pending and self.pending['kind'] == 'ready':
                candidate = self.pending
            elif self.initial:
                saved = self.preferences.get('alert_last_break', {})
                if saved.get('id') and 0 <= now - saved.get('ends_at', 0) < 300:
                    candidate = {'id': saved['id'], 'kind': 'ready'}
        if candidate and self.preferences.get('alert_ack_' + candidate['kind']) == candidate['id']:
            candidate = None
        self.pending = candidate
        self.previous = dict(session) if session else None
        self.initial = False
        return candidate

    def acknowledge(self):
        if self.pending:
            self.preferences['alert_ack_' + self.pending['kind']] = self.pending['id']
            self.dirty = True
            self.pending = None


def play_alert_sound(kind='break'):
    """Normal media output: independent of notification banners and Focus mode."""
    sound = Path('/System/Library/Sounds') / ('Glass.aiff' if kind in ('break', 'overtime') else 'Hero.aiff')
    if not sound.is_file() or not Path('/usr/bin/afplay').is_file():
        return None
    return subprocess.Popen(['/usr/bin/afplay', '-v', '1', str(sound)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def remaining_seconds(state):
    """Use the service's system clock, independent of response latency and NTP."""
    session = (state or {}).get('session')
    if not session:
        return 4800
    deadline = session.get('deadline_mono')
    if isinstance(deadline, (int, float)):
        return max(0, deadline - time.clock_gettime(time.CLOCK_MONOTONIC))
    return max(0, session['remaining'])


def overtime_seconds(state):
    session = (state or {}).get('session') or {}
    if not session.get('awaiting_rest'):
        return 0
    anchor = session.get('overtime_anchor_mono')
    elapsed = max(0, time.clock_gettime(time.CLOCK_MONOTONIC) - anchor) if isinstance(anchor, (int, float)) else 0
    return max(0, session.get('overtime_seconds', 0)) + elapsed


def block_task(tasks, custom_title=''):
    if not tasks:
        return {'title': custom_title.strip()}
    if len(tasks) == 1:
        return {key: tasks[0][key] for key in ('title', 'url', 'occurrence_id')}
    if len(tasks) > 30:
        raise ValueError('В одном блоке можно выбрать до 30 задач')
    title = ' · '.join(task['title'] for task in tasks)
    if len(title) > 300:
        title = f'{len(tasks)} задач: ' + title[:280] + '…'
    return {'title': title, 'items': [{key: task[key] for key in ('title', 'url', 'occurrence_id')} for task in tasks]}


def archive_collection(col, backup):
    if col.get_config('slovo_archive'):
        raise ValueError('Архив уже существует')
    backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    col.create_backup(backup_folder=str(backup), force=True, wait_for_completion=True)
    roots = [{'id': int(d.id), 'name': d.name} for d in col.decks.all_names_and_ids()
             if '::' not in d.name and (d.id != 1 or col.db.scalar('select count() from cards where did=1'))]
    cards = list(col.find_cards('-is:suspended'))
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=3)))
    name = 'Архив · ' + today.strftime('%d.%m.%Y')
    if col.decks.by_name(name):
        raise ValueError('Колода с именем архива уже существует')
    entry = {'roots': roots, 'unsuspend_ids': cards, 'name': name, 'created': today.isoformat(), 'complete': False}
    col.set_config('slovo_archive', entry)
    if cards:
        col.sched.suspend_cards(cards)
    for deck in roots:
        col.decks.rename(deck['id'], name + '::' + deck['name'])
    archive_id = col.decks.id(name)
    deck = col.decks.get(archive_id)
    deck['collapsed'] = True
    col.decks.update(deck)
    entry.update(deck_id=int(archive_id), complete=True)
    col.set_config('slovo_archive', entry)
    return len(cards)


def restore_collection(col):
    archive = col.get_config('slovo_archive')
    if not archive:
        return
    for deck in archive['roots']:
        existing = col.decks.by_name(deck['name'])
        if existing and int(existing['id']) != deck['id']:
            raise ValueError('Уже есть новая колода «' + deck['name'] + '». Переименуй её перед восстановлением архива.')
    for deck in archive['roots']:
        col.decks.rename(deck['id'], deck['name'])
    live = set(col.find_cards('is:suspended'))
    ids = [i for i in archive['unsuspend_ids'] if i in live]
    if ids:
        col.sched.unsuspend_cards(ids)
    col.remove_config('slovo_archive')


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


def task_display_title(task):
    """Hide only a redundant weekday occurrence label; leave the saved title intact."""
    title = task['title']
    head, separator, tail = title.rpartition(' · ')
    weekdays = {'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'}
    return head if separator and head and tail.casefold() in weekdays else title


WALLPAPER_THEMES = {
    'skyline': 'Небоскрёбы', 'sea': 'Море', 'architecture': 'Архитектура в горах',
    'amg': 'Mercedes-AMG GT', 'rolls': 'Rolls-Royce', 'goggins': 'Дэвид Гоггинс',
    'landscape': 'Прежний пейзаж',
}
WALLPAPER_PERIODS = {
    'predawn': 'Предрассвет · 05–07', 'dawn': 'Рассвет · 07–10',
    'morning': 'Утро · 10–13', 'day': 'Полдень · 13–16',
    'golden': 'Золотой час · 16–18', 'evening': 'Закат · 18–21',
    'night': 'Ночь · 21–05',
}


def wallpaper_scene(hour=None):
    hour = dt.datetime.now().astimezone().hour if hour is None else hour
    for start, end, scene in ((5, 7, 'predawn'), (7, 10, 'dawn'), (10, 13, 'morning'),
                              (13, 16, 'day'), (16, 18, 'golden'), (18, 21, 'evening')):
        if start <= hour < end:
            return scene
    return 'night'


def wallpaper_settings(preferences, hour=None):
    theme = preferences.get('wallpaper_theme', 'skyline')
    if theme not in WALLPAPER_THEMES:
        theme = 'skyline'
    period = preferences.get('wallpaper_period', 'auto')
    scene = period if period in WALLPAPER_PERIODS else wallpaper_scene(hour)
    if theme == 'landscape':
        legacy = {'predawn': 'night', 'morning': 'day', 'golden': 'evening'}.get(scene, scene)
        path = f'wallpaper-{legacy}.png'
    else:
        path = f'wallpaper-{theme}-{scene}.png'
    return {'theme': theme, 'scene': scene, 'path': path}


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
                found[bundle] = ('Visual Studio Code' if bundle == 'com.microsoft.VSCode' else
                                 name if isinstance(name, str) and name.strip() else path.stem)
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
