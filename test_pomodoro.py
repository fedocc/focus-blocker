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

    def custom(self, **options):
        request={'task':{'title':'Study'},'apps':[], 'timing':{'focus':47,'short':7,'long':19,'every':2},
                 'auto_continue':True,'blocks':4,'blackout':True}
        request.update(options)
        return study.start(request,self.boot,self.now,self.mono)

    def short(self, session):
        return study.shorten_break({'session_id':session['id']},self.boot,self.now,self.mono)['session']

    def long_rest(self, blocks=4):
        session=self.custom(blocks=blocks)
        for index in range(2):
            self.advance(47*60)
            session=self.rest(session)
            if index==0:
                session=self.advance(420)['session']
                self.next(session)
                session=self.advance(300)['session']
        self.assertEqual(session['break_kind'],'long')
        return session

    def test_custom_durations_survive_the_full_series_and_source_mutation(self):
        timing={'focus':43,'short':6,'long':17,'every':3}
        session=self.custom(timing=timing)
        timing['focus']=1
        for index in range(1,5):
            self.assertEqual(session['focus_seconds'],2580)
            self.advance(2580)
            session=self.rest(session)
            expected=1020 if index==3 else 360
            self.assertEqual(session['remaining'],expected)
            state=self.advance(expected)
            if index<4:
                self.next(state['session'])
                session=self.advance(300)['session']
        self.assertIsNone(state['session'])
        self.assertEqual([r['seconds'] for r in state['history']],[2580]*4)
        self.assertEqual(state['history'][0]['timing']['focus'],43)

    def test_shortening_counts_elapsed_time_and_retries_cannot_extend_it(self):
        session=self.long_rest()
        history=copy.deepcopy(study.read()['history'])
        self.advance(240)
        shortened=self.short(session)
        self.assertEqual(shortened['remaining'],180)
        self.assertEqual(shortened['break_kind'],'short')
        self.advance(30)
        again=self.short(session)
        self.assertEqual(again['remaining'],150)
        self.assertEqual(again['deadline_mono'],shortened['deadline_mono'])
        self.assertIsNone(study.read()['last_emergency'])
        self.assertEqual(study.read()['history'],history)
        ready=self.advance(150)['session']
        self.assertEqual(ready['phase'],'ready')
        self.assertEqual(self.short(ready)['phase'],'ready')
        self.next(ready)
        session=self.advance(300)['session']
        self.assertNotIn('break_shortened',session)
        self.advance(2820)
        self.assertEqual(self.rest(session)['remaining'],420)

    def test_shortening_after_short_duration_requires_return_ack_and_finishes_final_series(self):
        session=self.long_rest()
        self.advance(600)
        ready=self.short(session)
        self.assertEqual(ready['phase'],'ready')
        self.assertEqual(self.advance(1000)['session']['phase'],'ready')
        # Independent final two-block series: no phantom next block.
        study.save({'schema':1,'session':None,'history':[],'last_emergency':None})
        session=self.long_rest(blocks=2)
        self.advance(420)
        self.assertIsNone(self.short(session))
        self.assertIsNone(study.read()['session'])
        self.assertEqual(len(study.read()['history']),2)

    def test_shortening_after_reboot_uses_saved_rest_checkpoint_not_wall_time(self):
        session=self.long_rest()
        self.advance(120)
        self.boot='reboot'
        self.now+=86400
        self.mono=10
        resumed=study.status(self.boot,self.now,self.mono)['session']
        self.assertEqual(resumed['remaining'],1020)
        shortened=self.short(session)
        self.assertEqual(shortened['remaining'],300)
        self.now-=172800
        self.advance(60)
        self.assertEqual(self.short(session)['remaining'],240)

    def test_shortening_rejects_focus_short_rest_stale_ids_and_extra_duration(self):
        session=self.custom()
        with self.assertRaises(RuntimeError):self.short(session)
        self.advance(2820)
        session=self.rest(session)
        with self.assertRaises(RuntimeError):self.short(session)
        for request in ({'session_id':'stale'}, {'session_id':session['id'],'seconds':0}):
            with self.assertRaises((ValueError,RuntimeError)):
                study.shorten_break(request,self.boot,self.now,self.mono)
        self.assertEqual(study.read()['session']['remaining'],420)

    def test_custom_validation_and_settings_are_locked_after_start(self):
        good={'focus':50,'short':7,'long':20,'every':2}
        for key,bad in (('focus',0),('focus',241),('short',True),('short',61),('long',6),('long',181),('every',0),('every',13),('focus',1.5)):
            with self.assertRaises(ValueError):self.custom(timing=dict(good,**{key:bad}))
            self.assertFalse(study.STATE.exists())
        with self.assertRaises(ValueError):self.custom(timing=dict(good,extra=1))
        with self.assertRaises(ValueError):self.custom(preset='pomo50')
        session=self.custom()
        with self.assertRaises(RuntimeError):self.custom(timing=good)
        with self.assertRaises(ValueError):
            self.next(session,'update',task={'title':'x'},apps=[],timing=good)
        self.assertEqual(study.read()['session']['timing'],{'focus':47,'short':7,'long':19,'every':2})

    def test_legacy_preferences_migrate_to_plain_values(self):
        self.assertEqual(bridge.timing_settings({'focus_mode':'pomo50'}),{'focus':50,'short':7,'long':30,'every':2})
        self.assertEqual(bridge.timing_settings(),{'focus':50,'short':7,'long':20,'every':2})
        self.assertEqual(bridge.cycle_text({'timing':{'focus':43,'short':6,'long':17,'every':3}}),
                         '43 мин учёбы · 6 мин отдыха · 17 мин после 3 блоков')

    def test_series_preview_matches_timing_totals_manual_gates_and_final_free_time(self):
        plan=bridge.series_plan({'focus':50,'short':7,'long':20,'every':2},blocks=4)
        self.assertEqual([n['minutes'] for n in plan['nodes'] if n['kind']=='rest'],[7,20,7,20])
        self.assertEqual(plan['focus_minutes'],200)
        self.assertEqual(plan['timer_minutes'],269)
        self.assertEqual(len([n for n in plan['nodes'] if n['kind']=='prepare']),3)
        self.assertEqual(plan['nodes'][-1]['id'],'end')
        self.assertEqual(plan['nodes'][-1]['kind'],'automatic')
        self.assertIn('свободное',plan['nodes'][-1]['title'])
        for i in range(4):
            gate=next(n for n in plan['nodes'] if n['id']==f'rest-start:{i}')
            self.assertEqual(gate['kind'],'manual')
            self.assertEqual(gate['minutes'],0)
        one=bridge.series_plan({'focus':50,'short':7,'long':20,'every':2},blocks=9,auto=False,blackout=False)
        self.assertEqual(one['blocks'],1)
        self.assertFalse(any(n['kind']=='prepare' for n in one['nodes']))
        self.assertFalse(next(n for n in one['nodes'] if n['kind']=='rest')['protected'])

    def test_series_preview_follows_committed_phase_overtime_shortening_and_ack(self):
        session=self.custom(timing={'focus':50,'short':7,'long':20,'every':2})
        def plan():
            state=study.status(self.boot,self.now,self.mono)
            before=copy.deepcopy(state)
            with patch.object(bridge,'remaining_seconds',return_value=(state['session'] or {}).get('remaining',0)):
                result=bridge.series_plan({'focus':1,'short':1,'long':1,'every':1},blocks=1,auto=False,state=state)
            self.assertEqual(state,before)
            self.assertEqual(result['focus_minutes'],200,'UI edits cannot replace committed timing')
            return result
        self.assertEqual(plan()['active_id'],'focus:0')
        self.advance(3000)
        self.assertEqual(plan()['active_id'],'rest-start:0')
        self.rest(session)
        self.assertEqual(plan()['active_id'],'rest:0')
        state=self.advance(420)
        self.assertEqual(plan()['active_id'],'return:0')
        self.next(state['session'])
        self.assertEqual(plan()['active_id'],'prepare:1')
        session=self.advance(300)['session']
        self.assertEqual(plan()['active_id'],'focus:1')
        self.advance(3000);self.rest(session);self.advance(60);self.short(session)
        model=plan()
        rest=next(n for n in model['nodes'] if n['id']=='rest:1')
        self.assertTrue(rest['shortened'])
        self.assertEqual(rest['minutes'],7)
        self.assertEqual(next(n for n in model['nodes'] if n['id']=='rest:3')['minutes'],20)

    def test_single_custom_preview_uses_same_daily_cadence_as_the_service(self):
        timing={'focus':11,'short':3,'long':8,'every':2}
        session=self.custom(timing=timing,auto_continue=False)
        self.advance(660);self.rest(session);self.advance(180)
        state=study.status(self.boot,self.now,self.mono)
        state['history'].append({'id':'legacy','preset':'pomo50','outcome':'completed','ended_at':self.now})
        model=bridge.series_plan(timing,auto=False,state=state)
        planned=next(n for n in model['nodes'] if n['kind']=='rest')['minutes']
        self.assertEqual(planned,8)
        session=self.custom(timing=timing,auto_continue=False)
        self.advance(660)
        self.assertEqual(self.rest(session)['remaining'],planned*60)

    def test_timeline_keeps_legacy_80_minute_session_and_daily_long_rest(self):
        for phase,completed in (('focus',1),('break',2)):
            state={'session':{'phase':phase,'remaining':100,'apps':[]},'completed_today':completed}
            with patch.object(bridge,'remaining_seconds',return_value=100):
                model=bridge.series_plan({'focus':1,'short':1,'long':1,'every':1},state=state)
            self.assertEqual(model['focus_minutes'],80)
            self.assertEqual(next(n for n in model['nodes'] if n['kind']=='rest')['minutes'],30)


if __name__ == '__main__':
    unittest.main()
