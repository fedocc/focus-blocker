import unittest
from unittest.mock import patch
from anki_study import bridge


def state(phase=None, key='one', end=1600):
    return {'session': {'id': key, 'phase': phase, 'ends_at': end} if phase else None}


class AlertTests(unittest.TestCase):
    def test_break_is_noticed_on_transition_and_reopening_anki(self):
        preferences = {}
        gate = bridge.PhaseAlerts(preferences)
        self.assertIsNone(gate.observe(state('focus'), 900))
        self.assertEqual(gate.observe(state('break'), 1000)['kind'], 'break')
        self.assertEqual(bridge.PhaseAlerts(preferences).observe(state('break'), 1100)['kind'], 'break')
        gate.acknowledge()
        self.assertIsNone(gate.observe(state('break'), 1100))
        self.assertIsNone(bridge.PhaseAlerts(preferences).observe(state('break'), 1100))
        self.assertEqual(gate.observe(state('break', key='two'), 1200)['id'], 'two')

    def test_rest_end_has_separate_acknowledgement_and_no_new_block(self):
        gate = bridge.PhaseAlerts({})
        gate.observe(state('break'), 1000)
        gate.acknowledge()
        self.assertEqual(gate.observe(state(), 1600), {'id': 'one', 'kind': 'ready'})
        self.assertEqual(gate.observe(state(), 1650)['kind'], 'ready')
        gate.acknowledge()
        self.assertIsNone(gate.observe(state(), 1700))
        self.assertIsNone(gate.observe(state('focus', key='two'), 1800))
        self.assertIsNone(gate.observe(state(), 1801), 'emergency stop is not the end of a break')

    def test_recovery_only_reports_a_recent_rest_end(self):
        preferences = {'alert_last_break': {'id': 'one', 'ends_at': 1600}}
        self.assertEqual(bridge.PhaseAlerts(preferences.copy()).observe(state(), 1610)['kind'], 'ready')
        self.assertIsNone(bridge.PhaseAlerts(preferences.copy()).observe(state(), 2000))
        self.assertIsNone(bridge.PhaseAlerts(preferences.copy()).observe(state(), 1500))
        preferences['alert_ack_ready'] = 'one'
        self.assertIsNone(bridge.PhaseAlerts(preferences).observe(state(), 1610))

    def test_audio_is_a_fixed_normal_volume_command_without_task_content(self):
        with patch.object(bridge.Path, 'is_file', return_value=True), patch.object(bridge.subprocess, 'Popen') as play:
            bridge.play_alert_sound()
            self.assertEqual(play.call_args.args[0], ['/usr/bin/afplay', '-v', '1', '/System/Library/Sounds/Glass.aiff'])
            self.assertNotIn('shell', play.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
