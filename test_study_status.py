"""Status remains responsive during browser checks, without live system calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import focus_study as study


class StatusSnapshotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.patch = patch.object(study, 'STATE', Path(folder.name) / 'study.json')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now, self.mono, self.boot = 1792396800., 10000., 'boot-1'
        self.session = study.start({'task': {'title': 'Fixture'}, 'apps': [],
                                    'timing': {'focus': 50, 'short': 7, 'long': 20, 'every': 2},
                                    'auto_continue': True, 'blocks': 2, 'blackout': True},
                                   self.boot, self.now, self.mono)

    def snapshot(self, elapsed, boot=None):
        before = study.STATE.read_bytes()
        value = study.status(boot or self.boot, self.now + elapsed,
                             self.mono + elapsed, advance_state=False)
        self.assertEqual(study.STATE.read_bytes(), before)
        return value

    def test_busy_snapshot_keeps_id_phase_history_and_emergency_at_deadlines(self):
        for phase in ('focus', 'break', 'ready', 'prepare'):
            value = study.read()
            value['session'].update(phase=phase, deadline_mono=self.mono + 10, remaining=10)
            value['last_emergency'] = self.now - 100
            study.save(value)
            result = self.snapshot(600)
            self.assertEqual(result['session']['phase'], phase)
            self.assertEqual(result['session']['id'], self.session['id'])
            self.assertEqual(result['session']['remaining'], 0)
            self.assertEqual(result['history'], [])
            self.assertFalse(result['emergency_available'])
            self.assertEqual(result['session']['apps'], self.session['apps'])
            self.assertTrue(result['session']['blackout'])

    def test_snapshot_projects_countdown_and_overtime_without_double_credit(self):
        self.assertEqual(self.snapshot(100)['session']['remaining'], 2900)
        study.status(self.boot, self.now + 3100, self.mono + 3100)
        for _ in range(2):
            result = self.snapshot(3200)
            self.assertEqual(result['session']['overtime_seconds'], 200)
            self.assertEqual(result['session']['overtime_anchor_mono'], self.mono + 3200)
        self.assertEqual(study.read()['session']['overtime_seconds'], 100)
        result = study.status(self.boot, self.now + 3200, self.mono + 3200)
        self.assertEqual(result['session']['overtime_seconds'], 200)

    def test_new_boot_snapshot_uses_checkpoint_without_offline_credit(self):
        study.status(self.boot, self.now + 100, self.mono + 100)
        result = self.snapshot(80000, boot='boot-2')
        self.assertEqual(result['session']['remaining'], 2900)
        self.assertEqual(result['session']['deadline_mono'], self.mono + 80000 + 2900)
        self.assertEqual(study.read()['session']['boot'], self.boot)

    def test_idle_and_corrupt_state_are_not_reset_by_snapshot(self):
        value = study.read()
        value['session'] = None
        study.save(value)
        self.assertIsNone(self.snapshot(100)['session'])
        study.STATE.write_text('{invalid')
        with self.assertRaises(ValueError):
            study.status(self.boot, advance_state=False)
        self.assertEqual(study.STATE.read_text(), '{invalid')

    def test_command_returns_with_daemon_lock_held_and_all_mutations_still_wait(self):
        program = r'''
import contextlib,fcntl,io,json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
import focus_study  # Load repository code before the installed helper path.
from test_youtube_guard import helper
with tempfile.TemporaryDirectory() as folder:
 base=Path(folder);state=base/'study.json'
 state.write_text(json.dumps({'schema':1,'session':None,'history':[],'last_emergency':1234}))
 before=state.read_bytes()
 with patch.object(helper,'BASE',base),patch.object(helper.study_lock,'STATE',state),patch.object(helper.os,'geteuid',return_value=0),patch.object(helper,'boot_identity',return_value='fixture'),patch.object(helper,'timer_running',return_value=True),patch.object(sys,'argv',['focus-helper','study-status']):
  for busy in (True,False):
   with contextlib.ExitStack() as stack:
    if busy:stack.enter_context(helper.locked())
    output=io.StringIO()
    with contextlib.redirect_stdout(output):helper.main()
    value=json.loads(output.getvalue())
    assert value['status_refresh_pending'] is busy
    assert value['last_emergency']==1234 and value['session'] is None
    assert value['service_running'] and value['supports_confirmed_break']
    assert state.read_bytes()==before
  # Default/mutating callers still request a blocking exclusive lock.
  with patch.object(helper.fcntl,'flock') as flock:
   with helper.locked():pass
  assert flock.call_args_list[0].args[1]==fcntl.LOCK_EX
 print('busy status returned; state unchanged; mutations remain serialized')
'''
        result = subprocess.run([sys.executable, '-B', '-c', program],
                                cwd=Path(__file__).parent, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
