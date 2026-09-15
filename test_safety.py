import contextlib
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import focus_open as client

loader = importlib.machinery.SourceFileLoader('helper', str(Path(__file__).with_name('focus-helper')))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)
HOSTS = '''127.0.0.1 unrelated.test
# === FOCUS_BLOCK:YOUTUBE START ===
127.0.0.1 youtube.com
::1 youtube.com
# === FOCUS_BLOCK:YOUTUBE END ===
# === FOCUS_BLOCK:INSTAGRAM START ===
127.0.0.1 instagram.com
# === FOCUS_BLOCK:INSTAGRAM END ===
# === FOCUS_BLOCK:TELEGRAM START ===
127.0.0.1 web.telegram.org
::1 web.telegram.org
# === FOCUS_BLOCK:TELEGRAM END ===
# === FOCUS_BLOCK:VKVIDEO START ===
127.0.0.1 vkvideo.ru
::1 vkvideo.ru
# === FOCUS_BLOCK:VKVIDEO END ===
'''


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in [('BASE', base), ('HOSTS', base/'hosts'), ('STATE', base/'lease.json'), ('HEARTBEAT', base/'heartbeat')]:
            self.stack.enter_context(patch.object(helper, name, value))
        helper.HOSTS.write_text(HOSTS)
        helper.HEARTBEAT.touch()
        self.stop = self.stack.enter_context(patch.object(helper, 'stop_browser'))
        self.stack.enter_context(patch.object(helper, 'flush'))
        self.stack.enter_context(patch.object(helper, 'write_hosts', side_effect=helper.HOSTS.write_text))

    def test_deadline_enforced_without_client_or_password(self):
        helper.lease('youtube', 2)
        self.assertIn(helper.PREFIX, helper.HOSTS.read_text())
        with patch.object(helper.time, 'time', return_value=time.time()+3):
            helper.expire()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.stop.assert_called_once()
        self.assertFalse(helper.STATE.exists())

    def test_telegram_expires_and_restart_restores_before_deadline(self):
        helper.lease('telegram', 900)
        helper.restore()  # daemon startup always restores outstanding leases
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        helper.lease('telegram', 900)
        with patch.object(helper.time, 'time', return_value=time.time()+901):
            helper.expire()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)

    def test_not_expired_and_manual_restore(self):
        helper.lease('instagram', 180)
        helper.expire()
        self.stop.assert_not_called()
        helper.restore()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)

    def test_daemon_down_refuses_to_unblock(self):
        helper.HEARTBEAT.unlink()
        with self.assertRaises(RuntimeError):
            helper.lease('youtube', 60)
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.assertFalse(helper.STATE.exists())

    def test_overlapping_lease_rejected(self):
        helper.lease('youtube', 60)
        with self.assertRaises(RuntimeError):
            helper.lease('instagram', 60)
        self.assertEqual(helper.read_lease()['service'], 'youtube')

    def test_reboot_restores_stale_unlock_even_without_lease(self):
        helper.HOSTS.write_text(helper.transform(HOSTS, 'youtube', True))
        helper.restore()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.stop.assert_called_once()

    def test_browser_stop_failure_still_restores_hosts(self):
        helper.lease('youtube', 60)
        with patch.object(helper, 'stop_browser', side_effect=TimeoutError('stuck')):
            with self.assertRaises(TimeoutError):
                helper.restore()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.assertTrue(helper.STATE.exists())

    def test_stdin_is_read_without_blocking_the_timer_lock(self):
        events = []
        @contextlib.contextmanager
        def lock():
            events.append('locked')
            yield
        def read(_):
            events.append('input')
            return '60'
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper.sys, 'argv', ['helper', 'lease', 'youtube']), patch.object(helper.sys, 'stdin', Mock(read=read)), patch.object(helper, 'locked', lock):
            helper.main()
        self.assertEqual(events, ['input', 'locked'])

    def test_invalid_arguments_do_not_change_hosts(self):
        for service, duration in [('other', 60), ('vkvideo', 60), ('youtube', 0), ('youtube', -1), ('youtube', 21601)]:
            with self.assertRaises(ValueError):
                helper.lease(service, duration)
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)

    def test_permanent_block_is_repaired_and_cannot_be_leased(self):
        tampered = helper.transform(HOSTS, 'vkvideo', True)
        helper.HOSTS.write_text(tampered)
        helper.enforce_permanent_blocks()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.stop.assert_called_once()
        with self.assertRaises(ValueError):
            helper.lease('vkvideo', 60)

    def test_restore_never_unblocks_permanent_block(self):
        helper.lease('youtube', 60)
        helper.restore()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)
        self.assertNotIn(helper.PREFIX + '127.0.0.1 vkvideo.ru', helper.HOSTS.read_text())

    def test_missing_permanent_block_is_recreated(self):
        without_block = HOSTS.split('# === FOCUS_BLOCK:VKVIDEO START ===')[0]
        helper.HOSTS.write_text(without_block)
        helper.enforce_permanent_blocks()
        restored = helper.HOSTS.read_text()
        self.assertIn('# === FOCUS_BLOCK:VKVIDEO START ===', restored)
        for domain in helper.PERMANENT_DOMAINS['vkvideo']:
            self.assertIn(f'127.0.0.1 {domain}\n', restored)

    def test_deadline_saved_before_hosts_open(self):
        def write(text):
            self.assertIsNotNone(helper.read_lease())
            helper.HOSTS.write_text(text)
        with patch.object(helper, 'write_hosts', side_effect=write):
            helper.lease('youtube', 60)

    def test_restore_failure_preserves_lease_for_retry(self):
        helper.lease('youtube', 60)
        with patch.object(helper, 'write_hosts', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                helper.restore()
        self.assertTrue(helper.STATE.exists())
        helper.restore()
        self.assertEqual(helper.HOSTS.read_text(), HOSTS)


class ClientTests(unittest.TestCase):
    def test_helper_never_prompts_for_password(self):
        with patch.object(client.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            client.helper_command('restore')
        self.assertEqual(run.call_args.args[0][:2], ['/usr/bin/sudo', '-n'])

    def test_sudo_command_uses_exact_arguments_and_duration_on_stdin(self):
        with patch.object(client.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            client.helper_command('lease', 'youtube', '720')
        self.assertEqual(run.call_args.args[0][-2:], ['lease', 'youtube'])
        self.assertEqual(run.call_args.kwargs['input'], '720')

    def test_duration_fallback_has_no_questions(self):
        with patch('builtins.input', side_effect=AssertionError('Unexpected question')):
            value = client.compute_access_seconds('youtube', 'https://youtu.be/abc', client.UrlValidation(True, ''))
        self.assertEqual(value, 720)

    def run_client(self, restore_fails=False):
        stack = contextlib.ExitStack()
        with stack:
            state = {'services': {}, 'history': []}
            stack.enter_context(patch.object(client, 'exclusive_lock', contextlib.nullcontext))
            stack.enter_context(patch.object(client, 'load_state', return_value=state))
            for name in ['save_state', 'helper_command', 'prepare_browser_guard', 'update_hosts_for_service', 'sleep_with_countdown', 'open_url']:
                stack.enter_context(patch.object(client, name))
            stack.enter_context(patch.object(client, 'app_is_running', return_value=True))
            stack.enter_context(patch('builtins.input', side_effect=AssertionError('Unexpected question')))
            stack.enter_context(patch.object(client, 'prompt_text', side_effect=AssertionError('Unexpected dialog')))
            reopen = stack.enter_context(patch.object(client, 'open_app'))
            restore = stack.enter_context(patch.object(client, 'restore_all_hosts_blocks', side_effect=client.FocusOpenError('failed') if restore_fails else None))
            if restore_fails:
                with self.assertRaises(client.FocusOpenError):
                    client.run_service('instagram', 'https://instagram.com/p/ABC/')
                reopen.assert_not_called()
                self.assertIn('active_unlock', state)
                self.assertIsNone(state['history'][-1]['closed_at'])
            else:
                self.assertEqual(client.run_service('instagram', 'https://instagram.com/p/ABC/'), 0)
                restore.assert_called_once()
                reopen.assert_called_once_with('Arc')

    def test_link_runs_without_confirmations(self):
        self.run_client()

    def test_failed_restore_never_reopens_browser_or_claims_success(self):
        self.run_client(restore_fails=True)


if __name__ == '__main__':
    unittest.main()
