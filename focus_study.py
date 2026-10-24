"""Protected study blocks. Mutating callers must hold the helper's file lock.

No UI state, planner file or Anki configuration is an authority after start.
All durations and emergency rules live in this root-owned module.
"""
import copy
import json
import math
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from focus_service_policy import day_at
from focus_youtube_guard import atomic_json

BASE = Path('/Library/PrivilegedHelperTools/focus-open')
STATE = BASE / 'study.json'
FOCUS = 80 * 60
EMERGENCY_WAIT = 0
CONFIRMATION = 'ПОДТВЕРЖДАЮ'
PREPARE = 300
# Compatibility for saved sessions/history; new UI uses explicit timing.
PRESETS = {'deep80': (4800, 600, 1800, 2),
           'pomo20': (1200, 300, 1800, 4),
           'pomo25': (1500, 300, 1800, 4),
           'pomo30': (1800, 300, 1800, 4),
           'pomo50': (3000, 420, 1800, 2)}
ESSENTIAL_APPS = frozenset({
    'net.ankiweb.anki', 'com.apple.Photos', 'com.apple.finder',
    'com.apple.systemuiserver', 'com.apple.dock', 'com.apple.controlcenter',
    'com.apple.notificationcenterui', 'com.apple.loginwindow',
    'com.apple.SecurityAgent', 'com.apple.ScreenSaver.Engine',
    'com.apple.systempreferences', 'com.apple.Spotlight',
})
# Exact bundle IDs, never names, category metadata or a user-editable claim
# that an app is educational. Browsers, terminals, media and general messengers
# deliberately still require selection before focus. This permits tools, not
# a claim that everything done inside a tool is study.
AUTO_STUDY_APPS = {
    'us.zoom.xos': 'Zoom',
    'com.microsoft.VSCode': 'Visual Studio Code',
    'com.jetbrains.CLion': 'CLion',
    'com.jetbrains.pycharm': 'PyCharm',
    'com.apple.dt.Xcode': 'Xcode',
    'com.goodnotesapp.x': 'Goodnotes',
    'com.apple.Notes': 'Заметки',
    'com.apple.Preview': 'Просмотр PDF',
    'com.apple.TextEdit': 'TextEdit',
    'com.apple.iWork.Pages': 'Pages',
    'com.apple.Pages': 'Pages Creator Studio',
    'com.apple.iWork.Numbers': 'Numbers',
    'com.apple.iWork.Keynote': 'Keynote',
    'com.apple.calculator': 'Калькулятор',
    'com.apple.Dictionary': 'Словарь',
    'com.apple.grapher': 'Grapher',
    'com.softissimo.ReversoContext.macosapp': 'Reverso',
    'ru.hse.HSEAppX': 'HSE App X',
}
BUNDLE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,199}\Z')
DISTRACTION_HOSTS = ('tiktok.com', 'twitch.tv', 'reddit.com', 'netflix.com',
                     'rutube.ru', 'dzen.ru', 'ok.ru', 'facebook.com', '9gag.com')
DISTRACTION_DOMAINS = tuple(host for domain in DISTRACTION_HOSTS for host in (domain, 'www.' + domain, 'm.' + domain))


def distraction_site(url):
    host = (urlparse(url).hostname or '').lower().rstrip('.')
    return next((domain for domain in DISTRACTION_HOSTS if host == domain or host.endswith('.' + domain)), None)


def read():
    if not STATE.exists():
        return {'schema': 1, 'session': None, 'history': [], 'last_emergency': None}
    value = json.loads(STATE.read_text())
    if (not isinstance(value, dict) or value.get('schema') != 1 or
            not isinstance(value.get('history'), list) or 'session' not in value or
            'last_emergency' not in value):
        raise RuntimeError('Повреждено защищённое состояние учёбы. Автосброс запрещён.')
    return value


def save(value):
    atomic_json(STATE, value)


