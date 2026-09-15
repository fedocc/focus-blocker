import contextlib
import datetime as dt
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import focus_open as f
import install


class ContentLimits(unittest.TestCase):
    def test_youtube_aliases_and_tracking_share_two_uses(self):
        state={'history':[{'service':'youtube','url':'https://youtu.be/abc?t=10'}]}
        f.ensure_content_limit(state,'youtube','https://www.youtube.com/watch?v=abc&feature=share')
        state['history'].append({'service':'youtube','url':'https://m.youtube.com/watch?v=abc'})
        with self.assertRaises(f.FocusOpenError):
            f.ensure_content_limit(state,'youtube','https://youtu.be/abc')
        f.ensure_content_limit(state,'youtube','https://youtu.be/other')

    def test_instagram_plural_alias_and_tracking_share_one_use(self):
        state={'history':[{'service':'instagram','url':'https://instagram.com/reels/ABC/?igsh=x'}]}
        with self.assertRaises(f.FocusOpenError):
            f.ensure_content_limit(state,'instagram','https://www.instagram.com/reel/ABC/')
        with self.assertRaises(f.FocusOpenError):
            f.ensure_content_limit(state,'instagram','https://instagram.com/p/ABC/')
        f.ensure_content_limit(state,'instagram','https://instagram.com/p/OTHER/')

    def test_refused_grant_does_not_consume_content(self):
        state={'history':[{'service':'instagram','url':'https://instagram.com/p/ABC/','access_granted':False}]}
        f.ensure_content_limit(state,'instagram','https://instagram.com/p/ABC/')

    def test_repeat_refused_before_privileged_command(self):
        state={'history':[{'service':'instagram','url':'https://instagram.com/p/ABC/'}]}
        with patch.object(f,'exclusive_lock',contextlib.nullcontext), patch.object(f,'load_state',return_value=state), patch.object(f,'helper_command') as helper:
            with self.assertRaises(f.FocusOpenError):
                f.run_service('instagram','https://instagram.com/p/ABC/')
            helper.assert_not_called()


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        base=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key,value in [('STATE_DIR',base),('STATE_PATH',base/'state.json'),('LOCK_PATH',base/'lock')]:
            self.stack.enter_context(patch.object(f,key,value))
        self.now=dt.datetime(2026,9,9,23,55,tzinfo=dt.timezone(dt.timedelta(hours=3)))
        self.stack.enter_context(patch.object(f,'now_local',side_effect=lambda:self.now))
        self.helper=self.stack.enter_context(patch.object(f,'helper_command'))
        self.open=self.stack.enter_context(patch.object(f,'open_url'))
        self.stack.enter_context(patch.object(f,'default_browser_app',return_value='Arc'))
        self.status=self.stack.enter_context(patch.object(f,'service_block_status',return_value='blocked'))
        self.stack.enter_context(patch('builtins.input',side_effect=AssertionError('Unexpected prompt')))
        self.output=io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def test_default_15_minutes_returns_without_foreground_sleep(self):
        with patch.object(f,'sleep_with_countdown',side_effect=AssertionError('Foreground timer')):
            self.assertEqual(f.run_telegram(),0)
        self.helper.assert_any_call('lease','telegram','900')
        self.open.assert_called_once_with('https://web.telegram.org/')
        state=f.load_state()
        self.assertEqual(state['services']['telegram']['last_opened_at'],self.now.isoformat())
        ends=f.parse_iso_datetime(state['active_unlock']['ends_at'])
        self.assertEqual((ends-self.now).total_seconds(),900)
        self.assertEqual(self.output.getvalue(),'Telegram unlocked for 15 minutes.\n')

    def test_active_session_cannot_extend_across_midnight(self):
        f.run_telegram()
        self.helper.reset_mock()
        self.now+=dt.timedelta(minutes=6)
        self.status.return_value='temporarily unlocked'
        self.assertEqual(f.run_telegram(),1)
        self.helper.assert_not_called()
        self.assertIn('already active',self.output.getvalue())

    def test_youtube_cannot_interrupt_active_telegram(self):
        f.run_telegram()
        self.helper.reset_mock()
        self.status.return_value='temporarily unlocked'
        with self.assertRaises(f.FocusOpenError):
            f.run_service('youtube','https://youtu.be/abc')
        self.helper.assert_not_called()

    def test_same_calendar_day_denied_and_next_day_allowed(self):
        self.now=self.now.replace(hour=10)
        f.run_telegram()
        self.helper.reset_mock()
        self.now+=dt.timedelta(hours=2)
        self.assertEqual(f.run_telegram(),1)
        self.helper.assert_not_called()
        self.now+=dt.timedelta(days=1)
        self.assertEqual(f.run_telegram(),0)
        self.helper.assert_any_call('lease','telegram','900')

    def test_set_minutes_persists_without_using_daily_quota(self):
        f.run_telegram(10)
        self.assertFalse(f.telegram_used_today(f.load_state(),self.now))
        self.helper.assert_not_called()
        f.run_telegram()
        self.helper.assert_any_call('lease','telegram','600')
        f.run_telegram(20)
        self.assertEqual(f.run_telegram(),1)

    def test_bad_duration_rejected(self):
        for minutes in (-1,0,91):
            with self.assertRaises(f.FocusOpenError):
                f.run_telegram(minutes)
        self.helper.assert_not_called()

    def test_day_reserved_before_access_even_if_terminal_disappears(self):
        def grant(*args):
            if args[0]=='lease':
                self.assertTrue(f.telegram_used_today(f.load_state(),self.now))
        self.helper.side_effect=grant
        self.open.side_effect=f.FocusOpenError('browser failed')
        with self.assertRaises(f.FocusOpenError):
            f.run_telegram()
        self.assertTrue(f.telegram_used_today(f.load_state(),self.now))

    def test_definite_denied_lease_rolls_back_daily_reservation(self):
        def grant(*args):
            if args[0]=='lease':
                raise f.FocusOpenError('another lease active')
        self.helper.side_effect=grant
        with self.assertRaises(f.FocusOpenError):
            f.run_telegram()
        self.assertFalse(f.telegram_used_today(f.load_state(),self.now))

    def test_corrupt_state_does_not_reset_limits(self):
        f.STATE_PATH.write_text('{broken')
        with self.assertRaises(f.FocusOpenError):
            f.run_telegram()
        self.helper.assert_not_called()

    def test_local_calendar_converts_saved_offset(self):
        state={'services':{'telegram':{'last_opened_at':'2026-09-09T23:30:00+00:00'}}}
        now=dt.datetime(2026,9,10,3,tzinfo=dt.timezone(dt.timedelta(hours=3)))
        self.assertTrue(f.telegram_used_today(state,now))


