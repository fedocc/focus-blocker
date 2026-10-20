import ctypes
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from anki_study import activity_detail as detail


class DetailTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS framework smoke check')
    def test_native_descriptor_allocation_and_disposal_without_any_app_events(self):
        native = detail.NativeMetadata()
        desc = detail.AEDesc()
        code = int.from_bytes(b'utf8', 'big')
        self.assertEqual(native.ae.AECreateDesc(code, b'test', 4, ctypes.byref(desc)), 0)
        try:
            self.assertEqual(desc.descriptorType, code)
            self.assertTrue(desc.dataHandle)
        finally:
            native.ae.AEDisposeDesc(ctypes.byref(desc))
    def test_carbon_descriptor_uses_the_sdk_two_byte_layout(self):
        self.assertEqual(detail.AEDesc.dataHandle.offset, 4)
        self.assertEqual(ctypes.sizeof(detail.AEDesc), 4 + ctypes.sizeof(ctypes.c_void_p))

    def test_url_groups_sites_and_pages_without_persisting_paths_or_queries(self):
        item = detail.browser_context('https://user:secret@www.docs.test/guide?token=private#password', 'Guide')
        self.assertEqual(item['group'], 'docs.test')
        self.assertEqual(item['name'], 'Guide')
        self.assertEqual(item['key'], detail.browser_context('https://docs.test/guide?other=1', 'New title')['key'])
        self.assertNotEqual(item['key'], detail.browser_context('https://docs.test/other', 'Guide')['key'])
        for private in ('secret', 'user', 'private', '/guide', 'password'):
            self.assertNotIn(private, json.dumps(item))
        for url in ('file:///Users/example/private.pdf', 'about:blank', 'javascript:alert(1)', 'http://[bad', 'https://test:wrong/'):
            self.assertIsNone(detail.browser_context(url))

    def test_lectures_are_distinguished_but_playlist_parameters_are_not_stored(self):
        a = detail.browser_context('https://www.youtube.com/watch?v=aaaaaaaaaaa&list=private', 'Lecture A')
        b = detail.browser_context('https://youtu.be/aaaaaaaaaaa?secret=123', 'Lecture A')
        other = detail.browser_context('https://youtube.com/watch?v=bbbbbbbbbbb', 'Lecture B')
        self.assertEqual(a, b)
        self.assertNotEqual(a['key'], other['key'])
        self.assertNotIn('list', json.dumps(a))

    def test_private_windows_and_document_titles_are_coarse_and_sanitized(self):
        private = detail.browser_context('https://private.test/account', 'Secret page', private=True)
        self.assertNotIn('private.test', json.dumps(private))
        self.assertNotIn('Secret page', json.dumps(private))
        item = detail.window_context('com.microsoft.VSCode', 'main.py — graph_in_ml — Visual Studio Code')
        self.assertEqual((item['group'], item['name']), ('graph_in_ml', 'main.py'))
        item = detail.window_context('com.apple.Preview', '/Users/example/Documents/lecture.pdf')
        self.assertEqual(item['name'], 'lecture.pdf')
        self.assertIsNone(detail.window_context('com.apple.Terminal', 'secret command'))
        self.assertIsNone(detail.window_context('com.openai.codex', 'private conversation'))
        self.assertNotIn('\n', detail.clean_label('one\ntwo\x00'))

    def test_missing_permissions_never_prompt_or_launch_a_browser_during_sampling(self):
        native = SimpleNamespace(automation_allowed=Mock(return_value=False))
        reader = detail.MacDetails(native)
        with patch.object(detail.subprocess, 'run') as run:
            self.assertEqual(reader.capture('company.thebrowser.Browser', 123), (None, 'permission'))
            run.assert_not_called()
        source = detail.browser_script('company.thebrowser.Browser')
        self.assertIn('is running', source)
        self.assertIn('incognito of w', source)
        self.assertIn('mode of w', source)
        self.assertNotIn('count of windows', source)
        self.assertNotIn('activate', source)
        self.assertNotIn('execute javascript', source)
        with self.assertRaises(ValueError):
            detail.browser_script('untrusted" & do shell script "command')

    def test_browser_reader_handles_private_empty_timeouts_and_explicit_permission(self):
        reader = detail.MacDetails(SimpleNamespace(automation_allowed=lambda _: True))
        with patch.object(detail.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='PRIVATE\n')):
            item, status = reader.capture('company.thebrowser.Browser', 123)
            self.assertEqual((item['kind'], status), ('private', 'private'))
        with patch.object(detail.subprocess, 'run', side_effect=subprocess.TimeoutExpired('osascript', 2.5)):
            self.assertEqual(reader.capture('company.thebrowser.Browser', 123), (None, 'timeout'))
        with patch.object(detail.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='https://docs.test/guide\x1fGuide\n')) as run:
            item, status = reader.browser('company.thebrowser.Browser', request_access=True)
            self.assertEqual((item['group'], status), ('docs.test', 'ready'))
            self.assertEqual(run.call_args.kwargs['timeout'], 60)
            self.assertNotIn('shell', run.call_args.kwargs)
        with patch.object(detail.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='NOT_RUNNING\n')):
            self.assertEqual(reader.browser('company.thebrowser.Browser', request_access=True), (None, 'not_running'))

    def test_browser_diagnostics_keep_only_stage_and_error_number(self):
        bundle = 'company.thebrowser.Browser'
        reader = detail.MacDetails(SimpleNamespace(automation_allowed=lambda _: True))
        for stage, code, status in [('window', -1728, 'error'), ('privacy', -1743, 'permission'), ('tab', -1712, 'timeout')]:
            output = 'ERROR\x1f' + stage + '\x1f' + str(code) + '\n'
            with patch.object(detail.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)):
                self.assertEqual(reader.capture(bundle, 123), (None, status))
                self.assertEqual(reader.diagnostics[bundle], (stage, code))
                self.assertIn(str(code), reader.status_message(bundle, status))
        with patch.object(detail.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='Private page https://secret.test?token=hidden (-1728)\n')):
            self.assertEqual(reader.capture(bundle, 123), (None, 'error'))
            self.assertEqual(reader.diagnostics[bundle], ('script', -1728))
            self.assertNotIn('secret', reader.status_message(bundle, 'error'))

    def test_window_access_failure_keeps_app_totals_available(self):
        reader = detail.MacDetails(SimpleNamespace(window_title=lambda _: ('', 'accessibility')))
        self.assertEqual(reader.capture('com.microsoft.VSCode', 123), (None, 'accessibility'))
        self.assertEqual(reader.capture('com.apple.Terminal', 123), (None, 'unsupported'))


if __name__ == '__main__':
    unittest.main()
