import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import focus_youtube_guard as guard
import focus_open as client
import focus_service_policy as socials

STAGE = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('test_root_helper', str(STAGE / 'focus-helper'))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)

POLICY = json.loads((STAGE / 'youtube-policy.json').read_text())
COURSE = POLICY['playlists'][0]
VIDEO = COURSE['videos'][0]['id']
OTHER = 'aaaaaaaaaaa'
ANOTHER = 'bbbbbbbbbbb'


class PolicyTests(unittest.TestCase):
    def test_entire_channel_is_permanent_and_does_not_consume_daily_access(self):
        channel = POLICY['channels'][0]
        usage = {'last_regular_opened_at':1000,'counts':{OTHER:999}}
        before = copy.deepcopy(usage)
        scope = guard.make_scope({'channel_id':channel['id']},POLICY,usage,1001)
        self.assertIsNone(scope['seconds'])
        self.assertEqual(usage,before)
        self.assertEqual(len(scope['channel_video_ids']),16)
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/watch?v=CibAgUC67x0'))
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/channel/'+channel['id']+'/videos'))
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/@Moptori-FCS/videos'))
        self.assertFalse(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+OTHER+'&channel_id='+channel['id']))

    def test_channel_request_cannot_authorize_an_arbitrary_video(self):
        channel_id=POLICY['channels'][0]['id']
        for request in ({'channel_id':channel_id,'video_id':OTHER}, {'channel_id':'UC'+'x'*22}, {'channel_id':channel_id,'playlist_id':COURSE['id']}):
            with self.assertRaises(ValueError):guard.make_scope(request,POLICY,{},1000)

    def test_other_channel_and_similar_handle_stay_blocked(self):
        scope=guard.study_scope(POLICY)
        for url in ('https://www.youtube.com/@Moptori-FCS-evil/videos','https://www.youtube.com/channel/UC'+'x'*22+'/videos','https://www.youtube.com/feed/subscriptions','https://www.youtube.com/results?search_query=seminar','https://www.youtube.com/shorts/'+OTHER):
            self.assertFalse(guard.url_allowed(scope,url),url)
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/shorts/CibAgUC67x0'))
    def test_course_ignores_entertainment_cooldown_and_repeat_count(self):
        usage = {'last_regular_opened_at': 1000, 'counts': {VIDEO: 999}}
        before = copy.deepcopy(usage)
        scope = guard.make_scope({'playlist_id': COURSE['id']}, POLICY, usage, 1001)
        self.assertEqual(scope['mode'], 'playlist')
        self.assertEqual(set(scope['allowed_ids']), {v['id'] for p in POLICY['playlists'] for v in p['videos']})
        self.assertEqual(usage, before)

    def test_direct_lecture_link_is_recognized_without_trusting_list_parameter(self):
        scope = guard.make_scope({'video_id': VIDEO}, POLICY, {'last_regular_opened_at': 1000}, 1001)
        self.assertEqual(scope['mode'], 'playlist')
        with self.assertRaises(ValueError):
            guard.make_scope({'playlist_id': 'unapproved'}, POLICY, {}, 1000)

    def test_ordinary_session_allows_one_id_and_enforces_root_cooldown(self):
        scope = guard.make_scope({'video_id': OTHER, 'seconds': 120}, POLICY, {}, 1000)
        self.assertEqual(scope['allowed_ids'], [OTHER])
        self.assertTrue(guard.url_allowed(scope, 'https://youtu.be/' + OTHER))
        self.assertFalse(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + ANOTHER))
        with self.assertRaises(RuntimeError):
            guard.make_scope({'video_id': OTHER}, POLICY, {'last_regular_opened_at': 1000}, 1001)

    def test_permanent_study_whitelist_survives_an_ordinary_video_session(self):
        scope = guard.make_scope({'video_id': OTHER}, POLICY, {}, 1000)
        self.assertTrue(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + VIDEO))
        self.assertTrue(guard.url_allowed(scope, 'https://www.youtube.com/playlist?list=' + COURSE['id']))
        self.assertFalse(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + ANOTHER + '&list=' + COURSE['id']))

    def test_caller_cannot_add_an_arbitrary_video_to_permanent_whitelist(self):
        scope = guard.make_scope({'video_id': OTHER, 'study_ids': [ANOTHER], 'playlist_ids': ['fake']}, POLICY, {}, 1000)
        self.assertNotIn(ANOTHER, scope['study_ids'])
        self.assertNotIn('fake', scope['playlist_ids'])

    def test_playlist_parameter_does_not_allow_an_outside_video(self):
        scope = guard.make_scope({'playlist_id': COURSE['id']}, POLICY, {}, 1000)
        self.assertFalse(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + OTHER + '&list=' + COURSE['id']))
        self.assertTrue(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + VIDEO))
        self.assertTrue(guard.url_allowed(scope, 'https://www.youtube.com/playlist?list=' + COURSE['id']))

    def test_query_spoofing_and_impulsive_surfaces_are_rejected(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO]}
        bad = [
            'https://www.youtube.com/watch?v=' + VIDEO + '&v=' + OTHER,
            'https://www.youtube.com/',
            'https://www.youtube.com/shorts/' + VIDEO,
            'https://www.youtube.com/results?search_query=movie',
            'https://www.youtube.com@evil.example/watch?v=' + VIDEO,
            'https://www.youtube.com/watch?v=' + OTHER + '&list=' + COURSE['id'],
        ]
        for value in bad:
            self.assertFalse(guard.url_allowed(scope, value), value)

    def test_broad_legacy_lease_and_arbitrary_allowed_ids_cannot_bypass_policy(self):
        for request in [300, {}, {'allowed_ids': [OTHER]}, {'video_id': 'bad'}, {'playlist_id': COURSE['id'], 'video_id': OTHER}]:
            with self.assertRaises(ValueError):
                guard.make_scope(request, POLICY, {}, 1000)


