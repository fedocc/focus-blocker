import contextlib
import datetime as dt
import io
import json
import stat
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_youtube_guard import guard, helper, client, POLICY, VIDEO, OTHER, ANOTHER
import test_youtube_guard as fixtures


def player_html(video=OTHER, seconds='600', status='OK'):
    return 'var ytInitialPlayerResponse = '+json.dumps({'playabilityStatus':{'status':status},'videoDetails':{'videoId':video,'lengthSeconds':seconds}})+';'

class EmbeddedVideoFixTests(unittest.TestCase):
    def test_embedded_lecture_cannot_select_other_videos_or_playlists(self):
        scope=guard.study_scope(POLICY)
        for host in ('www.youtube.com','youtube-nocookie.com'):
            for query in ('','?playlist='+OTHER,'?list=unapproved&listType=playlist','?enablejsapi=1'):
                self.assertFalse(guard.url_allowed(scope,'https://'+host+'/embed/'+VIDEO+query))
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+VIDEO))
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/playlist?list='+POLICY['playlists'][0]['id']))

class VerifiedDurationTests(unittest.TestCase):
    def test_identity_status_and_finite_duration_must_match(self):
        self.assertEqual(guard.verified_video_seconds(OTHER,lambda:player_html(seconds='1800')),1920)
        for html in (player_html(video=ANOTHER),player_html(status='ERROR'),player_html(seconds='0'),player_html(seconds='nan'),player_html(seconds=True),'no player data'):
            self.assertIsNone(guard.verified_video_seconds(OTHER,lambda:html))
        self.assertEqual(guard.verified_video_seconds(OTHER,lambda:player_html(seconds='50000')),21600)
    def test_network_failure_and_bad_ids_do_not_allow_a_long_window(self):
        self.assertIsNone(guard.verified_video_seconds(OTHER,Mock(side_effect=TimeoutError())))
        fetch=Mock()
        self.assertIsNone(guard.verified_video_seconds('https://attacker.test/',fetch))
        fetch.assert_not_called()
        scope=guard.make_scope({'video_id':OTHER,'seconds':21600,'verified_seconds':21600},POLICY,{},1000)
        self.assertEqual(scope['seconds'],720)
    def test_only_server_verified_duration_can_make_a_longer_window(self):
        self.assertEqual(guard.make_scope({'video_id':OTHER},POLICY,{},1000,verified_seconds=1920)['seconds'],1920)
        self.assertEqual(guard.make_scope({'video_id':OTHER,'seconds':21600},POLICY,{},1000,verified_seconds=1920)['seconds'],1920)
        self.assertEqual(guard.make_scope({'video_id':OTHER,'seconds':120},POLICY,{},1000,verified_seconds=1920)['seconds'],120)
    def test_public_metadata_uses_no_cookies_environment_proxy_or_media_download(self):
        response=Mock();response.read.return_value=player_html().encode()
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response);opener.open.return_value.__exit__=Mock(return_value=False)
        with patch.object(guard,'build_opener',return_value=opener) as build:
            self.assertEqual(guard.verified_video_seconds(OTHER),720)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://www.youtube.com/watch?v='+OTHER+'&hl=en')
        self.assertIsNone(request.get_header('Cookie'))
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertEqual(opener.open.call_args.kwargs['timeout'],5)

