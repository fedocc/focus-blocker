import datetime as dt
import tempfile
from pathlib import Path
import unittest

from anki_study.analytics import MOSCOW, REPAIR_REASON, UsageTracker, snapshot


def timestamp(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=MOSCOW).timestamp()


class AnalyticsTests(unittest.TestCase):
    def test_overtime_is_extra_time_in_one_block_and_survives_local_history(self):
        now = timestamp('2026-10-20T00:05:00')
        session = {'id': 'overtime', 'phase': 'focus', 'awaiting_rest': True, 'remaining': 0,
                   'overtime_seconds': 600}
        result = snapshot({'session': session}, now=now, overtime=660)
        self.assertEqual(sum(row['seconds'] for row in result['days'].values()), 5460)
        self.assertEqual(result['days']['2026-10-19']['overtime_seconds'], 360)
        self.assertEqual(result['days']['2026-10-20']['overtime_seconds'], 300)
        record = dict(session, seconds=5460, overtime_seconds=660, ended_at=now, outcome='completed')
        with tempfile.TemporaryDirectory() as folder:
            tracker = UsageTracker(Path(folder) / 'stats.sqlite3')
            tracker.remember([record])
            result = snapshot({'history': list(tracker.blocks.values()), 'session': dict(session, phase='break')}, now=now)
            self.assertEqual(len(result['records']), 1)
            self.assertEqual(result['records'][0]['overtime_seconds'], 660)
            self.assertEqual(result['days']['2026-10-20']['completed'], 1)
            self.assertEqual(sum(row['seconds'] for row in result['days'].values()), 5460)
            tracker.sample({'session': session}, ('code', 'VS Code'), now, 100)
            tracker.sample({'session': session}, ('code', 'VS Code'), now+5, 105)
            self.assertEqual(tracker.summary()['days']['2026-10-20']['code']['seconds'], 5)
            tracker.close()

    def test_technical_repair_is_kept_but_excluded_from_study_metrics(self):
        now = timestamp('2026-10-19T19:00:00')
        record = {'id': 'repair', 'outcome': 'interrupted', 'reason': REPAIR_REASON,
                  'seconds': 120, 'ended_at': now, 'app_attempts': {'code': 50}}
        result = snapshot({'history': [record]}, now=now)
        self.assertEqual(result['days'], {})
        self.assertEqual(result['records'][0]['outcome'], 'repair')

    def test_local_history_survives_service_history_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stats.sqlite3'
            tracker = UsageTracker(path)
            tracker.remember([{'id': 'old', 'outcome': 'completed', 'seconds': 4800,
                               'task': {'title': 'DL', 'url': 'private-url'}}])
            tracker.close()
            tracker = UsageTracker(path)
            tracker.remember([{'id': 'new', 'outcome': 'completed', 'seconds': 4800}])
            self.assertEqual(set(tracker.blocks), {'old', 'new'})
            self.assertNotIn('url', tracker.blocks['old']['task'])
            tracker.close()

    def test_midnight_splits_time_but_completion_belongs_to_end_day(self):
        end = timestamp('2026-10-20T00:30:00')
        item = {'id': 'a', 'ended_at': end, 'seconds': 4800, 'outcome': 'completed',
                'task': {'title': 'DL'}, 'attempts': {'youtube': 2}, 'app_attempts': {'telegram': 1}}
        result = snapshot({'history': [item, item]}, now=end)
        self.assertEqual(result['days']['2026-10-19']['seconds'], 3000)
        self.assertEqual(result['days']['2026-10-20']['seconds'], 1800)
        self.assertEqual(result['days']['2026-10-20']['completed'], 1)
        self.assertEqual(result['days']['2026-10-20']['attempts'], 3)
        self.assertEqual(result['streak'], 1)

    def test_live_timer_and_break_do_not_double_count(self):
        now = timestamp('2026-10-19T14:00:00')
        session = {'id': 'a', 'phase': 'focus', 'remaining': 3700, 'task': {'title': 'Две задачи',
                   'items': [{'title': 'Лекция'}, {'title': 'Практика'}]}}
        result = snapshot({'history': [], 'session': session}, now=now, remaining=3600)
        self.assertEqual(result['days']['2026-10-19']['seconds'], 1200)
        self.assertEqual(result['days']['2026-10-19']['completed'], 0)
        self.assertEqual(result['records'][0]['tasks'], ['Лекция', 'Практика'])
        history = dict(session, seconds=4800, ended_at=now, outcome='completed')
        result = snapshot({'history': [history], 'session': dict(session, phase='break')}, now=now)
        self.assertEqual(result['days']['2026-10-19']['seconds'], 4800)
        self.assertEqual(len(result['records']), 1)

    def test_yesterdays_streak_and_interruption(self):
        now = timestamp('2026-10-20T08:00:00')
        records = [{'id': str(i), 'ended_at': timestamp(f'2026-10-{i}T16:00:00'),
                    'seconds': 4800, 'outcome': 'completed'} for i in (18, 19)]
        records.append({'id': 'x', 'ended_at': now, 'seconds': 250, 'outcome': 'interrupted'})
        result = snapshot({'history': records}, now=now)
        self.assertEqual(result['streak'], 2)
        self.assertEqual(result['days']['2026-10-20']['interrupted'], 1)
        self.assertEqual(result['days']['2026-10-20']['completed'], 0)

    def test_sample_gaps_stale_state_sleep_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'usage.sqlite3'
            tracker = UsageTracker(path)
            state = {'session': {'id': 'a', 'phase': 'focus', 'remaining': 4800}}
            now = timestamp('2026-10-19T14:00:00')
            tracker.sample(state, ('code', 'VS Code'), now, 100)
            tracker.sample(state, ('code', 'VS Code'), now+5, 105)
            tracker.sample(state, ('code', 'VS Code'), now+500, 600)  # sleep gap
            tracker.sample(state, ('code', 'VS Code'), now+505, 605, fresh=False)
            tracker.sample(state, ('arc', 'Arc'), now+510, 610)
            tracker.sample(state, ('arc', 'Arc'), now+515, 615)
            tracker.sample({'session': {'phase': 'break'}}, ('arc', 'Arc'), now+520, 620)
            tracker.close()
            tracker = UsageTracker(path)
            usage = tracker.summary()['days']['2026-10-19']
            self.assertEqual(usage['code']['seconds'], 5)
            self.assertEqual(usage['arc']['seconds'], 5)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            tracker.close()


if __name__ == '__main__':
    unittest.main()
