import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import focus_series_policy as series
from focus_series_schedule import window_at, TIMEZONE


def timestamp(day='2026-11-02', clock='20:30:00'):
    return dt.datetime.fromisoformat(day+'T'+clock).replace(tzinfo=TIMEZONE).timestamp()

class SeriesScheduleTests(unittest.TestCase):
    def test_all_weekdays_and_exact_open_close_boundaries(self):
        for day in range(7):
            date=(dt.date(2026,11,2)+dt.timedelta(days=day)).isoformat()
            for clock in ('20:30:00','21:15:00','21:59:59'):
                self.assertEqual(window_at(timestamp(date,clock))['open'],day in (0,2,4,6),(date,clock))
            for clock in ('00:00:00','20:29:59','22:00:00','23:59:59'):
                self.assertFalse(window_at(timestamp(date,clock))['open'],(date,clock))
    def test_next_window_and_moscow_time(self):
        self.assertEqual(window_at(timestamp(clock='22:00:00'))['start'],timestamp('2026-11-04'))
        self.assertEqual(window_at(timestamp('2026-11-08','22:00:00'))['start'],timestamp('2026-11-09'))
        self.assertTrue(window_at(dt.datetime(2026,11,2,17,30,tzinfo=dt.timezone.utc).timestamp())['open'])
        self.assertFalse(window_at(dt.datetime(2026,11,2,19,0,tzinfo=dt.timezone.utc).timestamp())['open'])
    def test_whole_site_is_allowed_without_episode_or_extension_heartbeat(self):
        for url in ('https://www.mirabreakingbad.com/','https://www.mirabreakingbad.com/episodes/br-5-season-16-seriya/',
                    'https://plplayer.online/s/455?season=3&episode=10&voice=1', 'https://kalarona.org/s/540'):
            self.assertTrue(series.allowed_url(url,None,timestamp()),url)
            self.assertFalse(series.allowed_url(url,None,timestamp(clock='22:00:00')),url)
        for url in ('https://youtube.com/','https://mirabreakingbad.com.evil.test/','https://example.com/?q=plplayer.online'):
            self.assertFalse(series.allowed_url(url,None,timestamp()),url)
    def test_status_and_start_never_change_legacy_episode_or_consume_a_slot(self):
        legacy={'schema':1,'index':17,'started_at':1793174867.54071,'finished':False}
        before=json.dumps(legacy)
        for _ in range(2):
            value,status=series.transition(legacy,{'op':'start'},timestamp())
            self.assertEqual(value,legacy)
            self.assertTrue(status['active'])
            self.assertEqual(status['window_end'],timestamp(clock='22:00:00'))
        self.assertEqual(json.dumps(legacy),before)
        self.assertFalse(series.public_status(None,timestamp(clock='22:00:00'))['active'])
    def test_study_blocks_the_site_even_during_an_allowed_evening(self):
        self.assertFalse(series.allowed_url(series.SITE_URL,None,timestamp(),studying=True))
        with self.assertRaisesRegex(RuntimeError,'учебный'):series.transition(None,{'op':'start'},timestamp(),studying=True)
    def test_no_extension_events_or_public_schedule_time_override(self):
        for request in ({'op':'reset'},{'op':'heartbeat'},{'op':'finish'}, {'op':'start','now':timestamp()},
                        {'op':'start','seconds':900},{'op':'status','weekdays':[1,3,5]}):
            with self.assertRaises(ValueError):series.transition(None,request,timestamp())
        with self.assertRaises(RuntimeError):series.transition(None,{'op':'start'},timestamp(clock='22:00:00'))
    def test_network_schedule_changes_only_its_own_block(self):
        original='127.0.0.1 localhost\n# === FOCUS_BLOCK:SERIES START ===\n127.0.0.1 mirabreakingbad.com\n::1 mirabreakingbad.com\n# === FOCUS_BLOCK:SERIES END ===\n127.0.0.1 unrelated.example\n'
        with tempfile.TemporaryDirectory() as folder,patch.object(series,'ENABLED',Path(folder)/'enabled'):
            series.ENABLED.touch()
            opened=series.hosts_text(original,timestamp())
            self.assertIn('# TEMP_FOCUS_UNLOCK 127.0.0.1 mirabreakingbad.com',opened)
            self.assertTrue(opened.startswith('127.0.0.1 localhost\n'))
            self.assertTrue(opened.endswith('127.0.0.1 unrelated.example\n'))
            self.assertEqual(series.hosts_text(opened,timestamp(clock='22:00:00')),original)
            self.assertEqual(series.hosts_text(opened,timestamp(),studying=True),original)
            with self.assertRaises(RuntimeError):series.hosts_text('missing',timestamp())
    def test_closed_window_targets_only_series_sites(self):
        report=[{'name':'Arc','kind':'id','tabs':[
            {'window':'w','tab':'lecture','url':'https://youtube.com/watch?v=lecture'},
            {'window':'w','tab':'series','url':series.SITE_URL}]}]
        with tempfile.TemporaryDirectory() as folder,patch.object(series,'ENABLED',Path(folder)/'enabled'):
            series.ENABLED.touch()
            with patch('time.time',return_value=timestamp(clock='22:00:00')):
                self.assertEqual([t['tab'] for t in series.denied_targets(report)],['series'])
            with patch('time.time',return_value=timestamp()):
                self.assertEqual(series.denied_targets(report),[])
                self.assertEqual([t['tab'] for t in series.denied_targets(report,studying=True)],['series'])
    def test_native_observer_includes_whole_site_addresses_without_extension(self):
        import focus_youtube_guard as guard
        sep=chr(31)
        output='T'+sep+'Arc'+sep+'window'+sep+'tab'+sep+series.SITE_URL+'\nB'+sep+'Arc'+sep+'1\n'
        with patch.object(guard,'guard_arc_previews'),patch.object(guard,'browser_processes',return_value={'Arc'}),patch.object(guard,'user_script',return_value=output) as native:
            report=guard.browser_report()
        self.assertEqual(guard.targets_for(report,series.is_surface)[0]['url'],series.SITE_URL)
        for domain in ('mirabreakingbad.com','plplayer.online','kalarona.org'):
            self.assertIn('tabUrl contains "'+domain+'"',native.call_args.args[0])

    def test_missing_episode_history_cannot_block_the_new_site_schedule(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(series,'STATE',Path(folder)/'missing'):
            self.assertTrue(series.public_status(None,timestamp())['active'])
            self.assertFalse(series.STATE.exists())
    def test_preview_permit_is_short_lived_and_stops_at_the_window_end(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(series,'BASE',Path(folder)):
            now=timestamp(clock='21:59:58')
            series.publish_preview_permission(now)
            self.assertEqual(json.loads((Path(folder)/'series-preview-permit.json').read_text())['allowed_until'],timestamp(clock='22:00:00'))
            series.publish_preview_permission(timestamp(),studying=True)
            self.assertEqual(json.loads((Path(folder)/'series-preview-permit.json').read_text())['allowed_until'],0)

if __name__=='__main__':unittest.main()