class HostsTests(unittest.TestCase):
    def test_install_only_adds_web_domains_and_preserves_inbox(self):
        original='127.0.0.1 localhost\n::1 localhost\n127.0.0.1 youtube.com\n127.0.0.1 instagram.com\n'
        changed=install.telegram_hosts(original)
        self.assertTrue(changed.startswith(original))
        self.assertNotIn('8787',changed)
        self.assertNotIn('127.0.0.1 telegram.org\n',changed)
        self.assertTrue(all(domain.endswith('.web.telegram.org') or domain=='web.telegram.org' for domain in install.TELEGRAM_DOMAINS))
        self.assertEqual(install.telegram_hosts(changed),changed)

    def test_vkvideo_permanent_block_is_scoped_and_idempotent(self):
        original='127.0.0.1 localhost\n127.0.0.1 unrelated.example\n'
        changed=install.permanent_hosts(original)
        self.assertEqual(install.permanent_hosts(changed),changed)
        for domain in install.VKVIDEO_DOMAINS:
            self.assertIn(f'127.0.0.1 {domain}\n',changed)
            self.assertIn(f'::1 {domain}\n',changed)
        self.assertNotIn('127.0.0.1 vk.com\n',changed)
        self.assertNotIn('vk.ru',changed)
        self.assertNotIn('vkcdn',changed)
        self.assertTrue(changed.startswith(original))

    def test_vkvideo_has_no_cli_or_generic_unlock_route(self):
        parser=f.build_parser()
        self.assertNotIn('vkvideo',parser._subparsers._group_actions[0].choices)
        open_parser=parser._subparsers._group_actions[0].choices['open']
        service_action=next(action for action in open_parser._actions if action.dest=='service')
        self.assertNotIn('vkvideo',service_action.choices)
        self.assertNotIn('vkvideo',f.SERVICES)
        self.assertIn('vkvideo',f.PERMANENT_BLOCKS)

    def test_restore_text_repairs_permanent_block(self):
        hosts=install.permanent_hosts('127.0.0.1 localhost\n')
        unlocked=f.transform_hosts_block(hosts,'VKVIDEO',True)
        self.assertIn(f.TEMP_PREFIX,unlocked)
        self.assertEqual(f.restore_all_hosts_text(unlocked),hosts)


if __name__=='__main__':
    unittest.main()
