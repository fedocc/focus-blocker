import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import focus_playlist_sync as sync
import focus_youtube_guard as guard

PLAYLIST = {'id': 'PLYftJUd2A-Uw', 'owner_id': 'UCGY5mIxE-8T8BZFQVH3386Q', 'auto_refresh': True,
            'videos': [{'id': 'SMPIJhzA_XI', 'title': 'Seminar 1', 'duration_seconds': 4410}]}
FUTURE = 'FUTUREabc12'
OTHER = 'FORBIDDEN12'


def item(video, playlist=PLAYLIST['id'], duration='1:13:30'):
    return {'lockupViewModel': {'contentId': video, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO',
                               'rendererContext': {'commandContext': {'onTap': {'innertubeCommand': {'watchEndpoint': {'videoId': video, 'playlistId': playlist}}}}},
                               'metadata': {'lockupMetadataViewModel': {'title': {'content': 'Seminar'}}},
                               'contentImage': {'thumbnailViewModel': {'overlays': [{'thumbnailBottomOverlayViewModel': {'badges': [{'thumbnailBadgeViewModel': {'text': duration}}]}}]}}}}


def continuation(token):
    return {'continuationItemViewModel': {'continuationCommand': {'innertubeCommand': {'continuationCommand': {'token': token}}}}}


def page(items, owner=PLAYLIST['owner_id'], playlist_id=PLAYLIST['id']):
    data = {'microformat': {'microformatDataRenderer': {'urlCanonical': 'http://www.youtube.com/playlist?list=' + playlist_id}},
            'header': {'owner': {'browseEndpoint': {'browseId': owner}}},
            'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {'sectionListRenderer': {'contents': [{'itemSectionRenderer': {'contents': items}}]}}}}]}}}
    return 'var ytInitialData = ' + json.dumps(data) + ';ytcfg.set(' + json.dumps({'INNERTUBE_CLIENT_VERSION': '2.20261012.01.00'}) + ');'


class PlaylistParsingTests(unittest.TestCase):
    def test_future_video_is_imported_from_second_page_only_after_verified_membership(self):
        calls = []
        def request(url, payload=None):
            calls.append((url, payload))
            if payload is None:
                return page([item('SMPIJhzA_XI'), continuation('page2')])
            return json.dumps({'onResponseReceivedActions': [{'appendContinuationItemsAction': {'continuationItems': [item(FUTURE), item(OTHER, 'OtherPlaylist')]}}]})
        result = sync.fetch_playlist(PLAYLIST, request)
        self.assertEqual({v['id'] for v in result}, {'SMPIJhzA_XI', FUTURE})
        self.assertEqual(calls[1][1]['continuation'], 'page2')
        self.assertEqual(calls[1][0], 'https://www.youtube.com/youtubei/v1/browse?prettyPrint=false')
        scope = guard.study_scope({'playlists': [dict(PLAYLIST, videos=result)]})
        self.assertTrue(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + FUTURE))
        self.assertFalse(guard.url_allowed(scope, 'https://www.youtube.com/watch?v=' + OTHER + '&list=' + PLAYLIST['id']))

    def test_content_id_and_watch_endpoint_must_match(self):
        video = item(FUTURE)
        video['lockupViewModel']['rendererContext']['commandContext']['onTap']['innertubeCommand']['watchEndpoint']['videoId'] = OTHER
        self.assertIsNone(sync.video_entry(video, PLAYLIST['id']))

    def test_wrong_canonical_playlist_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'different playlist'):
            sync.fetch_playlist(PLAYLIST, lambda *args: page([item(FUTURE)], playlist_id='WrongPlaylist'))

    def test_owner_change_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'owner changed'):
            sync.fetch_playlist(PLAYLIST, lambda *args: page([item(FUTURE)], owner='UC' + 'x' * 22))

    def test_repeating_continuation_is_rejected_as_incomplete(self):
        def request(url, payload=None):
            if payload is None:
                return page([item(FUTURE), continuation('repeat')])
            return json.dumps({'onResponseReceivedActions': [{'appendContinuationItemsAction': {'continuationItems': [continuation('repeat')]}}]})
        with self.assertRaisesRegex(RuntimeError, 'did not finish'):
            sync.fetch_playlist(PLAYLIST, request)

    def test_empty_or_recommendation_only_result_keeps_previous_cache(self):
        with self.assertRaisesRegex(RuntimeError, 'no verified public videos'):
            sync.fetch_playlist(PLAYLIST, lambda *args: page([item(OTHER, 'WrongPlaylist')]))

    def test_old_playlist_renderer_is_supported(self):
        video = {'playlistVideoRenderer': {'videoId': FUTURE, 'title': {'runs': [{'text': 'Seminar'}]},
                                         'lengthText': {'simpleText': '39:35'},
                                         'navigationEndpoint': {'watchEndpoint': {'videoId': FUTURE, 'playlistId': PLAYLIST['id']}}}}
        self.assertEqual(sync.fetch_playlist(PLAYLIST, lambda *args: page([video]))[0]['duration_seconds'], 2375)

    def test_empty_recommendations_tail_does_not_discard_complete_playlist(self):
        def request(url, payload=None):
            if payload is None:
                return page([item(FUTURE), continuation('recommendations')])
            return json.dumps({'responseContext': {'serviceTrackingParams': [{'params': [{'key': 'GetRecommendedMusicPlaylists_rid', 'value': 'public'}]}]}})
        self.assertEqual(sync.fetch_playlist(PLAYLIST, request)[0]['id'], FUTURE)

    def test_unexplained_empty_continuation_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected playlist continuation'):
            sync.next_page({'responseContext': {}}, PLAYLIST['id'])

    def test_live_or_upcoming_video_does_not_need_a_duration(self):
        result = sync.fetch_playlist(PLAYLIST, lambda *args: page([item(FUTURE, duration='LIVE')]))
        self.assertIsNone(result[0]['duration_seconds'])

    def test_external_http_and_non_metadata_endpoints_are_rejected(self):
        for url in ('http://www.youtube.com/playlist', 'https://www.youtube.com.evil/playlist', 'https://www.youtube.com/watch'):
            with self.assertRaises(ValueError):
                sync.request_public(url)


class PlaylistCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.policy = self.base / 'policy.json'
        self.original = {'schema_version': 1, 'playlists': [copy.deepcopy(PLAYLIST)]}
        self.policy.write_text(json.dumps(self.original))
        self.patch = patch.object(guard, 'POLICY', self.policy)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.locked = False

    @contextlib.contextmanager
    def lock(self):
        self.assertFalse(self.locked)
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def test_network_fetch_does_not_hold_the_timer_lock_and_does_not_touch_usage(self):
        usage = self.base / 'usage.json'
        usage.write_text('protected existing usage')
        def fetch(playlist):
            self.assertFalse(self.locked)
            return [{'id': FUTURE, 'title': 'New lecture', 'duration_seconds': None}]
        with patch.object(guard, 'USAGE', usage):
            self.assertEqual(sync.refresh_playlist(PLAYLIST, self.lock, fetch), 1)
        self.assertEqual(usage.read_text(), 'protected existing usage')
        updated = guard.read_policy()
        self.assertEqual(updated['playlists'][0]['videos'][0]['id'], FUTURE)
        self.assertIsNone(guard.study_scope(updated)['seconds'])

    def test_network_failure_preserves_exact_previous_policy(self):
        before = self.policy.read_bytes()
        with self.assertRaises(TimeoutError):
            sync.refresh_playlist(PLAYLIST, self.lock, lambda p: (_ for _ in ()).throw(TimeoutError('offline')))
        self.assertEqual(self.policy.read_bytes(), before)

    def test_disabled_or_removed_playlist_cannot_be_restored_by_in_flight_fetch(self):
        before = self.policy.read_bytes()
        def fetch(playlist):
            value = copy.deepcopy(self.original)
            value['playlists'][0]['auto_refresh'] = False
            self.policy.write_text(json.dumps(value))
            return [{'id': FUTURE, 'title': 'Future', 'duration_seconds': None}]
        with self.assertRaisesRegex(RuntimeError, 'policy changed'):
            sync.refresh_playlist(PLAYLIST, self.lock, fetch)
        self.assertNotEqual(self.policy.read_bytes(), before)
        self.assertEqual(guard.read_policy()['playlists'][0]['videos'], PLAYLIST['videos'])

    def test_future_channel_upload_is_permanent_after_background_refresh(self):
        channel_id=PLAYLIST['owner_id'];uploads='UU'+channel_id[2:]
        channel={'id':channel_id,'title':'Seminars','handle':'Moptori-FCS','uploads_playlist_id':uploads}
        playlist=dict(copy.deepcopy(PLAYLIST),id=uploads)
        policy={'schema_version':1,'playlists':[playlist],'channels':[channel]}
        self.policy.write_text(json.dumps(policy))
        sync.refresh_playlist(playlist,self.lock,lambda p:[{'id':FUTURE,'title':'Future seminar','duration_seconds':None}])
        updated=guard.read_policy()
        scope=guard.make_scope({'video_id':FUTURE},updated,{'last_regular_opened_at':1000},1001)
        self.assertIsNone(scope['seconds'])
        self.assertIn(FUTURE,scope['channel_video_ids'])
        self.assertTrue(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+FUTURE))
        self.assertFalse(guard.url_allowed(scope,'https://www.youtube.com/watch?v='+OTHER+'&channel_id='+channel_id))

    def test_channel_cannot_use_an_editable_or_foreign_uploads_playlist(self):
        channel_id=PLAYLIST['owner_id']
        value=copy.deepcopy(self.original)
        value['channels']=[{'id':channel_id,'handle':'Moptori-FCS','uploads_playlist_id':PLAYLIST['id']}]
        self.policy.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,'verified uploads playlist'):guard.read_policy()


if __name__ == '__main__':
    unittest.main()