class DailyPolicyTests(unittest.TestCase):
    def test_all_social_services_use_one_moscow_day_and_exact_durations(self):
        import datetime as dt
        last=dt.datetime(2026,10,13,0,4,tzinfo=socials.TIMEZONE).timestamp()
        later=dt.datetime(2026,10,13,23,59,tzinfo=socials.TIMEZONE).timestamp()
        tomorrow=dt.datetime(2026,10,14,0,0,tzinfo=socials.TIMEZONE).timestamp()
        for service,seconds in (('instagram',120),('x',120),('telegram',420)):
            usage=socials.reserve(service,{'schema_version':1,'services':{}},last)
            with self.assertRaisesRegex(RuntimeError,'сегодня'):socials.validate(service,seconds,usage,later)
            socials.validate(service,seconds,usage,tomorrow)
            with self.assertRaises(ValueError):socials.validate(service,seconds+1,usage,tomorrow)
            with self.assertRaises(ValueError):socials.validate(service,21600,usage,tomorrow)

    def test_x_and_instagram_daily_allowances_are_independent(self):
        usage=socials.reserve('instagram',{'schema_version':1,'services':{}},time.time())
        socials.validate('x',120,usage,time.time())
        socials.validate('telegram',420,usage,time.time())

    def test_regular_youtube_uses_day_instead_of_rolling_six_hour_cooldown(self):
        import datetime as dt
        last=dt.datetime(2026,10,13,0,4,tzinfo=socials.TIMEZONE).timestamp()
        same_day=dt.datetime(2026,10,13,20,0,tzinfo=socials.TIMEZONE).timestamp()
        tomorrow=dt.datetime(2026,10,14,0,0,tzinfo=socials.TIMEZONE).timestamp()
        usage={'last_regular_opened_at':last,'counts':{}}
        with self.assertRaisesRegex(RuntimeError,'сегодня'):guard.make_scope({'video_id':OTHER},POLICY,usage,same_day)
        self.assertEqual(guard.make_scope({'video_id':OTHER},POLICY,usage,tomorrow)['allowed_ids'],[OTHER])

    def test_late_evening_session_can_open_again_after_calendar_day_changes(self):
        import datetime as dt
        last=dt.datetime(2026,10,13,23,59,tzinfo=socials.TIMEZONE).timestamp()
        next_day=last+60
        for name,seconds in socials.DURATIONS.items():
            usage=socials.reserve(name,{'schema_version':1,'services':{}},last)
            socials.validate(name,seconds,usage,next_day)

    def test_existing_instagram_record_is_not_reset_when_switching_to_daily(self):
        now=time.time()
        usage={'schema_version':1,'services':{'instagram':{'last_started_at':now-3*3600,'last_day':socials.day_at(now)}}}
        with self.assertRaises(RuntimeError):socials.validate('instagram',120,usage,now)

    def test_x_domains_legacy_and_media_are_controlled_without_lookalike_hosts(self):
        for url in ('https://x.com/home','https://twitter.com/user/status/1','https://mobile.x.com/','https://video.twimg.com/clip.mp4','https://platform.twitter.com/widgets.js'):
            self.assertEqual(socials.service_for_url(url),'x')
            self.assertTrue(guard.is_controlled_url(url))
        self.assertIsNone(socials.service_for_url('https://x.com.example.org/'))
        self.assertIsNone(socials.service_for_url('https://notx.com/'))

    def test_daily_limit_does_not_apply_to_whitelisted_lecture(self):
        scope=guard.make_scope({'video_id':VIDEO},POLICY,{'last_regular_opened_at':time.time(),'counts':{VIDEO:999}},time.time())
        self.assertIsNone(scope['seconds'])
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+VIDEO))


class PreviewPolicyTests(unittest.TestCase):
    def test_shared_google_viewer_link_is_a_controlled_denied_surface(self):
        url='https://www.google.com/search?sca_esv=bfa957c708c098d7&udm=vids&q=%D0%BC%D0%B0%D1%80%D0%BC%D0%BE%D0%BA+%D1%8E%D1%82%D1%83%D0%B1#fpstate=ive&vld=cid:ab9322e2,vid:koYrPMjjOJ4,st:0'
        self.assertTrue(guard.is_video_preview_url(url))
        self.assertTrue(guard.is_controlled_url(url))
        self.assertFalse(guard.url_allowed(guard.study_scope(POLICY),url))
        regular=guard.make_scope({'video_id':'koYrPMjjOJ4'},POLICY,{},time.time())
        self.assertFalse(guard.url_allowed(regular,url))

    def test_video_searches_and_viewers_are_denied_across_google_regions(self):
        for host in ('google.com','www.google.ru','www.google.co.uk','www.google.com.tr'):
            for path in ('/search?tbm=vid','/search?udm=7','/search?udm=vids','/search/#fpstate=ive','/search#vld=vid:aaaaaaaaaaa','/videohp'):
                self.assertTrue(guard.is_video_preview_url('https://'+host+path),(host,path))

    def test_regular_search_images_mail_and_maps_are_untouched(self):
        for url in ('https://www.google.com/search?q=deep+learning','https://www.google.com/search?udm=2&q=convolution','https://www.google.ru/search?tbm=isch','https://mail.google.com/mail/u/0/','https://www.google.com/maps','https://example.com/search?udm=vids'):
            self.assertFalse(guard.is_video_preview_url(url),url)
            self.assertFalse(guard.is_controlled_url(url),url)

    def test_spoofing_an_approved_id_in_viewer_hash_does_not_allow_player(self):
        url='https://www.google.com/search?q=video#fpstate=ive&vld=vid:'+VIDEO
        self.assertFalse(guard.url_allowed(guard.study_scope(POLICY),url))

    def test_direct_lecture_and_single_video_rules_are_preserved(self):
        self.assertTrue(guard.url_allowed(guard.study_scope(POLICY),'https://www.youtube.com/watch?v='+VIDEO))
        scope=guard.make_scope({'video_id':OTHER},POLICY,{},time.time())
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+OTHER))
        self.assertFalse(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+ANOTHER))

    def test_google_viewer_is_closed_by_stable_id_without_javascript_or_page_reads(self):
        url='https://www.google.com/search#fpstate=ive&vld=vid:'+OTHER
        target={'application':'Arc','kind':'id','window':'w','tab':'t','url':url}
        with patch.object(guard,'user_script',return_value='OK') as native:
            guard.close_tabs([target])
        source=native.call_args.args[0]
        self.assertIn('if tabId is "t" then',source)
        self.assertNotIn('javascript',source.lower())

    def test_observer_retains_only_preview_address_not_google_page_content(self):
        url='https://www.google.com/search#fpstate=ive&vld=vid:'+OTHER
        out='T'+chr(31)+'Arc'+chr(31)+'w'+chr(31)+'t'+chr(31)+url+'\nB'+chr(31)+'Arc'+chr(31)+'1\n'
        with patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'user_script',return_value=out) as native:
            report=guard.browser_report()
        self.assertEqual(report[0]['tabs'][0]['url'],url)
        self.assertNotIn('javascript',native.call_args.args[0].lower())


