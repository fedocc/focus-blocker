import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import focus_study as study
from anki_study import analytics, bridge


class PomodoroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(study, 'STATE', Path(self.temp.name) / 'study.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now, self.mono, self.boot = 1792494000, 100, 'test'

    def start(self, preset='pomo25', blocks=4):
        return study.start({'task': {'title': 'Lecture'}, 'apps': ['company.thebrowser.Browser'],
                            'preset': preset, 'blocks': blocks, 'auto_continue': True, 'blackout': True},
                           self.boot, self.now, self.mono)

    def advance(self, seconds):
        self.now += seconds
        self.mono += seconds
        return study.status(self.boot, self.now, self.mono)

    def rest(self, session):
        return study.start_break({'session_id': session['id']}, self.boot, self.now, self.mono)

    def next(self, session, action='ready', **kwargs):
        return study.next_block(dict(session_id=session['id'], **kwargs), self.boot, self.now, self.mono, action=action)

    def test_presets_complete_four_blocks_then_final_long_rest_and_stop(self):
        self.assertEqual({k:tuple(x*60 if i<3 else x for i,x in enumerate(v)) for k,v in bridge.PRESETS.items()}, study.PRESETS)
        for preset in study.PRESETS:
            with self.subTest(preset=preset):
                study.save({'schema':1,'session':None,'history':[],'last_emergency':None})
                session = self.start(preset)
                focus, short, long, every = study.PRESETS[preset]
                ids = []
                for index in range(1,5):
                    ids.append(session['id'])
                    session = self.advance(focus)['session']
                    self.assertTrue(session['awaiting_rest'])
                    rest = self.rest(session)
                    expected = long if index % every == 0 else short
                    self.assertEqual(rest['remaining'], expected)
                    state = self.advance(expected)
                    if index == 4:
                        self.assertIsNone(state['session'])
                    else:
                        session = state['session']
                        self.assertEqual(session['phase'], 'ready')
                        self.assertEqual(self.advance(5000)['session']['phase'], 'ready')
                        self.next(session)
                        self.assertEqual(self.advance(299)['session']['phase'], 'prepare')
                        session = self.advance(1)['session']
                        self.assertEqual(session['phase'], 'focus')
                        self.assertEqual(session['remaining'], focus)
                self.assertEqual(len(set(ids)), 4)
                self.assertEqual([r['seconds'] for r in state['history']], [focus]*4)

    def test_preparation_updates_keep_deadline_and_snapshot_and_can_start_early(self):
        session = self.start()
        session = self.advance(1500)['session']
        self.rest(session)
        session = self.advance(300)['session']
        session = self.next(session)
        deadline = session['deadline_mono']
        self.advance(15)
        task = {'title':'Zoom lecture'}
        updated = self.next(session, 'update', task=task, apps=['us.zoom.xos'])
        task['title'] = 'Mutated'
        self.assertEqual(updated['deadline_mono'], deadline)
        self.assertEqual(self.next(session)['deadline_mono'], deadline, 'repeated acknowledgement cannot extend preparation')
        focus = self.next(session, 'start', task=updated['task'], apps=updated['apps'])
        self.assertEqual(focus['phase'], 'focus')
        self.assertEqual(focus['task']['title'], 'Zoom lecture')
        self.assertIn('us.zoom.xos', focus['apps'])
        with self.assertRaises(RuntimeError):
            self.next(focus, 'update', task={'title':'Changed'}, apps=[])

    def test_manual_pomodoro_still_takes_long_rest_after_four_blocks(self):
        for expected in (300,300,300,1800):
            session=study.start({'task':{'title':'Study'},'apps':[],'preset':'pomo25'},self.boot,self.now,self.mono)
            self.advance(1500)
            rest=self.rest(session)
            self.assertEqual(rest['remaining'],expected)
            self.assertIsNone(self.advance(expected)['session'])

    def test_50_7_repeats_long_rest_after_every_two_blocks(self):
        self.assertEqual(study.PRESETS['pomo50'], (3000,420,1800,2))
        for expected in (420,1800,420,1800):
            session=study.start({'task':{'title':'Study'},'apps':[],'preset':'pomo50'},self.boot,self.now,self.mono)
            self.advance(3010)
            rest=self.rest(session)
            self.assertEqual(rest['remaining'],expected)
            self.assertEqual(study.read()['history'][-1]['seconds'],3010)
            self.assertIsNone(self.advance(expected)['session'])

    def test_late_selection_cannot_reset_or_modify_autostarted_block(self):
        session = self.start()
        self.advance(1500)
        self.rest(session)
        session = self.advance(300)['session']
        self.next(session)
        self.now += 300
        self.mono += 300
        with self.assertRaises(RuntimeError):
            self.next(session, 'update', task={'title':'Late'}, apps=[])
        state = study.read()
        self.assertEqual(state['session']['phase'], 'focus')
        self.assertEqual(state['session']['task']['title'], 'Lecture')
        self.assertNotEqual(state['session']['id'], session['id'])
        self.assertEqual(state['session']['remaining'], 1500)

    def test_reboot_and_daemon_gap_do_not_earn_unattended_blocks(self):
        session = self.start()
        self.advance(1500)
        self.rest(session)
        session = self.advance(300)['session']
        self.next(session)
        self.advance(60)
        state = study.status('newboot', self.now+100000, 10)
        self.assertEqual(state['session']['remaining'], 240)
        state = study.status('newboot', self.now+200000, 100000)
        self.assertEqual(state['session']['phase'], 'focus')
        self.assertEqual(state['session']['remaining'], 1500)
        self.assertEqual(len(state['history']), 1)

    def test_emergency_cancels_series_in_rest_ready_and_preparation_without_duplicate_history(self):
        for phase in ('break', 'ready', 'prepare'):
            study.save({'schema':1,'session':None,'history':[],'last_emergency':None})
            session = self.start()
            self.advance(1500)
            session = self.rest(session)
            if phase != 'break':
                session = self.advance(300)['session']
            if phase == 'prepare':
                session = self.next(session)
            request = {'session_id':session['id'], 'reason':'Emergency'}
            self.assertEqual(study.request_emergency(request, self.boot, self.now, self.mono)['wait_seconds'], 0)
            study.confirm_emergency({'session_id':session['id'], 'confirmation':'ПОДТВЕРЖДАЮ'}, self.boot, self.now, self.mono)
            state = self.advance(10000)
            self.assertIsNone(state['session'])
            self.assertEqual(len(state['history']), 1)
            self.assertFalse(state['emergency_available'])

    def test_invalid_mode_and_series_do_not_write_state(self):
        for key,value in [('preset','custom'),('preset',{}),('blocks',0),('blocks',25),('blocks',True),('auto_continue',1),('blackout','yes')]:
            request = {'task':{'title':'Study'},'apps':[],key:value}
            with self.assertRaises(ValueError):
                study.start(request,self.boot,self.now,self.mono)
            self.assertFalse(study.STATE.exists())

    def test_analytics_uses_actual_short_duration_and_excludes_rest_preparation(self):
        session = self.start('pomo30')
        state = self.advance(120)
        data = analytics.snapshot(state, now=self.now)
        self.assertEqual(data['records'][0]['seconds'],120)
        self.advance(1680)
        self.rest(session)
        state = self.advance(300)
        self.next(state['session'])
        data = analytics.snapshot(study.read(), now=self.now)
        self.assertEqual(sum(r['seconds'] for r in data['records']),1800)
        self.assertEqual(len(data['records']),1)


if __name__ == '__main__':
    unittest.main()
