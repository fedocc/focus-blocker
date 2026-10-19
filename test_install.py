import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install
import focus_service_policy as socials
import focus_youtube_guard as guard
import focus_study as study

SOURCE = Path(__file__).resolve().parent
POLICY = json.loads((SOURCE / 'youtube-policy.json').read_text())


class PolicyAndMigrationTests(unittest.TestCase):
    def test_update_preserves_live_cache_custom_aliases_and_other_playlists(self):
        current = copy.deepcopy(POLICY)
        current['playlists'][1]['alias'] = 'my-seminars'
        current['playlists'][1]['videos'].append({'id': 'FUTUREabc12', 'title': 'New seminar', 'duration_seconds': 1200})
        current['playlists'][1]['refreshed_at'] = '2026-10-14T00:00:00+00:00'
        merged = install.merge_policy(current, POLICY)
        self.assertEqual(merged, current)
        self.assertIsNot(merged, current)

    def test_old_install_gets_new_whitelists_without_losing_its_existing_list(self):
        old = {'schema_version': 1, 'playlists': [copy.deepcopy(POLICY['playlists'][0])]}
        merged = install.merge_policy(old, POLICY)
        self.assertEqual(merged['playlists'][0], old['playlists'][0])
        self.assertEqual(len(merged['playlists']), 3)
        self.assertEqual(merged['channels'], POLICY['channels'])

    def test_invalid_existing_policy_is_not_replaced_with_defaults(self):
        with self.assertRaises(RuntimeError):
            install.merge_policy({'schema_version': 999}, POLICY)

    def test_migration_unifies_youtube_links_preserves_latest_day_and_skips_denied_grants(self):
        state = {'services': {'youtube': {'last_opened_at': '2026-10-13T12:00:00+03:00'}}, 'history': [
            {'service': 'youtube', 'url': 'https://youtu.be/aaaaaaaaaaa?t=3', 'opened_at': '2026-10-12T10:00:00+03:00'},
            {'service': 'youtube', 'url': 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'opened_at': '2026-10-13T12:00:00+03:00'},
            {'service': 'youtube', 'url': 'https://youtu.be/aaaaaaaaaaa', 'access_granted': False, 'opened_at': '2026-10-14T12:00:00+03:00'}]}
        usage = install.migrate_youtube_usage(state)
        self.assertEqual(usage['counts'], {'aaaaaaaaaaa': 2})
        self.assertEqual(socials.day_at(usage['last_regular_opened_at']), '2026-10-13')

    def test_missing_history_timezone_does_not_reset_daily_limit(self):
        with self.assertRaises(RuntimeError):
            install.migrate_youtube_usage({'services': {'youtube': {'last_opened_at': '2026-10-13T12:00:00'}}})

    def test_sudo_exception_has_exact_scope_and_all_guard_commands(self):
        rule = install.sudoers_text('focus_test')
        commands = rule.split('NOPASSWD: ')[1].strip().split(', ')
        self.assertEqual(len(commands), 15)
        self.assertIn(str(install.BASE / 'focus-helper') + ' lease x', commands)
        self.assertIn(str(install.BASE / 'focus-helper') + ' youtube-duration', commands)
        self.assertFalse(any('*' in c or c.endswith(' daemon') or 'vkvideo' in c for c in commands))

    def test_hosts_blocks_preserve_unrelated_entries_and_both_address_families(self):
        original = '127.0.0.1 localhost\n127.0.0.1 unrelated.example\n'
        changed = install.hosts_block(original, 'TELEGRAM', install.TELEGRAM_DOMAINS)
        self.assertTrue(changed.startswith(original))
        self.assertEqual(install.hosts_block(changed, 'TELEGRAM', install.TELEGRAM_DOMAINS), changed)
        self.assertIn('::1 web.telegram.org\n', changed)
        self.assertNotIn('api.telegram.org', changed)
        self.assertNotIn('127.0.0.1 telegram.org\n', changed)
        for value in ('# === FOCUS_BLOCK:X START ===\n', '# === FOCUS_BLOCK:X END ===\n# === FOCUS_BLOCK:X START ===\n'):
            with self.assertRaises(RuntimeError):
                install.hosts_block(value, 'X', socials.X_HOSTS)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.base = self.root / 'helper'
        self.base.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.hosts = self.root / 'hosts'
        self.hosts.write_text('127.0.0.1 localhost\n127.0.0.1 unrelated.example\n')
        self.plist = self.root / 'daemon.plist'
        self.rule = self.root / 'sudoers.d/focus-open'
        self.stack.enter_context(patch.multiple(install, SOURCE=SOURCE, BASE=self.base, HOSTS=self.hosts, PLIST=self.plist, RULE=self.rule))
        self.stack.enter_context(patch.multiple(guard, BASE=self.base, POLICY=self.base/'youtube-policy.json', USAGE=self.base/'youtube-usage.json'))
        self.stack.enter_context(patch.multiple(socials, BASE=self.base, USAGE=self.base/'service-usage.json'))
        self.stack.enter_context(patch.object(install.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(install.os, 'chown'))
        self.stack.enter_context(patch.object(install.pwd, 'getpwnam', return_value=SimpleNamespace(pw_dir=str(self.home), pw_uid=os.getuid(), pw_gid=os.getgid())))
        self.stack.enter_context(patch.dict(install.os.environ, {'SUDO_USER': 'focus_test'}))
        self.stack.enter_context(patch.object(install, 'secure_directory', side_effect=lambda p:p.mkdir(parents=True,exist_ok=True)))
        self.run = self.stack.enter_context(patch.object(install.subprocess, 'run', return_value=SimpleNamespace(returncode=0)))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_fresh_install_contains_modules_policy_launchers_and_protected_empty_counters(self):
        install.main()
        for name in install.ROOT_FILES:
            self.assertEqual((self.base/name).read_bytes(), (SOURCE/name).read_bytes())
        self.assertEqual(guard.read_policy()['channels'], POLICY['channels'])
        self.assertEqual(guard.read_usage(), {'last_regular_opened_at': None, 'counts': {}})
        self.assertEqual(socials.read_usage(), {'schema_version': 1, 'services': {}})
        for name in ('focus-open', 'youtube', 'instagram', 'x', 'telegram'):
            self.assertTrue((self.home/'.local/bin'/name).exists())
        self.assertIn('lease x', self.rule.read_text())
        self.assertIn('FOCUS_BLOCK:X START', self.hosts.read_text())
        self.assertIn('127.0.0.1 unrelated.example', self.hosts.read_text())

    def test_reinstall_keeps_existing_protected_quotas_and_client_history_byte_for_byte(self):
        (self.base/'youtube-policy.json').write_text(json.dumps(POLICY))
        yt = self.base/'youtube-usage.json'
        social = self.base/'service-usage.json'
        yt.write_text('{"last_regular_opened_at": 1791839043, "counts": {"aaaaaaaaaaa": 2}}\n')
        social.write_text('{"schema_version":1,"services":{"telegram":{"last_day":"2026-10-13","last_started_at":1791839043}}}\n')
        history = self.home/'.focus_open/state.json'
        history.parent.mkdir()
        history.write_text('{"services": {}, "history": []}\n')
        before = {p:p.read_bytes() for p in (yt,social,history)}
        install.main()
        self.assertEqual({p:p.read_bytes() for p in before}, before)

    def test_active_session_is_left_intact_and_hosts_are_not_changed(self):
        lease = self.base/'lease.json'
        lease.write_text('{"service":"telegram","deadline":9999999999}')
        hosts = self.hosts.read_bytes()
        with self.assertRaises(SystemExit):
            install.main()
        self.assertEqual(self.hosts.read_bytes(), hosts)
        self.assertTrue(lease.exists())
        self.run.assert_not_called()

    def test_active_study_cannot_be_reinstalled_or_refunded(self):
        state = self.base / 'study.json'
        state.write_text('{"schema":1,"session":{"phase":"focus"},"last_emergency":1792396800}')
        before = state.read_bytes()
        with self.assertRaises(SystemExit):
            install.main()
        self.assertEqual(state.read_bytes(), before)
        self.run.assert_not_called()

    def test_explicit_repair_stops_focus_and_rolls_state_back_if_install_fails(self):
        state = self.base / 'study.json'
        current = {'schema':1, 'history':[], 'last_emergency':None,
                   'session':{'id':'session-1','phase':'focus','task':{'title':'Study'},'apps':[],
                              'started_at':100,'remaining':4790,'attempts':{},'app_attempts':{}}}
        state.write_text(json.dumps(current))
        before = state.read_bytes()
        def fail(args, **kwargs):
            if args[:2] == ['/bin/launchctl','bootstrap']:
                raise subprocess.CalledProcessError(1,args)
            return SimpleNamespace(returncode=0)
        self.run.side_effect = fail
        with patch.object(study,'STATE',state):
            with self.assertRaises(subprocess.CalledProcessError):
                install.main(stop_current_study=True)
        self.assertEqual(state.read_bytes(), before)
        self.run.side_effect = None
        with patch.object(study,'STATE',state):
            install.main(stop_current_study=True)
        repaired = json.loads(state.read_text())
        self.assertIsNone(repaired['session'])
        self.assertEqual(repaired['history'][-1]['outcome'],'interrupted')
        self.assertIsNotNone(repaired['last_emergency'])

    def test_optional_anki_install_preserves_private_preferences_and_existing_addons(self):
        parent = self.home / 'Library/Application Support/Anki2/addons21'
        target = parent / 'slovo_study_blocks'
        private = target / 'user_files/preferences.json'
        private.parent.mkdir(parents=True)
        private.write_text('{"token":"keep-me"}')
        other = parent / '1011095603'
        other.mkdir()
        (other / 'unchanged').write_text('theme')
        install.main(install_anki=True)
        for name in install.ANKI_FILES:
            self.assertEqual((target / name).read_bytes(), (SOURCE / 'anki_study' / name).read_bytes())
        self.assertEqual(private.read_text(), '{"token":"keep-me"}')
        self.assertEqual((other / 'unchanged').read_text(), 'theme')

    def test_failed_bootstrap_restores_previous_code_hosts_and_rules(self):
        prior = {self.base/'focus-helper':b'previous helper',self.hosts:self.hosts.read_bytes(),self.rule:b'previous rule',self.plist:b'previous plist'}
        self.rule.parent.mkdir()
        for path,data in prior.items():path.write_bytes(data)
        failed = False
        def run(args, **kwargs):
            nonlocal failed
            if args[0:2] == ['/bin/launchctl','bootstrap'] and not failed:
                failed=True
                raise subprocess.CalledProcessError(1,args)
            return SimpleNamespace(returncode=0)
        self.run.side_effect = run
        with self.assertRaises(subprocess.CalledProcessError):install.main()
        self.assertEqual({p:p.read_bytes() for p in prior}, prior)
        self.assertFalse((self.base/'focus_playlist_sync.py').exists())
        self.assertFalse((self.base/'youtube-usage.json').exists())


if __name__ == '__main__':
    unittest.main()