class ObserverTests(unittest.TestCase):
    def report(self, *ids):
        return [{'name': 'Arc', 'kind': 'id', 'tabs': [
            {'window': 'w1', 'tab': str(i), 'url': 'https://www.youtube.com/watch?v=' + value}
            for i, value in enumerate(ids)]}]

    def test_new_or_background_tab_on_other_video_revokes_even_if_next_poll_would_be_allowed(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO], 'grace_until': 0}
        before = self.report(VIDEO, OTHER)
        before.append({'name': 'Google Chrome', 'kind': 'id', 'tabs': [
            {'window': 'w2', 'tab': '42', 'url': 'https://www.youtube.com/watch?v=' + ANOTHER}]})
        with patch.object(guard, 'browser_report', side_effect=[before, self.report(VIDEO)]), patch.object(guard, 'close_tabs') as close:
            with self.assertRaisesRegex(RuntimeError, 'завершил доступ'):
                guard.inspect_session(scope, 1000)
        close.assert_not_called()

    def test_stable_id_is_closed_even_if_its_url_changed(self):
        target = {'application': 'Arc', 'window': 'w1', 'tab': 't1', 'kind': 'id',
                  'url': 'https://www.youtube.com/watch?v=' + OTHER}
        with patch.object(guard, 'user_script', return_value='OK') as script:
            guard.close_tabs([target])
        source = script.call_args.args[0]
        self.assertIn('if tabId is "t1" then', source)
        self.assertNotIn('and ((URL of t)', source)

    def test_reopening_youtube_without_a_lease_closes_tab_and_preserves_browser(self):
        with patch.object(guard, 'browser_report', side_effect=[self.report(OTHER), self.report()]), patch.object(guard, 'close_tabs') as close, patch.object(guard, 'force_stop_browsers') as stop:
            guard.enforce_idle_youtube(guard.study_scope(POLICY))
        close.assert_called_once()
        stop.assert_not_called()

    def test_idle_enforcement_preserves_browsers_without_youtube_tabs(self):
        report = [{'name': 'Arc', 'kind': 'id', 'tabs': []}]
        with patch.object(guard, 'browser_report', return_value=report), patch.object(guard, 'force_stop_browsers') as stop:
            guard.enforce_idle_youtube(guard.study_scope(POLICY))
        stop.assert_not_called()

    def test_idle_enforcement_preserves_approved_lecture_without_any_lease(self):
        report = self.report(VIDEO)
        with patch.object(guard, 'browser_report', return_value=report), patch.object(guard, 'close_tabs') as close:
            self.assertEqual(guard.enforce_idle_youtube(guard.study_scope(POLICY)), report)
        close.assert_not_called()

    def test_idle_enforcement_removes_only_outside_video_next_to_lecture(self):
        with patch.object(guard, 'browser_report', side_effect=[self.report(VIDEO, OTHER), self.report(VIDEO)]), patch.object(guard, 'close_tabs') as close:
            report = guard.enforce_idle_youtube(guard.study_scope(POLICY))
        self.assertEqual(len(report[0]['tabs']), 1)
        self.assertEqual(close.call_args.args[0][0]['url'], 'https://www.youtube.com/watch?v=' + OTHER)

    def test_automatic_refresh_does_not_overwrite_a_changed_tab_url(self):
        target = {'application': 'Arc', 'window': 'w', 'tab': 't', 'kind': 'id',
                  'url': 'https://www.youtube.com/watch?v=' + VIDEO}
        with patch.object(guard, 'user_script', return_value='OK') as script:
            guard.refresh_tabs([target])
        self.assertIn('and ((URL of t) as text) is "' + target['url'] + '" then', script.call_args.args[0])

    def test_failed_closing_or_unreadable_browser_does_not_continue_access(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO], 'grace_until': 0}
        with patch.object(guard, 'browser_report', return_value=self.report(OTHER)), patch.object(guard, 'close_tabs'):
            with self.assertRaises(RuntimeError):
                guard.inspect_session(scope, 1000)
        with patch.object(guard, 'browser_report', side_effect=RuntimeError('No automation permission')):
            with self.assertRaises(RuntimeError):
                guard.inspect_session(scope, 1000)

    def test_initial_loading_has_a_bounded_grace(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO], 'grace_until': 1015}
        with patch.object(guard, 'browser_report', return_value=[]):
            guard.inspect_session(scope, 1000)
            with self.assertRaises(RuntimeError):
                guard.inspect_session(scope, 1016)

    def test_observer_does_not_launch_a_closed_browser(self):
        with patch.object(guard, 'browser_processes', return_value=set()), patch.object(guard, 'user_script') as script:
            self.assertEqual(guard.browser_report(), [])
        script.assert_not_called()

    def test_observer_checks_running_before_entering_tell(self):
        output = 'B' + chr(31) + 'Arc' + chr(31) + '7\n'
        with patch.object(guard, 'browser_processes', return_value={'Arc'}), patch.object(guard, 'user_script', return_value=output) as script:
            report = guard.browser_report()
        source = script.call_args.args[0]
        self.assertLess(source.index('if application "Arc" is running then'), source.index('tell application "Arc"'))
        self.assertEqual(report[0]['tabs'], [])

    def test_restored_outside_video_is_removed_before_access_is_granted(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO]}
        with patch.object(guard, 'browser_report', side_effect=[self.report(VIDEO, OTHER), self.report(VIDEO)]), patch.object(guard, 'close_tabs') as close, patch.object(guard, 'force_stop_browsers') as force:
            guard.prepare_session(scope)
        self.assertEqual(len(close.call_args.args[0]), 1)
        self.assertEqual(close.call_args.args[0][0]['url'], 'https://www.youtube.com/watch?v=' + OTHER)
        force.assert_not_called()

    def test_failed_startup_cleanup_does_not_allow_unblocking(self):
        scope = {'mode': 'video', 'allowed_ids': [VIDEO]}
        with patch.object(guard, 'browser_report', return_value=self.report(OTHER)), patch.object(guard, 'close_tabs'):
            with self.assertRaisesRegex(RuntimeError, 'остаётся закрыт'):
                guard.prepare_session(scope)


class RootIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.multiple(helper, BASE=self.base, STATE=self.base / 'lease.json', HOSTS=self.base / 'hosts', HEARTBEAT=self.base / 'heartbeat', OBSERVED={s:set() for s in helper.TEMPORARY_SERVICES}))
        self.stack.enter_context(patch.multiple(guard, BASE=self.base, HEALTH=self.base / 'health.json', LAST_EVENT=self.base / 'event.json', USAGE=self.base / 'usage.json', POLICY=self.base / 'policy.json'))
        self.stack.enter_context(patch.object(socials, 'USAGE', self.base / 'socials.json'))
        guard.POLICY.write_text(json.dumps(POLICY))
        socials.USAGE.write_text(json.dumps({'schema_version':1, 'services':{}}))
        self.hosts = ''.join('# === FOCUS_BLOCK:' + s.upper() + ' START ===\n127.0.0.1 ' + s + '.test\n# === FOCUS_BLOCK:' + s.upper() + ' END ===\n' for s in helper.TEMPORARY_SERVICES)
        helper.HOSTS.write_text(self.hosts)
        helper.HEARTBEAT.touch()
        guard.publish_health(True)
        self.stack.enter_context(patch.object(helper, 'write_hosts', side_effect=lambda value: helper.HOSTS.write_text(value)))
        self.stack.enter_context(patch.object(helper, 'flush'))
        self.stack.enter_context(patch.object(helper, 'timer_running', return_value=True))
        self.report = self.stack.enter_context(patch.object(guard, 'browser_report', return_value=[]))
        self.close = self.stack.enter_context(patch.object(guard, 'close_tabs'))
        self.force = self.stack.enter_context(patch.object(guard, 'force_stop_browsers'))

    def tabs(self, *urls, name='Arc'):
        return [{'name':name, 'kind':'id', 'tabs':[{'window':'w','tab':str(i),'url':url} for i,url in enumerate(urls)], 'total_tabs':len(urls)+7}]

    def regular(self):
        scope=guard.make_scope({'video_id':OTHER,'seconds':120}, POLICY,{},time.time())
        helper.lease('youtube',120,scope)

    def test_permanent_lectures_have_no_deadline_and_no_lease(self):
        self.report.return_value=self.tabs('https://youtu.be/'+VIDEO)
        for now in (time.time(), time.time()+21601, time.time()+3*86400):
            with patch.object(helper.time,'time',return_value=now):
                helper.guard_cycle()
                helper.expire()
        self.assertIsNone(guard.study_scope(POLICY)['seconds'])
        self.assertIsNone(helper.read_lease())
        self.assertIn(helper.PREFIX,helper.HOSTS.read_text())
        self.close.assert_not_called()
        self.force.assert_not_called()

    def test_empty_browser_keeps_whitelist_ready_without_relaunch(self):
        helper.guard_cycle()
        self.assertIsNone(helper.read_lease())
        self.assertIn(helper.PREFIX,helper.HOSTS.read_text())
        self.close.assert_not_called()

    def test_denial_next_to_lecture_closes_only_outside_video(self):
        lecture='https://youtu.be/'+VIDEO
        denied='https://youtu.be/'+OTHER
        self.report.side_effect=[self.tabs(lecture,denied),self.tabs(lecture,denied),self.tabs(lecture)]
        self.assertTrue(helper.guard_cycle())
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]],[denied])
        self.assertIsNone(helper.read_lease())
        self.force.assert_not_called()

    def test_denial_revokes_ordinary_and_keeps_lecture_policy(self):
        self.regular()
        before=self.tabs('https://youtu.be/'+VIDEO,'https://youtu.be/'+OTHER,'https://youtu.be/'+ANOTHER)
        self.report.side_effect=[before,before,self.tabs('https://youtu.be/'+VIDEO)]
        helper.guard_cycle()
        self.assertIsNone(helper.read_lease())
        self.assertEqual({t['url'] for t in self.close.call_args.args[0]}, {'https://youtu.be/'+OTHER,'https://youtu.be/'+ANOTHER})
        self.assertIn(helper.PREFIX,helper.HOSTS.read_text())
        self.force.assert_not_called()

    def test_expiring_regular_video_preserves_lecture_and_browser(self):
        self.regular()
        self.report.side_effect=[self.tabs('https://youtu.be/'+VIDEO,'https://youtu.be/'+OTHER),self.tabs('https://youtu.be/'+VIDEO),self.tabs('https://youtu.be/'+VIDEO)]
        value=helper.read_lease();value['deadline']=time.time()-1;guard.atomic_json(helper.STATE,value)
        helper.expire();helper.guard_cycle()
        self.assertIsNone(helper.read_lease())
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]],['https://youtu.be/'+OTHER])
        self.force.assert_not_called()

    def test_restart_preserves_lecture_without_renewing_regular_session(self):
        self.regular()
        self.report.return_value=self.tabs('https://youtu.be/'+VIDEO)
        helper.startup();helper.guard_cycle()
        self.assertIsNone(helper.read_lease())
        self.assertIn(helper.PREFIX,helper.HOSTS.read_text())
        self.force.assert_not_called()
        self.assertEqual(self.close.call_args.args[0],[])

    def test_backward_wall_clock_cannot_extend_active_social_timer(self):
        helper.lease('telegram',420)
        current=helper.read_lease()
        with patch.object(helper.time,'time',return_value=current['started_at']-3600),patch.object(helper,'lease_clock',return_value=current['deadline_monotonic']+1):
            helper.expire()
        self.assertIsNone(helper.read_lease())
        self.assertNotIn(helper.PREFIX,helper.HOSTS.read_text())

    def test_telegram_duration_and_day_limits_survive_restore_and_client_state_reset(self):
        for seconds in (1,421,21600):
            with self.assertRaises(ValueError):helper.lease('telegram',seconds)
        helper.lease('telegram',420)
        helper.restore()
        # This simulated caller has no user state at all. The root reservation
        # still rejects a second grant, independent of the terminal client.
        with self.assertRaisesRegex(RuntimeError,'сегодня'):helper.lease('telegram',420)
        self.assertIsNone(helper.read_lease())

    def test_instagram_duration_and_cooldown_are_protected(self):
        with self.assertRaises(ValueError):helper.lease('instagram',21600)
        helper.lease('instagram',120)
        helper.restore()
        with self.assertRaisesRegex(RuntimeError,'Instagram уже открывался'):helper.lease('instagram',120)

    def test_missing_protected_social_usage_fails_closed(self):
        socials.USAGE.unlink()
        with self.assertRaises(FileNotFoundError):helper.lease('telegram',420)
        self.assertIsNone(helper.read_lease())
        self.assertNotIn(helper.PREFIX,helper.HOSTS.read_text())

    def test_cached_telegram_in_chrome_is_closed_without_killing_arc(self):
        helper.lease('telegram',420)
        self.report.side_effect=[self.tabs('https://web.telegram.org/k/',name='Google Chrome'),[]]
        value=helper.read_lease();value['deadline']=time.time()-1;guard.atomic_json(helper.STATE,value)
        helper.expire()
        self.assertEqual(self.close.call_args.args[0][0]['application'],'Google Chrome')
        self.force.assert_not_called()
        self.assertIsNone(helper.read_lease())

    def test_lecture_stays_available_during_telegram_window(self):
        helper.lease('telegram',420)
        self.report.return_value=self.tabs('https://youtu.be/'+VIDEO,'https://web.telegram.org/k/')
        helper.guard_cycle()
        self.assertEqual(helper.read_lease()['service'],'telegram')
        self.close.assert_not_called()
        self.force.assert_not_called()

    def test_outside_youtube_does_not_revoke_unrelated_telegram_window(self):
        helper.lease('telegram',420)
        before=self.tabs('https://youtu.be/'+OTHER,'https://web.telegram.org/k/')
        self.report.side_effect=[before,before,self.tabs('https://web.telegram.org/k/')]
        helper.guard_cycle()
        self.assertEqual(helper.read_lease()['service'],'telegram')
        self.force.assert_not_called()

    def test_failed_observer_blocks_hosts_and_stops_only_observed_connections(self):
        helper.guard_cycle()
        helper.OBSERVED['youtube']={'Arc'}
        self.report.side_effect=RuntimeError('No automation permission')
        self.assertFalse(helper.guard_cycle())
        self.force.assert_called_once_with({'Arc'})
        self.assertNotIn(helper.PREFIX,helper.HOSTS.read_text())
        self.assertFalse(json.loads(guard.HEALTH.read_text())['ok'])

    def test_preview_closure_preserves_direct_lecture_and_authorized_telegram(self):
        helper.lease('telegram',420)
        before=helper.read_lease()
        lecture='https://www.youtube.com/watch?v='+VIDEO
        preview='https://www.google.com/search?udm=vids#fpstate=ive&vld=vid:'+OTHER
        before_report=self.tabs(lecture,'https://web.telegram.org/k/',preview)
        after_report=self.tabs(lecture,'https://web.telegram.org/k/')
        self.report.side_effect=[before_report,before_report,after_report]
        helper.guard_cycle()
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]],[preview])
        self.assertEqual(helper.read_lease()['deadline'],before['deadline'])
        self.assertEqual(helper.read_lease()['service'],'telegram')
        self.force.assert_not_called()

    def test_ordinary_grant_cannot_keep_google_preview_alive(self):
        self.regular()
        preview='https://www.google.com/search#fpstate=ive&vld=vid:'+OTHER
        before=self.tabs('https://www.youtube.com/watch?v='+OTHER,preview)
        self.report.side_effect=[before,before,[]]
        helper.guard_cycle()
        self.assertIsNone(helper.read_lease())
        self.assertEqual({t['url'] for t in self.close.call_args.args[0]}, {'https://www.youtube.com/watch?v='+OTHER,preview})
        self.force.assert_not_called()

    def test_chrome_timeout_keeps_arc_telegram_and_its_exact_deadline(self):
        helper.lease('telegram',420)
        before=helper.read_lease()
        helper.OBSERVED['telegram']={'Arc'}
        report=guard.BrowserReport(self.tabs('https://web.telegram.org/k/'),{'Google Chrome':'timeout'})
        self.report.return_value=report
        self.assertFalse(helper.guard_cycle())
        after=helper.read_lease()
        self.assertEqual(after['deadline'],before['deadline'])
        self.assertEqual(after['deadline_monotonic'],before['deadline_monotonic'])
        self.assertEqual(after['service'],'telegram')
        self.assertIn(helper.PREFIX+'127.0.0.1 telegram.test',helper.HOSTS.read_text())
        self.assertNotIn(helper.PREFIX+'127.0.0.1 youtube.test',helper.HOSTS.read_text())
        self.close.assert_not_called()
        self.force.assert_not_called()

    def test_repeated_chrome_timeouts_cannot_erase_or_extend_telegram(self):
        helper.lease('telegram',420)
        deadline=helper.read_lease()['deadline']
        self.report.return_value=guard.BrowserReport(self.tabs('https://web.telegram.org/k/'),{'Google Chrome':'timeout'})
        for _ in range(4):helper.guard_cycle()
        self.assertEqual(helper.read_lease()['deadline'],deadline)
        self.force.assert_not_called()
        self.close.assert_not_called()

    def test_failed_youtube_browser_reset_does_not_select_healthy_telegram_browser(self):
        helper.lease('telegram',420)
        helper.OBSERVED['youtube']={'Google Chrome'}
        helper.OBSERVED['telegram']={'Arc'}
        self.report.return_value=guard.BrowserReport(self.tabs('https://web.telegram.org/k/'),{'Google Chrome':'timeout'})
        helper.guard_cycle()
        self.force.assert_called_once_with({'Google Chrome'})
        self.assertEqual(helper.read_lease()['service'],'telegram')

    def test_telegram_expiry_still_closes_its_tabs_despite_other_browser_failure(self):
        helper.lease('telegram',420)
        value=helper.read_lease();value['deadline']=time.time()-1;guard.atomic_json(helper.STATE,value)
        self.report.side_effect=[guard.BrowserReport(self.tabs('https://web.telegram.org/k/'),{'Google Chrome':'timeout'}),[]]
        helper.expire()
        self.assertIsNone(helper.read_lease())
        self.assertEqual(self.close.call_args.args[0][0]['application'],'Arc')
        self.force.assert_not_called()

    def test_unexpected_observer_error_preserves_valid_social_deadline(self):
        helper.lease('telegram',420)
        before=helper.read_lease()
        self.report.side_effect=[RuntimeError('unavailable'),[]]
        helper.guard_cycle()
        self.assertEqual(helper.read_lease()['deadline'],before['deadline'])
        self.assertIn(helper.PREFIX+'127.0.0.1 telegram.test',helper.HOSTS.read_text())
        self.force.assert_not_called()

    def request_video(self):
        output=io.StringIO()
        with patch.object(sys,'argv',['focus-helper','lease','youtube']),patch.object(sys,'stdin',io.StringIO(json.dumps({'video_id':OTHER}))),patch.object(helper.os,'geteuid',return_value=0),contextlib.redirect_stdout(output):
            helper.main()
        return json.loads(output.getvalue())

    def test_slow_preparation_cannot_make_its_own_timer_appear_dead(self):
        import os
        def slow_prepare(scope):
            old=time.time()-30
            os.utime(helper.HEARTBEAT,(old,old))
            return []
        with patch.object(guard,'prepare_session',side_effect=slow_prepare):
            grant=self.request_video()
        self.assertGreater(grant['deadline'],time.time())
        self.assertEqual(helper.read_lease()['service'],'youtube')
        self.assertEqual(guard.read_usage()['counts'][OTHER],1)

    def test_fresh_heartbeat_cannot_grant_access_without_actual_timer_process(self):
        helper.HEARTBEAT.touch()
        with patch.object(helper,'timer_running',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'не запущена'):self.request_video()
        self.assertIsNone(helper.read_lease())
        self.assertFalse(guard.USAGE.exists())

    def test_known_grant_refusal_does_not_consume_video_count_or_cooldown(self):
        original={'last_regular_opened_at':None,'counts':{ANOTHER:1}}
        guard.atomic_json(guard.USAGE,original)
        with patch.object(helper,'lease',side_effect=RuntimeError('grant refused')):
            with self.assertRaisesRegex(RuntimeError,'grant refused'):self.request_video()
        self.assertEqual(guard.read_usage(),original)
        self.assertIsNone(helper.read_lease())

    def test_uncertain_failure_after_lease_is_written_retains_root_reservation(self):
        actual=helper.lease
        def uncertain(*args,**kwargs):
            actual(*args,**kwargs)
            raise RuntimeError('unknown outcome')
        with patch.object(helper,'lease',side_effect=uncertain):
            with self.assertRaisesRegex(RuntimeError,'unknown outcome'):self.request_video()
        self.assertIsNotNone(helper.read_lease())
        self.assertEqual(guard.read_usage()['counts'][OTHER],1)

    def test_x_grants_two_minutes_then_refuses_a_second_daily_grant(self):
        helper.lease('x',120)
        self.assertEqual(helper.read_lease()['service'],'x')
        self.assertEqual(helper.read_lease()['deadline']-helper.read_lease()['started_at'],120)
        helper.restore()
        with self.assertRaisesRegex(RuntimeError,'X уже открывался'):helper.lease('x',120)
        self.assertIsNone(helper.read_lease())

    def test_x_expiry_closes_only_x_tab_and_preserves_lecture(self):
        helper.lease('x',120)
        lecture='https://www.youtube.com/watch?v='+VIDEO
        self.report.side_effect=[self.tabs(lecture,'https://x.com/home'),self.tabs(lecture)]
        value=helper.read_lease();value['deadline']=time.time()-1;guard.atomic_json(helper.STATE,value)
        helper.expire()
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]],['https://x.com/home'])
        self.assertIsNone(helper.read_lease())
        self.force.assert_not_called()

    def test_idle_cached_x_tab_is_closed_without_stopping_browser(self):
        self.report.side_effect=[self.tabs('https://twitter.com/user'),[]]
        helper.guard_cycle()
        self.assertEqual([t['url'] for t in self.close.call_args.args[0]],['https://twitter.com/user'])
        self.force.assert_not_called()

    def test_x_grant_preserves_always_available_lecture(self):
        helper.lease('x',120)
        self.report.return_value=self.tabs('https://x.com/home','https://www.youtube.com/watch?v='+VIDEO)
        helper.guard_cycle()
        self.assertEqual(helper.read_lease()['service'],'x')
        self.close.assert_not_called()
        self.force.assert_not_called()

    def test_playlist_refresh_applies_inside_existing_ordinary_youtube_lease(self):
        scope = {'mode':'video','allowed_ids':[OTHER],'study_ids':[VIDEO], 'playlist_ids':[COURSE['id']], 'seconds':720}
        helper.lease('youtube',720,scope)
        before = helper.read_lease()
        # The second playlist was not in the temporary grant's snapshot.
        seminar = POLICY['playlists'][1]['videos'][0]['id']
        self.report.return_value=self.tabs('https://www.youtube.com/watch?v='+seminar)
        helper.guard_cycle()
        self.close.assert_not_called()
        self.force.assert_not_called()
        self.assertEqual(helper.read_lease()['deadline'],before['deadline'])

    def test_channel_page_is_preserved_during_an_unrelated_temporary_session(self):
        helper.lease('x',120)
        self.report.return_value=self.tabs('https://x.com/home','https://www.youtube.com/@Moptori-FCS/videos')
        helper.guard_cycle()
        self.assertEqual(helper.read_lease()['service'],'x')
        self.close.assert_not_called()
        self.force.assert_not_called()

    def test_grant_from_another_process_uses_the_same_clock_as_daemon(self):
        import subprocess
        code = "import importlib.machinery,importlib.util,json,sys; from pathlib import Path; "
        code += "sys.path.insert(0,"+repr(str(STAGE))+"); "
        code += "loader=importlib.machinery.SourceFileLoader('child_helper',"+repr(str(STAGE/'focus-helper'))+"); "
        code += "spec=importlib.util.spec_from_loader(loader.name,loader); h=importlib.util.module_from_spec(spec); loader.exec_module(h); "
        code += "print(json.dumps({'clock':h.lease_clock(),'local':h.time.monotonic()}))"
        child=json.loads(subprocess.run(['/usr/bin/python3','-I','-c',code],capture_output=True,text=True,check=True).stdout)
        self.assertLess(abs(helper.lease_clock()-child['clock']),5)
        # The clock persisted by a fresh CLI is boot-relative, not a local
        # zero-based process clock. An older daemon must not expire it early.
        self.assertGreater(child['clock']-child['local'],100)
        helper.lease('telegram',420)
        with patch.object(helper.time,'monotonic',return_value=999999999):
            helper.expire()
        self.assertIsNotNone(helper.read_lease())

    def test_legacy_whole_site_youtube_unlock_is_rejected(self):
        with patch.object(sys,'argv',['focus-helper','lease','youtube']),patch.object(sys,'stdin',io.StringIO('300')),patch.object(helper.os,'geteuid',return_value=0):
            with self.assertRaises(ValueError):helper.main()
        self.assertIsNone(helper.read_lease())

    def test_legacy_course_command_returns_permanent_grant_without_occupying_lease(self):
        with patch.object(sys,'argv',['focus-helper','lease','youtube']),patch.object(sys,'stdin',io.StringIO(json.dumps({'playlist_id':COURSE['id']}))),patch.object(helper.os,'geteuid',return_value=0),contextlib.redirect_stdout(io.StringIO()) as out:
            helper.main()
        self.assertIsNone(json.loads(out.getvalue())['deadline'])
        self.assertIsNone(helper.read_lease())


