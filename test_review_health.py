"""Review defaults against Anki's real backend and disposable collections."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from anki_study import review_health as health
try:
    from anki.collection import Collection
except ImportError:
    Collection = None


@unittest.skipIf(Collection is None, 'Anki backend is not installed')
class ReviewHealthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.col = Collection(str(self.root / 'collection.anki2'))
        self.addCleanup(self.col.close)
        self.backups = self.root / 'new-parent' / 'review-backups'
        self.model = next(m for m in self.col.models.all() if m['type'] == 0)

    def card(self, tags=(), deck=1, queue=0, due=0):
        note = self.col.new_note(self.model)
        note.fields[0] = 'Question ' + str(time.time_ns())
        note.fields[1] = 'Answer'
        note.tags = list(tags)
        self.col.add_note(note, deck)
        card = self.col.get_card(self.col.find_cards('nid:' + str(note.id))[0])
        card.queue = queue
        card.type = 2 if queue in (2, -1) else 0
        card.due = due
        if card.type == 2:
            card.ivl, card.reps, card.lapses = 12, 4, 2
        self.col.update_card(card)
        return card

    def schedule(self):
        return self.col.db.all('select id,nid,did,ord,type,queue,due,ivl,factor,reps,lapses,left,odue,odid,flags from cards order by id')

    def test_apply_preserves_cards_history_assignments_limits_and_optimized_weights(self):
        custom = self.col.decks.add_config('ML')
        defaults = self.col.decks.get_deck_configs_for_update(1).defaults.config
        custom['fsrsParams6'] = list(defaults.fsrs_params_6)
        custom['new']['perDay'] = 7
        custom['rev']['perDay'] = 345
        self.col.decks.update_config(custom)
        deck = self.col.decks.get(1)
        self.col.decks.set_config_id_for_deck_dict(deck, custom['id'])
        unused = self.col.decks.add_config('Unused')
        self.card(queue=2, due=self.col.sched.today - 3)
        self.card(queue=-1, tags=['leech'])
        self.card()
        before = self.schedule()
        notes = self.col.db.all('select id,flds,tags from notes order by id')
        self.assertTrue(health.apply_once(self.col, self.backups))
        self.assertEqual(self.schedule(), before)
        self.assertEqual(self.col.db.all('select id,flds,tags from notes order by id'), notes)
        self.assertEqual(self.col.db.scalar('select count(*) from revlog'), 0)
        self.assertEqual(self.col.decks.get(1)['conf'], custom['id'])
        saved = self.col.decks.get_config(custom['id'])
        self.assertEqual(len(saved['fsrsParams6']), len(custom['fsrsParams6']))
        for actual, expected in zip(saved['fsrsParams6'], custom['fsrsParams6']):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual((saved['new']['perDay'], saved['rev']['perDay']), (7, 345))
        self.assertEqual(saved['lapse']['leechFails'], 6)
        self.assertEqual(saved['lapse']['leechAction'], 0)
        self.assertTrue(saved['new']['bury'] and saved['rev']['bury'] and saved['buryInterdayLearning'])
        self.assertAlmostEqual(saved['desiredRetention'], .9)
        self.assertEqual(self.col.decks.get_config(unused['id'])['lapse']['leechFails'], 8)
        self.assertTrue(self.col.get_config('fsrs'))
        self.assertFalse(self.col.get_preferences().reviewing.show_intervals_on_buttons)
        backup = next(self.backups.glob('*.json'))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        # Subsequent user choices win, including on repeated startup.
        saved['lapse']['leechFails'] = 9
        self.col.decks.update_config(saved)
        self.assertFalse(health.apply_once(self.col, self.backups))
        self.assertEqual(self.col.decks.get_config(custom['id'])['lapse']['leechFails'], 9)
        self.assertEqual(len(list(self.backups.glob('*.json'))), 1)

    def test_failure_after_deck_write_restores_original_settings_and_allows_retry(self):
        before = copy.deepcopy(self.col.decks.all_config()[0])
        original = self.col.set_preferences
        calls = [0]
        def fail_once(prefs):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('disk write failed')
            return original(prefs)
        with patch.object(self.col, 'set_preferences', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'disk write failed'):
                health.apply_once(self.col, self.backups)
        restored = self.col.decks.all_config()[0]
        for key in ('lapse','new','rev','desiredRetention','buryInterdayLearning'):
            self.assertEqual(restored[key], before[key])
        self.assertFalse(self.col.get_config('fsrs'))
        self.assertIsNone(self.col.get_config(health.MARKER))
        self.assertTrue(health.apply_once(self.col, self.backups))

    def test_backup_failure_prevents_any_settings_write(self):
        with patch.object(health, '_write_backup', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                health.apply_once(self.col, self.backups)
        self.assertFalse(self.col.get_config('fsrs'))
        self.assertEqual(self.col.decks.all_config()[0]['lapse']['leechFails'], 8)
        self.assertIsNone(self.col.get_config(health.MARKER))

    def test_restore_only_touched_fields_keeps_later_user_choices_and_new_presets(self):
        self.card(queue=-1)
        schedule = self.schedule()
        health.apply_once(self.col, self.backups)
        config = self.col.decks.get_config(1)
        config['new']['perDay'] = 11
        self.col.decks.update_config(config)
        prefs = self.col.get_preferences()
        prefs.reviewing.show_remaining_due_counts = False
        self.col.set_preferences(prefs)
        new = self.col.decks.add_config('Added later')
        health.restore_settings(self.col, self.backups)
        self.assertFalse(self.col.get_config('fsrs'))
        self.assertEqual(self.col.decks.get_config(1)['lapse']['leechFails'], 8)
        self.assertEqual(self.col.decks.get_config(1)['new']['perDay'], 11)
        self.assertTrue(self.col.get_preferences().reviewing.show_intervals_on_buttons)
        self.assertFalse(self.col.get_preferences().reviewing.show_remaining_due_counts)
        self.assertEqual(self.col.decks.get_config(new['id'])['lapse'], new['lapse'])
        self.assertEqual(self.schedule(), schedule)
        self.assertFalse(health.apply_once(self.col, self.backups))

    def test_snapshot_uses_anki_day_excludes_suspended_due_and_archived_repair_cards(self):
        today = self.col.sched.today
        self.card(queue=2, due=today-1, tags=['leech'])
        self.card(queue=2, due=today)
        self.card(queue=2, due=today+1)
        self.card(queue=-1, due=today-20, tags=[health.REPAIR_TAG])
        self.card(queue=0)
        archive = self.col.decks.id('Архив · старое')
        self.card(queue=-1, deck=archive, tags=['leech'])
        self.col.set_config('slovo_archive', {'name': 'Архив · старое', 'deck_id': archive})
        schedule = self.schedule()
        data = health.snapshot(self.col)
        self.assertEqual((data['review_due'],data['overdue'],data['repair_count']), (2,1,2))
        self.assertIsNone(data['retention'])
        self.assertEqual(self.schedule(), schedule)

    def test_retention_only_grades_scheduled_reviews_in_rolling_30_days(self):
        card = self.card()
        now = time.time()
        rows = [(1,1,0), (1,2,1), (1,3,2), (1,4,3), (0,3,4),
                (2,1,5), (3,3,6), (1,0,7), (1,1,31*86400), (1,1,-100)]
        for kind,ease,age in rows:
            self.col.db.execute('insert into revlog values (?,?,?,?,?,?,?,?,?)',
                int((now-age)*1000),card.id,-1,ease,1,1,2500,1000,kind)
        data = health.snapshot(self.col, now)
        self.assertEqual((data['review_answers'],data['recalled'],data['retention']), (4,3,75.0))

    def test_missing_local_backup_does_not_change_settings(self):
        health.apply_once(self.col, self.backups)
        next(self.backups.glob('*.json')).unlink()
        with self.assertRaises(FileNotFoundError):
            health.restore_settings(self.col, self.backups)
        self.assertTrue(self.col.get_config('fsrs'))
