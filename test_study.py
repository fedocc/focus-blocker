import copy
import json
import contextlib
import io
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import focus_study as study
import install as installer
import test_youtube_guard as guards
helper, guard = guards.helper, guards.guard


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(study, 'STATE', Path(self.temp.name) / 'study.json')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now = 1792396800.0
        self.mono = 10000.0
        self.boot = 'boot-1'
        self.request = {'task': {'title': 'Лекция DL'}, 'apps': ['company.thebrowser.Browser']}

    def start(self):
        return study.start(self.request, self.boot, self.now, self.mono)

    def state(self, seconds=0, wall=None, boot=None):
        return study.status(boot or self.boot, self.now + seconds if wall is None else wall, self.mono + seconds)

    def emergency(self, session):
        return study.request_emergency({'session_id': session['id'], 'reason': 'Нужно экстренно закончить работу'}, self.boot, self.now, self.mono)

    def confirm(self, session, seconds=60):
        return study.confirm_emergency({'session_id': session['id'], 'confirmation': study.CONFIRMATION}, self.boot, self.now + seconds, self.mono + seconds)

    def test_fixed_duration_and_immutable_deep_snapshot(self):
        session = self.start()
        self.request['task']['title'] = 'Подмена'
        self.assertEqual(study.read()['session']['task']['title'], 'Лекция DL')
        self.assertEqual(session['remaining'], 4800)
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertIn('com.apple.Photos', session['apps'])
        self.assertIn('net.ankiweb.anki', session['apps'])

    def test_multiple_tasks_are_validated_and_committed_independently_of_ui(self):
        items = [{'title': 'Лекция DL', 'occurrence_id': 'dl', 'url': 'https://slovo.45-80-228-215.sslip.io/?task=dl'},
                 {'title': 'Алгоритмы', 'occurrence_id': 'algo', 'url': 'https://slovo.45-80-228-215.sslip.io/?task=algo'}]
        self.request['task'] = {'title': 'Две задачи', 'items': items}
        self.start()
        items[0]['title'] = 'Подмена'
        snapshot = study.read()['session']['task']['items']
        self.assertEqual([item['occurrence_id'] for item in snapshot], ['dl', 'algo'])
        self.assertEqual(snapshot[0]['title'], 'Лекция DL')
        with self.assertRaises(RuntimeError):
            self.start()

    def test_nested_duplicate_and_external_grouped_tasks_are_rejected(self):
        for items in ([{'title':'x','items':[{'title':'y'}]}],
                      [{'title':'x','occurrence_id':'same'},{'title':'y','occurrence_id':'same'}],
                      [{'title':'x','url':'https://other.example/'}], [], [{'title':'x'}]*31):
            with self.assertRaises(ValueError):
                study.start({'task': {'title': 'Группа', 'items': items}, 'apps': []}, self.boot, self.now, self.mono)
        self.assertFalse(study.STATE.exists())

    def test_explicit_installer_repair_preserves_history_and_charges_exit(self):
        self.start()
        value = study.read()
        value['history'] = [{'id':'old','outcome':'completed','ended_at':self.now-100}]
        fixed = installer.interrupt_for_repair(value, self.now + 500)
        self.assertIsNotNone(value['session'], 'repair returns a snapshot; no implicit state mutation')
        self.assertIsNone(fixed['session'])
        self.assertEqual(fixed['last_emergency'], self.now + 500)
        self.assertEqual(fixed['history'][0], value['history'][0])
        self.assertEqual(fixed['history'][-1]['outcome'], 'interrupted')
        self.assertFalse(study.emergency_available(fixed, self.now + 501))

    def test_helper_uses_stable_boot_uuid_and_caches_it(self):
        from types import SimpleNamespace
        boot_id = '8C277B74-17D0-4677-A3B4-1B76E5B84A85'
        with patch.object(helper, '_BOOT', None), patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(stdout=boot_id)) as run:
            self.assertEqual(helper.boot_identity(), boot_id.lower())
            self.assertEqual(helper.boot_identity(), boot_id.lower())
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ['/usr/sbin/sysctl','-n','kern.bootsessionuuid'])

    def test_alternating_mandatory_breaks_and_five_plus_optional_progress(self):
        for index in range(1, 7):
            session = self.start()
            finished = self.state(4800)
            self.assertTrue(finished['session']['awaiting_rest'])
            self.assertEqual(finished['session']['phase'], 'focus')
            study.start_break({'session_id': session['id']}, self.boot, self.now + 4800, self.mono + 4800)
            finished = self.state(4800)
            self.assertEqual(finished['completed_today'], index)
            rest = 1800 if index % 2 == 0 else 600
            self.assertEqual(finished['session']['remaining'], rest)
            with self.assertRaises(RuntimeError):
                study.start(self.request, self.boot, self.now + 4801, self.mono + 4801)
            self.assertIsNone(self.state(4800 + rest)['session'])
            self.now += 4800 + rest
            self.mono += 4800 + rest

    def test_overtime_keeps_protection_and_full_break_needs_explicit_confirmation(self):
        session = self.start()
        for request in ({'session_id': session['id']}, {'session_id': session['id'], 'seconds': 1}):
            with self.assertRaises((RuntimeError, ValueError)):
                study.start_break(request, self.boot, self.now + 100, self.mono + 100)
        value = self.state(5400)
        self.assertEqual(value['history'], [])
        self.assertEqual(value['session']['overtime_seconds'], 600)
        self.assertEqual(value['session']['apps'], session['apps'])
        with self.assertRaises(RuntimeError):
            study.start(self.request, self.boot, self.now + 5400, self.mono + 5400)
        study.record_attempt('app', 'com.example.Game')
        rest = study.start_break({'session_id': session['id']}, self.boot, self.now + 5520, self.mono + 5520)
        self.assertEqual(rest['remaining'], 600)
        value = self.state(5520)
        self.assertEqual(value['completed_today'], 1)
        self.assertEqual(value['history'][0]['seconds'], 5520)
        self.assertEqual(value['history'][0]['overtime_seconds'], 720)
        self.assertEqual(value['history'][0]['app_attempts'], {'com.example.Game': 1})
        self.assertIsNone(value['last_emergency'])
        retry = study.start_break({'session_id': session['id']}, self.boot, self.now + 5530, self.mono + 5530)
        self.assertEqual(retry['remaining'], 590)
        self.assertEqual(len(study.read()['history']), 1)
        self.assertIsNone(self.state(6120)['session'])

    def test_overtime_reboot_preserves_checkpoint_without_offline_or_clock_credit(self):
        session = self.start()
        self.state(5100)
        value = study.status('boot-2', self.now + 80000, 100)
        self.assertEqual(value['session']['overtime_seconds'], 300)
        value = study.status('boot-2', self.now - 86400, 160)
        self.assertEqual(value['session']['overtime_seconds'], 360)
        with self.assertRaises(RuntimeError):
            study.start_break({'session_id': 'wrong'}, 'boot-2', self.now, 160)
        result = study.request_emergency({'session_id': session['id'], 'reason': 'Экстренный выход'}, 'boot-2', self.now, 160)
        self.assertEqual(result['wait_seconds'], 0)

    def test_wall_clock_changes_cannot_finish_or_extend_focus(self):
        self.start()
        self.assertEqual(self.state(300, wall=self.now + 86400)['session']['remaining'], 4500)
        self.assertEqual(self.state(600, wall=self.now - 86400)['session']['remaining'], 4200)

    def test_same_boot_process_restart_does_not_reset_deadline(self):
        self.start()
        self.assertEqual(self.state(1200)['session']['remaining'], 3600)
        self.assertEqual(self.state(1800)['session']['remaining'], 3000)

    def test_reboot_resumes_checkpoint_instead_of_crediting_offline_time(self):
        self.start()
        self.state(1200)
        value = study.status('boot-2', self.now + 80000, 100)
        self.assertEqual(value['session']['remaining'], 3600)
        self.assertEqual(value['completed_today'], 0)

    def test_emergency_needs_request_and_exact_confirmation_without_wait(self):
        session = self.start()
        with self.assertRaises(ValueError):
            study.request_emergency({'session_id': session['id'], 'reason': ''}, self.boot, self.now, self.mono)
        with self.assertRaises(RuntimeError):
            self.confirm(session, 0)
        self.emergency(session)
        with self.assertRaises(RuntimeError):
            study.confirm_emergency({'session_id': session['id'], 'confirmation': 'подтверждаю'}, self.boot, self.now, self.mono)
        result = self.confirm(session, 0)
        self.assertEqual(result['outcome'], 'interrupted')
        self.assertIsNone(study.read()['session'])
        self.assertEqual(self.state(61)['completed_today'], 0)
        self.assertFalse(self.state(61)['emergency_available'])

    def test_second_emergency_refused_after_new_session_and_reload(self):
        session = self.start()
        self.emergency(session)
        self.confirm(session)
        session = study.start(self.request, self.boot, self.now + 100, self.mono + 100)
        with self.assertRaises(RuntimeError):
            self.emergency(session)
        self.assertEqual(len(study.read()['history']), 1)

    def test_midnight_does_not_immediately_refund_emergency(self):
        value = study.read()
        value['last_emergency'] = self.now
        self.assertFalse(study.emergency_available(value, self.now + 100))
        self.assertFalse(study.emergency_available(value, self.now - 86400))
        self.assertTrue(study.emergency_available(value, self.now + 86400))

    def test_stale_session_token_cannot_stop_next_block(self):
        old = self.start()
        self.emergency(old)
        self.confirm(old)
        study.start(self.request, self.boot, self.now + 86400, self.mono + 86400)
        with self.assertRaises(RuntimeError):
            study.confirm_emergency({'session_id': old['id'], 'confirmation': study.CONFIRMATION}, self.boot, self.now + 86460, self.mono + 86460)
        self.assertIsNotNone(study.read()['session'])

    def test_prearmed_confirmation_expires_and_reboot_does_not_create_wait(self):
        session = self.start()
        self.emergency(session)
        with self.assertRaises(RuntimeError):
            self.confirm(session, 181)
        self.emergency(session)
        study.status('boot-2', self.now + 5, 1)
        result = study.confirm_emergency({'session_id': session['id'], 'confirmation': study.CONFIRMATION}, 'boot-2', self.now + 10, 6)
        self.assertTrue(result['stopped'])

    def test_attempt_counts_exclude_urls_and_survive_completion(self):
        self.start()
        study.record_attempt('site', 'youtube')
        study.record_attempt('app', 'com.example.Game')
        value = self.state(4800)
        study.start_break({'session_id': value['session']['id']}, self.boot, self.now + 4800, self.mono + 4800)
        value = self.state(4800)
        self.assertEqual(value['history'][-1]['attempts'], {'youtube': 1})
        self.assertEqual(value['history'][-1]['app_attempts'], {'com.example.Game': 1})
        self.assertEqual(value['attempts_today'], 2)
        self.assertEqual(self.state(4801)['attempts_today'], 2, 'break is not counted a second time')

    def test_app_failure_is_visible_and_recovery_clears_it(self):
        self.start()
        study.app_health('Нет разрешения macOS')
        self.assertEqual(self.state()['session']['app_guard_error'], 'Нет разрешения macOS')
        study.app_health(None)
        self.assertIsNone(self.state()['session']['app_guard_error'])

    def test_malformed_input_and_corrupt_state_fail_without_reset(self):
        for changes in ({'seconds': 1}, {'apps': ['com.example.App" & do shell script "x']},
                        {'task': {'title': 'Task', 'url': 'javascript:alert(1)'}}, {'apps': [True]}):
            request = dict(copy.deepcopy(self.request), **changes)
            with self.assertRaises(ValueError):
                study.start(request, self.boot, self.now, self.mono)
            self.assertFalse(study.STATE.exists())
        study.STATE.write_text('{broken')
        with self.assertRaises(ValueError):
            self.start()
        self.assertEqual(study.STATE.read_text(), '{broken')