class RaceAndMigrationTests(unittest.TestCase):
    def test_arc_index_race_is_retried_without_closing_a_lecture(self):
        report=[{'name':'Arc','kind':'id','tabs':[{'window':'w','tab':'t','url':'https://youtu.be/'+VIDEO}]}]
        with patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'_browser_report',side_effect=[RuntimeError('Неверный индекс. (-1719)'),report]) as observer,patch.object(guard.time,'sleep'),patch.object(guard,'close_tabs') as close:
            self.assertEqual(guard.browser_report(),report)
        self.assertEqual(observer.call_count,2)
        close.assert_not_called()

    def test_permission_failure_is_not_hidden_by_retries(self):
        with patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'_browser_report',side_effect=RuntimeError('Not authorized (-1743)')) as observer:
            with self.assertRaises(RuntimeError):guard.browser_report()
        self.assertEqual(observer.call_count,1)

    def test_repeated_race_fails_closed_after_three_attempts(self):
        with patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'_browser_report',side_effect=RuntimeError('(-1719)')) as observer,patch.object(guard.time,'sleep'):
            with self.assertRaises(RuntimeError):guard.browser_report()
        self.assertEqual(observer.call_count,3)

    def test_timer_liveness_is_checked_against_exact_launchd_service(self):
        import types
        good='state = running\n pid = 12345\n'
        with patch.object(helper.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout=good)) as command,patch.object(helper.os,'kill') as probe:
            self.assertTrue(helper.timer_running())
        self.assertEqual(command.call_args.args[0],['/bin/launchctl','print','system/local.focus-open.timer'])
        probe.assert_called_once_with(12345,0)
        for text in ('state = waiting\npid = 12345\n','state = running\n'):
            with patch.object(helper.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout=text)):
                self.assertFalse(helper.timer_running())

    def test_actual_subprocess_timeout_becomes_a_retryable_observer_error(self):
        import types
        with patch.object(guard,'username',return_value='fedornikonov'),patch.object(guard.pwd,'getpwnam',return_value=types.SimpleNamespace(pw_uid=501)),patch.object(guard.subprocess,'run',side_effect=guard.subprocess.TimeoutExpired(['osascript'],5)):
            with self.assertRaisesRegex(guard.BrowserScriptTimeout,'5 секунд'):
                guard.user_script('return "OK"')

    def test_each_browser_gets_its_own_script_and_healthy_arc_report_survives_chrome_timeout(self):
        arc=[{'name':'Arc','kind':'id','tabs':[{'window':'w','tab':'t','url':'https://web.telegram.org/k/'}]}]
        def snapshot(strict_unsupported=True,only_browser=None):
            if only_browser=='Google Chrome':raise guard.BrowserScriptTimeout('timeout')
            return arc
        with patch.object(guard,'browser_processes',return_value={'Arc','Google Chrome'}),patch.object(guard,'_browser_report',side_effect=snapshot) as observer,patch.object(guard.time,'sleep'):
            report=guard.browser_report(tolerate_errors=True)
        self.assertEqual(list(report),arc)
        self.assertEqual(set(report.errors),{'Google Chrome'})
        self.assertEqual(observer.call_count,3)
        self.assertEqual([c.kwargs['only_browser'] for c in observer.call_args_list],['Arc','Google Chrome','Google Chrome'])

    def test_single_browser_timeout_recovers_on_bounded_retry(self):
        with patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'_browser_report',side_effect=[guard.BrowserScriptTimeout('timeout'),[]]) as observer,patch.object(guard.time,'sleep'):
            report=guard.browser_report()
        self.assertEqual(report.errors,{})
        self.assertEqual(observer.call_count,2)

    def test_telegram_day_is_moscow_day_independent_of_caller_timezone(self):
        import datetime as dt
        now=dt.datetime(2026,10,12,22,0,tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(socials.day_at(now),'2026-10-13')
        usage={'schema_version':1,'services':{'telegram':{'last_day':'2026-10-13'}}}
        with self.assertRaises(RuntimeError):socials.validate('telegram',420,usage,now)

    def test_migration_preserves_most_recent_existing_social_history(self):
        state={'services':{'telegram':{'last_opened_at':'2026-10-12T14:25:00+03:00'}},'history':[{'service':'instagram','opened_at':'2026-10-12T14:40:00+03:00','access_granted':True}]}
        usage=socials.migrate_client_state(state)
        self.assertEqual(usage['services']['telegram']['last_day'],'2026-10-12')
        with self.assertRaises(RuntimeError):socials.validate('telegram',420,usage,usage['services']['telegram']['last_started_at']+600)
        with self.assertRaises(RuntimeError):socials.validate('instagram',120,usage,usage['services']['instagram']['last_started_at']+600)

    def test_notifier_api_is_an_explicit_exception(self):
        self.assertIsNone(socials.service_for_url('https://api.telegram.org/'))
        self.assertEqual(socials.service_for_url('https://t.me/channel'),'telegram')
        self.assertIsNone(socials.service_for_url('https://t.me.evil.test/'))


class ClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.base=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.multiple(client,STATE_DIR=self.base,STATE_PATH=self.base/'state.json',LOCK_PATH=self.base/'lock',YOUTUBE_POLICY_PATH=STAGE/'youtube-policy.json'))
        self.open=self.stack.enter_context(patch.object(client,'open_url'))
        self.stack.enter_context(patch.object(client,'prepare_youtube_browser'))
        self.stack.enter_context(patch.object(client,'restore_all_hosts_blocks'))

    def test_bare_youtube_prompts_for_link_without_course_choice(self):
        with patch.object(client,'run_osascript_dialog',side_effect=AssertionError('Unexpected course menu')),patch.object(client,'prompt_text',return_value='https://youtu.be/'+OTHER) as prompt:
            url,request=client.youtube_target(None,None,POLICY)
        self.assertEqual(request['video_id'],OTHER)
        prompt.assert_called_once()

    def test_course_opens_without_lease_or_cooldown_change(self):
        before={'services':{'youtube':{'last_opened_at':client.now_local().isoformat()}},'history':[]}
        client.save_state(before);previous=client.STATE_PATH.read_bytes()
        with patch.object(client,'helper_json',return_value={}) as rpc:
            client.run_youtube(course='dl')
        rpc.assert_called_once_with('guard-check')
        self.assertEqual(client.STATE_PATH.read_bytes(),previous)
        self.open.assert_called_once_with(client.youtube_course_entry(COURSE),'Arc')

    def test_whole_channel_shortcut_opens_without_lease_or_daily_limit_change(self):
        before={'services':{'youtube':{'last_opened_at':client.now_local().isoformat()}},'history':[]}
        client.save_state(before);previous=client.STATE_PATH.read_bytes()
        with patch.object(client,'helper_json',return_value={}) as rpc,contextlib.redirect_stdout(io.StringIO()):
            client.run_youtube(course='seminars')
        rpc.assert_called_once_with('guard-check')
        self.assertEqual(client.STATE_PATH.read_bytes(),previous)
        self.open.assert_called_once_with(client.youtube_channel_entry(POLICY['channels'][0]),'Arc')

    def test_video_outside_original_seminar_playlist_is_permanent(self):
        client.save_state({'services':{'youtube':{'last_opened_at':client.now_local().isoformat()}},'history':[]})
        previous=client.STATE_PATH.read_bytes()
        with patch.object(client,'helper_json',return_value={}) as rpc,contextlib.redirect_stdout(io.StringIO()):
            client.run_youtube(raw_url='https://www.youtube.com/watch?v=CibAgUC67x0')
        rpc.assert_called_once_with('guard-check')
        self.assertEqual(client.STATE_PATH.read_bytes(),previous)

    def test_channel_link_is_canonicalized_to_verified_channel_id(self):
        url,request=client.youtube_target('https://www.youtube.com/@Moptori-FCS',None,POLICY)
        self.assertEqual(request,{'channel_id':POLICY['channels'][0]['id']})
        self.assertEqual(url,client.youtube_channel_entry(POLICY['channels'][0]))

    def test_direct_lecture_opens_even_if_social_session_is_active(self):
        with patch.object(client,'helper_json',return_value={}) as rpc:
            client.run_youtube('https://youtu.be/'+VIDEO)
        rpc.assert_called_once_with('guard-check')
        self.open.assert_called_once()

    def test_ordinary_video_keeps_bound_root_grant_and_return_link(self):
        scope=guard.make_scope({'video_id':OTHER},POLICY,{},time.time())
        output=io.StringIO()
        with patch.object(client,'helper_json',side_effect=[{'lease':None},{},{'deadline':time.time()+720,'scope':scope}]) as rpc,patch.object(client,'get_youtube_duration_seconds',return_value=None),contextlib.redirect_stdout(output):
            client.run_youtube('https://youtu.be/'+OTHER)
        self.assertEqual(rpc.call_args.args,('lease','youtube',{'video_id':OTHER,'seconds':720}))
        self.assertIn(client.youtube_course_entry(COURSE),output.getvalue())
        self.assertIn('Автоблокировка:',output.getvalue())

    def test_x_cli_opens_home_without_prompt_and_records_two_minute_window(self):
        with patch.object(client,'helper_command') as command,patch.object(client,'helper_json',return_value={}),patch.object(client,'prompt_text',side_effect=AssertionError('unexpected link prompt')):
            self.assertEqual(client.main(['x']),0)
        self.assertEqual(command.call_args.args,('lease','x','120'))
        self.open.assert_called_once_with('https://x.com/','Arc')
        state=client.load_state()
        self.assertEqual(state['history'][-1]['service'],'x')
        self.assertEqual(state['history'][-1]['duration_seconds'],120)
        self.assertTrue(client.service_used_today(state,'x',client.now_local()))

    def test_client_blocks_same_day_x_and_instagram_even_after_old_two_hour_pause(self):
        import datetime as dt
        now=dt.datetime(2026,10,13,20,0,tzinfo=client.DAILY_TIMEZONE)
        old=dt.datetime(2026,10,13,8,0,tzinfo=client.DAILY_TIMEZONE)
        for key in ('x','instagram','youtube','telegram'):
            state={'services':{key:{'last_opened_at':old.isoformat(),'cooldown_seconds':0}}}
            with self.assertRaises(client.FocusOpenError):client.ensure_cooldown_allows(state,key,now)

    def test_telegram_setting_cannot_raise_protected_limit(self):
        with self.assertRaises(client.FocusOpenError):client.run_telegram(set_minutes=90)

    def test_browser_guard_cannot_be_disabled(self):
        with self.assertRaises(client.FocusOpenError):client.run_youtube('https://youtu.be/'+VIDEO,disable_browser_guard=True)


if __name__=='__main__':unittest.main()
