"""One-time review defaults and read-only collection health, using Anki APIs."""
import base64
import copy
import json
import os
from pathlib import Path
import time
import uuid

MARKER = 'slovo_review_defaults_v1'
REPAIR_TAG = 'slovo::repair'


def repair_query(col):
    query = f'(tag:leech OR tag:{REPAIR_TAG})'
    # The old suspended archive is deliberately outside the repair queue.
    archive = col.get_config('slovo_archive') or {}
    if archive.get('name'):
        query += ' -deck:' + json.dumps(archive['name'], ensure_ascii=False)
    return query


def _request(col):
    from anki.decks import UpdateDeckConfigs
    # Preserve selected preset, per-deck limits, custom scheduling and globals.
    target = next(d for d in col.decks.all() if not d.get('dyn'))
    current = col.decks.get_deck_configs_for_update(target['id'])
    configs = [copy.deepcopy(row.config) for row in current.all_config]
    configs.sort(key=lambda row: row.id == current.current_deck.config_id)
    return UpdateDeckConfigs(
        target_deck_id=target['id'], configs=configs,
        limits=current.current_deck.limits,
        card_state_customizer=current.card_state_customizer,
        new_cards_ignore_review_limit=current.new_cards_ignore_review_limit,
        apply_all_parent_limits=current.apply_all_parent_limits,
        fsrs=current.fsrs, fsrs_health_check=current.fsrs_health_check,
        fsrs_reschedule=False), current


def _encoded(message):
    return base64.b64encode(message.SerializeToString()).decode('ascii')


def _write_backup(folder, request, preferences, configured_ids):
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / ('review-settings-' + uuid.uuid4().hex + '.json')
    data = {'schema': 1, 'created_at': time.time(),
            'deck_request': _encoded(request), 'preferences': _encoded(preferences),
            'configured_ids': sorted(configured_ids)}
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(data, stream)
    return path


def apply_once(col, backup_folder):
    """No reschedule, optimization, card edits, unsuspension or deck moves."""
    if col.get_config(MARKER):
        return False
    before, current = _request(col)
    preferences = col.get_preferences()
    used = {row.config.id for row in current.all_config if row.use_count}
    used.add(1)  # Default for future decks.
    backup = _write_backup(backup_folder, before, preferences, used)
    request, updated = copy.deepcopy(before), copy.deepcopy(preferences)
    request.fsrs = True
    for row in request.configs:
        if row.id not in used:
            continue
        config = row.config
        config.desired_retention = 0.90
        config.leech_threshold = 6
        config.leech_action = 0  # DeckConfig.LeechAction: SUSPEND, not TAG_ONLY.
        config.bury_new = config.bury_reviews = config.bury_interday_learning = True
    updated.reviewing.show_intervals_on_buttons = False
    try:
        col.decks.update_deck_configs(request)
        col.set_preferences(updated)
        col.set_config(MARKER, {'version': 1, 'backup': backup.name, 'applied_at': time.time()})
    except Exception:
        # Revert both halves if either write fails. A failing rollback is surfaced
        # to the caller; the original serialized settings remain on disk.
        col.decks.update_deck_configs(before)
        col.set_preferences(preferences)
        raise
    return True


def restore_settings(col, backup_folder):
    from anki.decks import UpdateDeckConfigs
    from anki.collection import Preferences
    marker = col.get_config(MARKER) or {}
    name = marker.get('backup', '')
    if not name or Path(name).name != name:
        raise RuntimeError('Нет локальной резервной копии настроек.')
    data = json.loads((Path(backup_folder) / name).read_text())
    if data.get('schema') != 1:
        raise RuntimeError('Неизвестный формат резервной копии.')
    before = UpdateDeckConfigs.FromString(base64.b64decode(data['deck_request']))
    request, _ = _request(col)
    touched = set(data['configured_ids'])
    originals = {row.id: row.config for row in before.configs if row.id in touched}
    for row in request.configs:
        if row.id not in originals:
            continue  # A preset added after installation belongs to the user.
        for key in ('desired_retention', 'leech_threshold', 'leech_action',
                    'bury_new', 'bury_reviews', 'bury_interday_learning'):
            setattr(row.config, key, getattr(originals[row.id], key))
    request.fsrs = before.fsrs
    request.fsrs_reschedule = False
    original_prefs = Preferences.FromString(base64.b64decode(data['preferences']))
    preferences = col.get_preferences()
    preferences.reviewing.show_intervals_on_buttons = original_prefs.reviewing.show_intervals_on_buttons
    col.decks.update_deck_configs(request)
    col.set_preferences(preferences)
    col.set_config(MARKER, dict(marker, restored=True))  # Do not reapply next launch.


def snapshot(col, now=None):
    now = time.time() if now is None else now
    today = col.sched.today  # Anki's rollover/timezone, not UTC or focus midnight.
    review_due, overdue = col.db.first('''
        select coalesce(sum((case when odid != 0 then odue else due end) <= ?),0),
               coalesce(sum((case when odid != 0 then odue else due end) < ?),0)
        from cards where queue = 2''', today, today)
    # Count graded scheduled reviews only. Learning/relearning, preview/manual
    # entries (ease=0) and future clock anomalies do not enter the denominator.
    total, recalled = col.db.first('''
        select count(*), coalesce(sum(ease > 1),0) from revlog
        where type = 1 and ease between 1 and 4 and id >= ? and id <= ?''',
        int((now - 30 * 86400) * 1000), int(now * 1000))
    queue = repair_query(col)
    marker = col.get_config(MARKER) or {}
    return {'review_due': review_due, 'overdue': overdue,
            'review_answers': total, 'recalled': recalled,
            'retention': round(recalled / total * 100, 1) if total else None,
            'repair_count': len(col.find_cards(queue)), 'repair_query': queue,
            'fsrs': bool(col.get_config('fsrs')),
            'configured': bool(marker) and not marker.get('restored', False),
            'restored': bool(marker.get('restored')), 'loading': False}