class StudyHelperTests(unittest.TestCase):
    def setUp(self):
        guards.RootIntegrationTests.setUp(self)
        helper.HOSTS.write_text(helper.HOSTS.read_text() + '# === FOCUS_BLOCK:STUDY_WEB START ===\n127.0.0.1 tiktok.com\n# === FOCUS_BLOCK:STUDY_WEB END ===\n')

    def test_overtime_guard_and_explicit_rest_command_use_the_protected_session(self):
        session = study.start({'task': {'title': 'Учёба'}, 'apps': []}, 'test-boot', 1000, 100)
        with patch.object(study, 'clock', return_value=5500), patch.object(study.time, 'time', return_value=6400):
            with patch.object(guard, 'user_script', return_value='com.example.Game'):
                helper.guard_study_apps()
            self.assertTrue(study.read()['session']['awaiting_rest'])
            with self.assertRaises(RuntimeError):
                helper.lease('instagram', 120)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['focus-helper', 'study-break-start']), patch.object(sys, 'stdin', io.StringIO(json.dumps({'session_id': session['id']}))), patch.object(helper.os, 'geteuid', return_value=0), contextlib.redirect_stdout(output):
                helper.main()
            self.assertEqual(json.loads(output.getvalue())['remaining'], 600)
            self.assertEqual(study.read()['history'][0]['app_attempts'], {'com.example.Game': 1})
            self.assertEqual(study.read()['history'][0]['overtime_seconds'], 600)

    def test_study_blocks_social_and_ordinary_youtube_grants(self):
        study.start({'task': {'title': 'Учёба'}, 'apps': []}, 'test-boot')
        for service, scope in (('telegram', None), ('instagram', None), ('x', None),
                               ('youtube', {'mode': 'video', 'allowed_ids': ['aaaaaaaaaaa']})):
            with self.assertRaisesRegex(RuntimeError, 'учебный блок'):
                helper.lease(service, 120, scope)
        self.assertIsNone(helper.read_lease())

    def test_app_guard_hides_unselected_app_without_quitting_documents(self):
        study.start({'task': {'title': 'Учёба'}, 'apps': ['company.thebrowser.Browser']}, 'test-boot')
        with patch.object(guard, 'user_script', return_value='com.example.Game') as script:
            helper.guard_study_apps()
        source = script.call_args.args[0]
        self.assertIn('set visible of frontProcess to false', source)
        self.assertNotIn('quit', source)
        self.assertIn('com.apple.Photos', source)
        self.assertEqual(study.read()['session']['app_attempts'], {'com.example.Game': 1})

    def test_rest_guard_returns_to_anki_without_quitting_apps_or_changing_focus_time(self):
        session = study.start({'task': {'title':'Lecture'}, 'apps':['us.zoom.xos'],
                              'preset':'pomo25', 'blackout':True, 'auto_continue':True, 'blocks':4}, 'test-boot',1000,100)
        study.status('test-boot',2500,1600)
        study.start_break({'session_id':session['id']},'test-boot',2500,1600)
        with patch.object(study,'clock',return_value=1601), patch.object(study.time,'time',return_value=2501), patch.object(guard,'user_script',return_value='us.zoom.xos') as script:
            helper.guard_study_apps()
        source=script.call_args.args[0]
        self.assertIn('set frontmost of application process "Anki" to true',source)
        self.assertIn('set ankiBundle to "net.ankiweb.anki"',source)
        self.assertIn('set visible of frontProcess to false',source)
        self.assertNotIn('quit',source)
        self.assertNotIn('us.zoom.xos',source)
        self.assertNotIn('com.apple.Photos',source)
        self.assertEqual(study.read()['history'][0]['seconds'],1500)
        self.assertEqual(study.read()['session']['remaining'],299)

    def test_selected_zoom_is_allowed_during_focus_and_unselected_apps_are_hidden(self):
        study.start({'task': {'title':'Zoom'}, 'apps':['us.zoom.xos']},'test-boot')
        with patch.object(guard,'user_script',return_value='') as script:
            helper.guard_study_apps()
        self.assertIn('"us.zoom.xos"',script.call_args.args[0])
        self.assertNotIn('to activate',script.call_args.args[0])
        self.assertEqual(study.read()['session']['app_attempts'],{})

    def test_forgotten_study_tools_are_allowed_without_changing_commitment(self):
        study.start({'task': {'title':'Lecture'}, 'apps':['company.thebrowser.Browser']}, 'test-boot',1000,100)
        before = study.read()
        # The AppleScript includes exact educational bundle IDs before checking
        # or hiding the foreground process. Nothing needs to be preselected.
        with patch.object(study,'clock',return_value=100), patch.object(study.time,'time',return_value=1000), patch.object(guard, 'user_script', return_value='') as script:
            helper.guard_study_apps()
        source = script.call_args.args[0]
        for bundle in ('us.zoom.xos', 'com.microsoft.VSCode', 'com.apple.Preview', 'com.apple.calculator'):
            self.assertNotIn(bundle, before['session']['apps'])
            self.assertIn(json.dumps(bundle), source)
        for bundle in ('com.apple.Music', 'com.apple.TV', 'ru.keepcoder.Telegram', 'com.apple.Safari', 'com.apple.Terminal', 'com.example.Zoom'):
            self.assertNotIn(json.dumps(bundle), source)
        after = study.read()
        after['session'].pop('app_guard_error', None)
        self.assertEqual(after, before, 'automatic admission does not reset time, change tasks, or spend the emergency exit')
        policy = study.status('test-boot',1000,100)['auto_study_apps']
        self.assertEqual(policy['us.zoom.xos'], 'Zoom')
        self.assertNotIn('com.apple.Music', policy)

    def test_automatic_tools_are_not_exempt_from_mandatory_rest_or_ready(self):
        session = study.start({'task': {'title':'Lecture'}, 'apps':[],
                              'preset':'pomo25', 'blackout':True, 'auto_continue':True, 'blocks':4}, 'test-boot',1000,100)
        study.start_break({'session_id':session['id']}, 'test-boot',2500,1600)
        for now, mono, expected in ((2501,1601,'break'), (2800,1900,'ready')):
            with patch.object(study,'clock',return_value=mono), patch.object(study.time,'time',return_value=now), patch.object(guard,'user_script',return_value='us.zoom.xos') as script:
                helper.guard_study_apps()
            self.assertEqual(study.read()['session']['phase'], expected)
            source = script.call_args.args[0]
            for bundle in study.AUTO_STUDY_APPS:
                self.assertNotIn(json.dumps(bundle), source)
            self.assertIn('set frontmost of application process "Anki" to true', source)

    def test_entertainment_domains_close_without_touching_documentation(self):
        study.start({'task': {'title': 'Учёба'}, 'apps': []}, 'test-boot')
        def tabs(*urls):
            return guards.RootIntegrationTests.tabs(self, *urls)
        self.report.side_effect = [tabs('https://www.tiktok.com/@x/video/1', 'https://docs.python.org/3/'), tabs('https://docs.python.org/3/')]
        self.assertTrue(helper.guard_cycle())
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]], ['https://www.tiktok.com/@x/video/1'])
        self.assertEqual(study.read()['session']['attempts'], {'tiktok.com': 1})
        self.assertIn('127.0.0.1 tiktok.com', helper.HOSTS.read_text())

    def test_domain_matching_cannot_be_tricked_by_suffix_or_username(self):
        self.assertEqual(study.distraction_site('https://m.tiktok.com/a'), 'tiktok.com')
        self.assertIsNone(study.distraction_site('https://tiktok.com.docs.example/a'))
        self.assertIsNone(study.distraction_site('https://tiktok.com@docs.example/a'))

    def test_lecture_command_stays_available_during_focus(self):
        study.start({'task': {'title': 'Учёба'}, 'apps': []}, 'test-boot')
        playlist = guards.POLICY['playlists'][0]['id']
        output = io.StringIO()
        with patch.object(sys, 'argv', ['focus-helper', 'lease', 'youtube']), patch.object(sys, 'stdin', io.StringIO(json.dumps({'playlist_id': playlist}))), patch.object(helper.os, 'geteuid', return_value=0), contextlib.redirect_stdout(output):
            helper.main()
        self.assertIsNone(json.loads(output.getvalue())['deadline'])
        self.assertIsNone(helper.read_lease())
        self.assertEqual(study.read()['session']['phase'], 'focus')
