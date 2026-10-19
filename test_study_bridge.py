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