class ApprovedRootFixTests(unittest.TestCase):
    setUp=fixtures.RootIntegrationTests.setUp
    tabs=fixtures.RootIntegrationTests.tabs
    regular=fixtures.RootIntegrationTests.regular
    def command(self,args,payload):
        with patch.object(helper.os,'geteuid',return_value=0),patch.object(helper.sys,'argv',['helper']+args),patch.object(helper.sys,'stdin',io.StringIO(json.dumps(payload))),contextlib.redirect_stdout(io.StringIO()):
            helper.main()
    def test_metadata_is_checked_outside_lock_before_initial_grant(self):
        # Verify the network call is outside the exclusive timer lock.
        original=helper.locked
        held=[]
        @contextlib.contextmanager
        def lock(*args,**kwargs):
            with original(*args,**kwargs) as result:
                held.append(True)
                try:yield result
                finally:held.pop()
        def metadata(video):
            self.assertEqual(video,OTHER);self.assertFalse(held);return 1920
        with patch.object(helper,'locked',lock),patch.object(guard,'verified_video_seconds',side_effect=metadata):
            self.command(['lease','youtube'],{'video_id':OTHER})
        lease=helper.read_lease()
        self.assertEqual(lease['deadline']-lease['started_at'],1920)
        self.assertEqual(lease['youtube_scope']['allowed_ids'],[OTHER])
    def test_existing_session_can_be_shortened_but_never_extended(self):
        self.regular();before=helper.STATE.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'Увеличение'):
            self.command(['youtube-duration'],21600)
        self.assertEqual(helper.STATE.read_bytes(),before)
        self.command(['youtube-duration'],60)
        current=helper.read_lease()
        self.assertEqual(current['deadline']-current['started_at'],60)
        self.assertEqual(current['deadline_monotonic']-current['started_monotonic'],60)
    def enable_series(self):
        helper.series.ENABLED.touch()
        helper.HOSTS.write_text(self.hosts+'# === FOCUS_BLOCK:SERIES START ===\n127.0.0.1 mirabreakingbad.com\n# === FOCUS_BLOCK:SERIES END ===\n')
    def series_at(self,clock):
        return dt.datetime.fromisoformat('2026-11-04T'+clock+'+03:00').timestamp()
    def observe_series(self):
        self.report.return_value=self.tabs(helper.series.SITE_URL)
        with patch('time.time',return_value=self.series_at('21:00:00')):
            self.assertTrue(helper.guard_cycle())
    def test_lost_series_observation_after_close_resets_only_affected_browser(self):
        self.enable_series();self.observe_series()
        cache=self.base/'series-observed-browsers.json'
        self.assertEqual(json.loads(cache.read_text())['browsers'],['Arc'])
        self.report.return_value=guard.BrowserReport(self.tabs('https://example.test/',name='Google Chrome'),errors={'Arc':'lost observer'})
        with patch('time.time',return_value=self.series_at('22:00:01')):
            self.assertFalse(helper.guard_cycle())
        self.force.assert_called_once_with({'Arc'})
    def test_observation_failure_within_permitted_window_does_not_revoke_series(self):
        self.enable_series();self.observe_series()
        self.report.return_value=guard.BrowserReport(errors={'Arc':'lost observer'})
        with patch('time.time',return_value=self.series_at('21:10:00')):
            self.assertFalse(helper.guard_cycle())
        self.force.assert_not_called()
    def test_generic_error_closes_remembered_series_after_window(self):
        self.enable_series();self.observe_series()
        self.report.side_effect=[RuntimeError('native helper failed'),guard.BrowserReport(errors={'Arc':'lost observer'})]
        with patch('time.time',return_value=self.series_at('22:00:01')):
            self.assertFalse(helper.guard_cycle())
        self.force.assert_called_once_with({'Arc'})
    def test_remembered_browser_survives_a_daemon_restart(self):
        self.enable_series();self.observe_series()
        cache=self.base/'series-observed-browsers.json'
        helper.OBSERVED['series']=set()
        original=Path.lstat
        def protected_info(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path==cache:return types.SimpleNamespace(st_mode=value.st_mode,st_uid=0,st_size=value.st_size)
            return value
        with patch.object(Path,'lstat',protected_info):helper.load_series_observations()
        self.assertEqual(helper.OBSERVED['series'],{'Arc'})
        self.report.return_value=guard.BrowserReport(errors={'Arc':'observer unavailable after restart'})
        with patch('time.time',return_value=self.series_at('22:00:01')):
            self.assertFalse(helper.guard_cycle())
        self.force.assert_called_once_with({'Arc'})

    def test_healthy_browser_after_series_closed_clears_remembered_target(self):
        self.enable_series();self.observe_series()
        self.report.return_value=self.tabs('https://example.test/')
        with patch('time.time',return_value=self.series_at('22:00:01')):
            self.assertTrue(helper.guard_cycle())
        self.assertEqual(helper.OBSERVED['series'],set())
        self.force.assert_not_called()

class NativeHandshakeFixTests(unittest.TestCase):
    def test_socket_can_be_served_before_open_exits(self):
        launcher=Mock(pid=987654)
        launcher.wait.side_effect=[guard.subprocess.TimeoutExpired('own-launcher',.25),0]
        with patch.object(guard.subprocess,'Popen',return_value=launcher) as start,patch.object(guard.os,'killpg') as kill:
            with guard.preview_launcher(['/own/test/launcher']):
                launcher.wait.assert_not_called()  # handshake runs now
            kill.assert_called_once_with(987654,guard.signal.SIGTERM)
        self.assertTrue(start.call_args.kwargs['start_new_session'])
    def test_completed_launcher_is_not_killed(self):
        launcher=Mock(pid=987654)
        launcher.wait.return_value=0
        with patch.object(guard.subprocess,'Popen',return_value=launcher),patch.object(guard.os,'killpg') as kill:
            with guard.preview_launcher(['/own/test/launcher']):pass
            kill.assert_not_called()


class LuLuSeriesPatternTests(unittest.TestCase):
    def test_covers_series_and_preserves_previous_blocks_without_matching_lookalikes(self):
        import re
        policy=json.loads(Path(__file__).with_name('chatgpt_lulu_policy.json').read_text())
        pattern=policy['domain_regex']
        for domain in ('mirabreakingbad.com','www.plplayer.online','kalarona.org','youtube.com','instagram.com','tiktok.com'):
            self.assertIsNotNone(re.search(pattern,'https://'+domain+'/'))
        for domain in ('example.com','openai.com','mirabreakingbad.com.evil.test'):
            self.assertIsNone(re.search(pattern,'https://'+domain+'/'))
        self.assertEqual(len(policy['application_code_ids']),3)

if __name__=='__main__':unittest.main()
