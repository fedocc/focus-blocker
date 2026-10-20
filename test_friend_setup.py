import importlib.util
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import zipfile

import prepare_friend as bundle


class FriendSetupTests(unittest.TestCase):
    def test_origin_rejects_credentials_path_redirect_and_remote_http(self):
        for bad in ('https://user:pass@study.example', 'https://study.example/api',
                    'http://study.example', 'https://study.example?token=secret',
                    'https://study.example/#fragment', 'file:///tmp/plan',
                    'https://study.example\\@other.example', 'https://a..b', 'http://localhost:0'):
            with self.subTest(origin=bad), self.assertRaises(ValueError):
                bundle.origin(bad)
        self.assertEqual(bundle.origin('https://STUDY.EXAMPLE/'), 'https://study.example')
        self.assertEqual(bundle.origin('http://127.0.0.1:3000'), 'http://127.0.0.1:3000')

    def test_prepared_bundle_has_matching_origins_complete_assets_and_no_private_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target = bundle.prepare('https://study.example', Path(folder) / 'fresh')
            for name in ('anki_study/bridge.py', 'focus_study.py'):
                text = (target / name).read_text()
                self.assertIn('https://study.example', text)
                self.assertNotIn(bundle.ORIGINAL_ORIGIN, text)
            names = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
            self.assertFalse(any('user_files' in n or '.git/' in n or n.endswith('preferences.json') or n.endswith('study.json') for n in names))
            self.assertFalse((target / 'anki_study/user_files').exists())
            self.assertEqual(json.loads((target / 'instance.json').read_text())['planner_origin'], 'https://study.example')
            with zipfile.ZipFile(target / 'slovo-study-blocks.ankiaddon') as archive:
                self.assertEqual(set(archive.namelist()), set(bundle.ADDON_FILES))
                self.assertIn(b'https://study.example', archive.read('bridge.py'))
            with self.assertRaises(ValueError):
                bundle.prepare('https://other.example', target)

    def test_installer_file_list_includes_statistics(self):
        install = runpy.run_path(str(bundle.ROOT / 'install.py'), run_name='test_friend_install')
        self.assertEqual(set(install['ANKI_FILES']), set(bundle.ADDON_FILES))

    def test_custom_planner_task_url_is_accepted_but_foreign_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = bundle.prepare('https://study.example', Path(folder) / 'fresh')
            spec = importlib.util.spec_from_file_location('friend_focus_study', target / 'focus_study.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.clean_task({'title': 'DL', 'url': 'https://study.example/?id=1'})['title'], 'DL')
            for bad in ('https://study.example.evil/?id=1', bundle.ORIGINAL_ORIGIN + '/?id=1'):
                with self.assertRaises(ValueError):
                    module.clean_task({'title': 'DL', 'url': bad})

    def test_shared_theme_has_appearance_but_no_identity_or_game_state(self):
        with zipfile.ZipFile(bundle.ROOT / 'theme/focus-study.onigiri') as archive:
            theme = json.loads(archive.read('theme.json'))
        config = theme['customization']['addon_config']
        self.assertEqual(config['userName'], 'Ученик')
        self.assertFalse(config['study_tools_visibility']['pomodoro'])
        for forbidden in ('userBirthday', 'achievements', 'hashi_notes', 'onigiriProfile', 'onigimon', 'hexagon_land'):
            self.assertNotIn(forbidden, config)
        self.assertIn('onigiriWidgetLayout', config)


if __name__ == '__main__':
    unittest.main()
