import json
import os
from pathlib import Path
import tempfile
import plistlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from anki_study import bridge


class BridgeTests(unittest.TestCase):
    def test_local_wallpaper_period_boundaries_and_task_labels(self):
        expected = {0:'night',4:'night',5:'predawn',6:'predawn',7:'dawn',9:'dawn',
                    10:'morning',12:'morning',13:'day',15:'day',16:'golden',17:'golden',
                    18:'evening',20:'evening',21:'night',23:'night'}
        for hour, scene in expected.items():
            self.assertEqual(bridge.wallpaper_scene(hour), scene)
        self.assertEqual(bridge.task_display_title({'title':'Лекция · Вторник'}), 'Лекция')
        for title in ['Вторник', 'Разобрать · тему', 'Лекция · 18:00']:
            self.assertEqual(bridge.task_display_title({'title':title}), title)

    def test_wallpaper_selection_validates_paths_and_supports_fixed_preview(self):
        self.assertEqual(bridge.wallpaper_settings({}, 7),
                         {'theme':'skyline', 'scene':'dawn', 'path':'wallpaper-skyline-dawn.png'})
        self.assertEqual(bridge.wallpaper_settings({'wallpaper_theme':'sea', 'wallpaper_period':'night'}, 13),
                         {'theme':'sea', 'scene':'night', 'path':'wallpaper-sea-night.png'})
        invalid = {'wallpaper_theme':'../../user_files/preferences.json', 'wallpaper_period':'../night'}
        self.assertEqual(bridge.wallpaper_settings(invalid, 13)['path'], 'wallpaper-skyline-day.png')
        for hour in range(24):
            path = bridge.wallpaper_settings({'wallpaper_theme':'landscape'}, hour)['path']
            self.assertIn(path, ['wallpaper-night.png', 'wallpaper-dawn.png', 'wallpaper-day.png', 'wallpaper-evening.png'])

    def test_remaining_uses_protected_deadline_despite_stale_response(self):
        state = {'session': {'remaining':4800, 'deadline_mono':14800}}
        with patch.object(bridge.time, 'clock_gettime', return_value=10300):
            self.assertEqual(bridge.remaining_seconds(state), 4500)
        with patch.object(bridge.time, 'clock_gettime', return_value=15000):
            self.assertEqual(bridge.remaining_seconds(state), 0)
        self.assertEqual(bridge.remaining_seconds({'session':None}), 4800)

    def test_group_summary_keeps_every_full_task_even_when_titles_are_long(self):
        tasks = [{'title':str(i)+'Я'*290,'url':bridge.ORIGIN+'/?task='+str(i),'occurrence_id':str(i)} for i in range(4)]
        group = bridge.block_task(tasks)
        self.assertLessEqual(len(group['title']), 300)
        self.assertEqual(group['items'], tasks)
        self.assertEqual(bridge.block_task(tasks[:1]), tasks[0])
        self.assertEqual(bridge.block_task([], ' Своя задача '), {'title':'Своя задача'})

    def test_one_broken_application_does_not_break_the_app_picker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in (('Valid', {'CFBundleIdentifier': 'com.example.Editor', 'CFBundleName': 'Editor'}),
                                ('InvalidXML', b'<?xml version="1.0"?><plist><dict>\x00</dict></plist>'),
                                ('ArrayRoot', []),
                                ('InvalidID', {'CFBundleIdentifier': ['not', 'a', 'string']}),
                                ('MissingID', {})):
                contents = root / (name + '.app') / 'Contents'
                contents.mkdir(parents=True)
                (contents / 'Info.plist').write_bytes(value if isinstance(value, bytes) else plistlib.dumps(value))
            self.assertEqual(bridge.applications([root]), [('com.example.Editor', 'Editor')])

    def test_transport_has_fixed_executable_and_no_user_command_interpolation(self):
        payload = {'task': {'title': '$(rm -rf /) `echo x`'}, 'apps': []}
        with patch.object(bridge.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as run:
            bridge.helper('study-start', payload)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', bridge.HELPER, 'study-start'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']), payload)
        self.assertNotIn('shell', run.call_args.kwargs)
        with self.assertRaises(ValueError):
            bridge.helper('daemon')

    def test_pairing_key_not_forwarded_to_redirect_target(self):
        with self.assertRaises(RuntimeError):
            bridge.NoRedirect().redirect_request(None,None,302,'Found',{},'https://attacker.example')

    def test_tokens_validate_before_any_network_access(self):
        for token in ('invalid', 'A'*42+'\n', 'A'*42+' '):
            with patch.object(bridge.urllib.request, 'build_opener') as opener:
                with self.assertRaises(ValueError):
                    bridge.plan(token)
                opener.assert_not_called()

    def test_preferences_are_private_and_never_written_inside_protected_state(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'user_files/preferences.json'
            bridge.save_preferences(target, {'token': 'private', 'apps': []})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text())['token'], 'private')