def clock():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def valid_text(value, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError('Некорректный текст')
    return value.strip()


def completed_today(value, now):
    return sum(item['outcome'] == 'completed' and day_at(item['ended_at']) == day_at(now)
               for item in value['history'])


def emergency_available(value, now):
    last = value['last_emergency']
    # Both a new Moscow date and 20 real hours are needed. Midnight is not
    # an immediate second escape; backwards clock changes never refund one.
    return last is None or (day_at(now) > day_at(last) and now - last >= 20 * 3600)


def duration(session):
    return session.get('focus_seconds', FOCUS)


def clean_timing(value):
    if not isinstance(value, dict) or set(value) != {'focus', 'short', 'long', 'every'}:
        raise ValueError('Задай учёбу, короткий и длинный отдых и частоту длинного отдыха.')
    for key, maximum in (('focus', 240), ('short', 60), ('long', 180), ('every', 12)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError('Некорректная длительность или частота отдыха.')
    if value['long'] < value['short']:
        raise ValueError('Длинный отдых не может быть короче короткого.')
    return dict(value)


def timing_seconds(session):
    if 'timing' in session:
        t = clean_timing(session['timing'])
        return t['focus'] * 60, t['short'] * 60, t['long'] * 60, t['every']
    return PRESETS[session.get('preset', 'deep80')]


def new_focus(previous, now, mono, boot):
    result = copy.deepcopy(previous)
    for key in ('awaiting_rest', 'overtime_seconds', 'overtime_anchor_mono', 'app_guard_error',
                'break_kind', 'break_seconds', 'break_shortened'):
        result.pop(key, None)
    result.update(id=secrets.token_hex(16), phase='focus', started_at=now, boot=boot,
                  remaining=duration(result), deadline_mono=mono + duration(result),
                  ends_at=now + duration(result), emergency=None, attempts={}, app_attempts={})
    return result


def advance(value, now, mono, boot):
    session = value['session']
    if not session:
        return False
    if session['boot'] != boot:
        # Reboot resumes the last saved remaining time, rather than earning
        # credit while the machine was off or silently cancelling a block.
        session.update(boot=boot, deadline_mono=mono + session['remaining'])
        if session.get('awaiting_rest'):
            session['overtime_anchor_mono'] = mono
        if session.get('emergency'):
            session['emergency'].update(boot=boot, ready_mono=mono + EMERGENCY_WAIT)
    session['remaining'] = max(0, session['deadline_mono'] - mono)
    session['ends_at'] = now + session['remaining']
    if session.get('emergency') and mono > session['emergency']['ready_mono'] + 120:
        session['emergency'] = None
    if session['phase'] == 'ready':
        session.update(remaining=0, deadline_mono=mono, ends_at=now)
        return True
    if session.get('awaiting_rest'):
        session['overtime_seconds'] += max(0, mono - session['overtime_anchor_mono'])
        session['overtime_anchor_mono'] = mono
        return True
    if session['remaining'] > 0:
        return True
    if session['phase'] == 'focus':
        # Completion of the minimum is not permission to start the break.
        # Keep the committed app/site restrictions until explicit confirmation.
        session.update(awaiting_rest=True,
                       overtime_seconds=max(0, mono - session['deadline_mono']),
                       overtime_anchor_mono=mono, emergency=None)
    elif session['phase'] == 'prepare':
        # One fresh block only; a stopped daemon never earns unattended blocks.
        value['session'] = new_focus(session, now, mono, boot)
    elif session.get('auto_continue') and session.get('blocks_left', 1) > 1:
        session.update(phase='ready', remaining=0, deadline_mono=mono, ends_at=now,
                       emergency=None, blocks_left=session['blocks_left'] - 1,
                       cycle_index=session.get('cycle_index', 1) + 1)
    else:
        value['session'] = None
    return True


def start_break(request, boot, now=None, mono=None):
    """End this focus block and start its full break, once and only once."""
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    if not isinstance(request, dict) or set(request) != {'session_id'}:
        raise ValueError('Нужен идентификатор блока')
    value = read()
    advance(value, now, mono, boot)
    session = value['session']
    if not session or session['id'] != request['session_id']:
        raise RuntimeError('Блок уже изменился. Обнови состояние.')
    if session['phase'] == 'break':
        save(value)
        return copy.deepcopy(session)  # Retrying never resets the rest deadline.
    if not session.get('awaiting_rest'):
        save(value)
        raise RuntimeError('Сначала заверши учебный блок.')
    extra = session['overtime_seconds']
    ended = {k: copy.deepcopy(session[k]) for k in
             ('id', 'task', 'apps', 'started_at', 'attempts', 'app_attempts')}
    ended.update(ended_at=now, outcome='completed', seconds=duration(session) + extra,
                 overtime_seconds=extra, focus_seconds=duration(session), preset=session.get('preset', 'deep80'))
    if 'timing' in session:
        ended['timing'] = dict(session['timing'])
    value['history'] = (value['history'] + [ended])[-1000:]
    _, short, long, every = timing_seconds(session)
    index = session.get('cycle_index', completed_today(value, now))
    rest = long if index % every == 0 else short
    session.update(phase='break', remaining=rest, deadline_mono=mono + rest,
                   ends_at=now + rest, emergency=None, awaiting_rest=False,
                   break_kind='long' if index % every == 0 else 'short',
                   break_seconds=rest, break_shortened=False)
    save(value)
    return copy.deepcopy(session)


def shorten_break(request, boot, now=None, mono=None):
    """Use the committed short duration measured from the original rest start."""
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    if not isinstance(request, dict) or set(request) != {'session_id'}:
        raise ValueError('Нужен идентификатор блока')
    value = read()
    advance(value, now, mono, boot)
    session = value['session']
    if not session or session['id'] != request['session_id']:
        save(value)
        raise RuntimeError('Блок уже изменился. Обнови состояние.')
    if session.get('break_shortened') and session['phase'] in ('break', 'ready'):
        save(value)
        return {'session': copy.deepcopy(session), 'shortened': True}
    if session['phase'] != 'break' or session.get('break_kind') != 'long':
        save(value)
        raise RuntimeError('Сократить до короткого можно только длинный отдых.')
    short = timing_seconds(session)[1]
    elapsed = max(0, session['break_seconds'] - session['remaining'])
    remaining = max(0, short - elapsed)
    session.update(break_kind='short', break_seconds=short, break_shortened=True,
                   remaining=remaining, deadline_mono=mono + remaining, ends_at=now + remaining)
    advance(value, now, mono, boot)
    save(value)
    return {'session': copy.deepcopy(value['session']), 'shortened': True}


def status(boot, now=None, mono=None, *, advance_state=True):
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    value = read()
    if advance_state:
        if advance(value, now, mono, boot):
            save(value)
    elif value['session']:
        # Project only the display clock from the last atomic checkpoint.
        # Keep its committed phase/ID even past a deadline: a busy observer
        # cannot invent a new focus block or release mandatory rest. The next
        # locked action/daemon pass remains the authority for all transitions.
        session = value['session']
        if session['boot'] != boot:
            session['deadline_mono'] = mono + session['remaining']
            if session.get('awaiting_rest'):
                session['overtime_anchor_mono'] = mono
        session['remaining'] = max(0, session['deadline_mono'] - mono)
        session['ends_at'] = now + session['remaining']
        if session.get('awaiting_rest'):
            session['overtime_seconds'] += max(0, mono - session['overtime_anchor_mono'])
            session['overtime_anchor_mono'] = mono
    records = [item for item in value['history'] if day_at(item['ended_at']) == day_at(now)]
    if value['session'] and value['session']['phase'] == 'focus':
        records.append(value['session'])
    attempts = sum(sum(item.get('attempts', {}).values()) + sum(item.get('app_attempts', {}).values()) for item in records)
    return dict(copy.deepcopy(value), completed_today=completed_today(value, now),
                attempts_today=attempts, emergency_available=emergency_available(value, now), now=now,
                auto_study_apps=dict(AUTO_STUDY_APPS),
                supported_study_presets=list(PRESETS), prepare_seconds=PREPARE,
                supports_custom_timing=True, supports_break_shorten=True)


def clean_task(task, grouped=True):
    allowed = {'title', 'url', 'occurrence_id'} | ({'items'} if grouped else set())
    if not isinstance(task, dict) or set(task) - allowed or 'title' not in task:
        raise ValueError('Некорректная задача')
    clean = {'title': valid_text(task['title'], 300)}
    if task.get('url'):
        url = valid_text(task['url'], 2048)
        if not url.startswith('https://slovo.45-80-228-215.sslip.io/'):
            raise ValueError('Ссылка должна вести в Слово')
        clean['url'] = url
    if task.get('occurrence_id'):
        clean['occurrence_id'] = valid_text(task['occurrence_id'], 100)
    if 'items' in task:
        items = task['items']
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise ValueError('Выбери от 1 до 30 задач')
        clean['items'] = [clean_task(item, grouped=False) for item in items]
        ids = [item['occurrence_id'] for item in clean['items'] if 'occurrence_id' in item]
        if len(set(ids)) != len(ids):
            raise ValueError('Задачи повторяются')
    return clean


def start(request, boot, now=None, mono=None):
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    if not isinstance(request, dict) or not {'task', 'apps'} <= set(request) or set(request) - {'task', 'apps', 'preset', 'timing', 'auto_continue', 'blocks', 'blackout'}:
        raise ValueError('Некорректные настройки блока')
    timing = clean_timing(request['timing']) if 'timing' in request else None
    if timing is not None and 'preset' in request:
        raise ValueError('Выбери собственные длительности без готового режима.')
    preset = request.get('preset', 'deep80')
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError('Неизвестный режим фокуса')
    auto = request.get('auto_continue', False)
    blocks = request.get('blocks', 1)
    blackout = request.get('blackout', False)
    if type(auto) is not bool or type(blackout) is not bool or type(blocks) is not int or not 1 <= blocks <= 24:
        raise ValueError('Выбери от 1 до 24 блоков в серии')
    clean = clean_task(request['task'])
    apps = request['apps']
    if (not isinstance(apps, list) or len(apps) > 100 or
            any(not isinstance(app, str) or not BUNDLE_ID.fullmatch(app) for app in apps)):
        raise ValueError('Выбери приложения из списка')
    value = read()
    advance(value, now, mono, boot)
    if value['session']:
        save(value)
        raise RuntimeError('Текущий блок или обязательный перерыв ещё идёт. Изменения закрыты.')
    seconds = timing['focus'] * 60 if timing else PRESETS[preset][0]
    value['session'] = {
        'id': secrets.token_hex(16), 'phase': 'focus', 'task': clean,
        'apps': sorted(set(apps) | ESSENTIAL_APPS), 'started_at': now,
        'boot': boot, 'deadline_mono': mono + seconds, 'remaining': seconds,
        'ends_at': now + seconds, 'emergency': None,
        'preset': 'custom' if timing else preset, 'focus_seconds': seconds, 'auto_continue': auto,
        'blocks_left': blocks if auto else 1, 'blocks_total': blocks if auto else 1,
        'blackout': blackout, 'series_id': secrets.token_hex(16),
        'attempts': {}, 'app_attempts': {},
    }
    if timing:
        value['session']['timing'] = timing
    # Legacy 80-minute single blocks retain the daily alternating rest cycle.
    if auto:
        value['session']['cycle_index'] = 1
    elif timing:
        value['session']['cycle_index'] = 1 + sum(
            item['outcome'] == 'completed' and item.get('timing') == timing and
            day_at(item['ended_at']) == day_at(now) for item in value['history'])
    elif preset != 'deep80' or 'preset' in request:
        value['session']['cycle_index'] = 1 + sum(
            item['outcome'] == 'completed' and item.get('preset', 'deep80') == preset and
            day_at(item['ended_at']) == day_at(now) for item in value['history'])
    save(value)
    return copy.deepcopy(value['session'])


def next_block(request, boot, now=None, mono=None, action='ready'):
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    keys = {'session_id'} | ({'task', 'apps'} if action in ('update', 'start') else set())
    if not isinstance(request, dict) or set(request) != keys:
        raise ValueError('Некорректные настройки следующего блока')
    value = read()
    advance(value, now, mono, boot)
    session = value['session']
    if not session or session['id'] != request['session_id']:
        save(value)
        raise RuntimeError('Блок уже изменился. Обнови состояние.')
    if action == 'ready':
        if session['phase'] == 'ready':
            session.update(phase='prepare', remaining=PREPARE, deadline_mono=mono + PREPARE, ends_at=now + PREPARE)
        elif session['phase'] != 'prepare':
            save(value)
            raise RuntimeError('Сначала дождись окончания отдыха.')
    else:
        if session['phase'] != 'prepare':
            save(value)
            raise RuntimeError('Менять задачи можно только в пятиминутном окне подготовки.')
        task = clean_task(request['task'])
        apps = request['apps']
        if not isinstance(apps, list) or len(apps) > 100 or any(not isinstance(app, str) or not BUNDLE_ID.fullmatch(app) for app in apps):
            raise ValueError('Выбери приложения из списка')
        session.update(task=task, apps=sorted(set(apps) | ESSENTIAL_APPS))
        if action == 'start':
            value['session'] = new_focus(session, now, mono, boot)
    save(value)
    return copy.deepcopy(value['session'])


def require_focus(value, session_id):
    session = value['session']
    if not session or session['id'] != session_id:
        raise RuntimeError('Блок уже изменился. Обнови состояние.')
    return session


def request_emergency(request, boot, now=None, mono=None):
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    if not isinstance(request, dict) or set(request) != {'session_id', 'reason'}:
        raise ValueError('Нужны блок и причина')
    reason = valid_text(request['reason'], 500)
    value = read()
    advance(value, now, mono, boot)
    session = require_focus(value, request['session_id'])
    if not emergency_available(value, now):
        raise RuntimeError('Экстренный выход уже использован: один раз в день, минимум 20 часов между выходами.')
    if not session['emergency']:
        session['emergency'] = {'reason': reason, 'boot': boot,
                                'ready_mono': mono + EMERGENCY_WAIT}
    save(value)
    return {'wait_seconds': max(0, math.ceil(session['emergency']['ready_mono'] - mono))}


def confirm_emergency(request, boot, now=None, mono=None):
    now = time.time() if now is None else now
    mono = clock() if mono is None else mono
    if not isinstance(request, dict) or set(request) != {'session_id', 'confirmation'}:
        raise ValueError('Некорректное подтверждение')
    value = read()
    advance(value, now, mono, boot)
    session = require_focus(value, request['session_id'])
    pending = session['emergency']
    if (request['confirmation'] != CONFIRMATION or not pending or
            pending['boot'] != boot or mono < pending['ready_mono']):
        raise RuntimeError('Подтверди прерывание словом: ' + CONFIRMATION)
    if not emergency_available(value, now):
        raise RuntimeError('Сегодня экстренный выход уже использован.')
    value['last_emergency'] = now
    if session['phase'] == 'focus':
        value['history'].append({k: copy.deepcopy(session[k]) for k in
                                 ('id', 'task', 'apps', 'started_at', 'attempts', 'app_attempts')})
        value['history'][-1].update(ended_at=now, outcome='interrupted',
                                  reason=pending['reason'], focus_seconds=duration(session),
                                  seconds=duration(session) - session['remaining'] + session.get('overtime_seconds', 0))
    value['history'] = value['history'][-1000:]
    value['session'] = None
    save(value)
    return {'stopped': True, 'outcome': 'interrupted'}


def record_attempt(kind, identity):
    value = read()
    session = value['session']
    if session and session['phase'] == 'focus':
        bucket = session['app_attempts' if kind == 'app' else 'attempts']
        # Aggregate by app/service only; URLs, page titles, keystrokes and
        # screenshots are deliberately absent from protected telemetry.
        identity = valid_text(identity, 200)
        bucket[identity] = bucket.get(identity, 0) + 1
        save(value)


def app_health(error):
    value = read()
    session = value['session']
    if session and session.get('app_guard_error') != error:
        session['app_guard_error'] = error
        save(value)
