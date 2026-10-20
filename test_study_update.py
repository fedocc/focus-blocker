import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wait_study_idle as update


class StudyUpdateTests(unittest.TestCase):
    def test_waits_for_focus_overtime_and_full_rest_without_mutations(self):
        read = Mock(side_effect=[{'phase': 'focus'}, {'phase': 'focus', 'awaiting_rest': True},
                                 {'phase': 'break'}, None])
        pause, report = Mock(), Mock()
        update.wait_until_idle(read, pause, report)
        self.assertEqual(pause.call_count, 3)
        self.assertEqual(read.call_count, 4)
        self.assertEqual(report.call_count, 3)

    def test_status_errors_do_not_fall_through_to_installation(self):
        for result in [SimpleNamespace(returncode=1),
                       SimpleNamespace(returncode=0, stdout='{}'),
                       SimpleNamespace(returncode=0, stdout=json.dumps({'session': {'phase': 'unknown'}}))]:
            with patch.object(update.subprocess, 'run', return_value=result), self.assertRaises((ValueError, RuntimeError)):
                update.current_session()
        with patch.object(update.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"session":null}')) as run:
            self.assertIsNone(update.current_session())
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', str(update.HELPER), 'study-status'])

    def test_installer_checks_idle_before_requesting_admin_password(self):
        source = (update.Path(__file__).parent / 'install-study.command').read_text()
        self.assertLess(source.index('python3 -I wait_study_idle.py'), source.index('exec /usr/bin/sudo'))


if __name__ == '__main__':
    unittest.main()
