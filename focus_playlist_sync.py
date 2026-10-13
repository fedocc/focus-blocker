"""Refresh explicitly approved public playlists without pausing focus timers.

Only the root-owned policy supplies playlist IDs. Public YouTube HTTPS data
supplies membership; browser URL parameters and user-owned programs never do.
"""
import datetime as dt
import json
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import focus_youtube_guard as guard

STATUS = guard.BASE / 'playlist-sync-status.json'
INTERVAL = 60
MAX_RESPONSE = 8 * 1024 * 1024
MAX_PAGES = 100
MAX_VIDEOS = 10000
PLAYLIST_ID = re.compile(r'^[A-Za-z0-9_-]{2,100}$')
CLIENT_VERSION = re.compile(r'^2\.[0-9.]{4,40}$')
YOUTUBE = 'https://www.youtube.com'


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Unexpected playlist redirect')


def request_public(url, payload=None):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'www.youtube.com' or parsed.path not in ('/playlist', '/youtubei/v1/browse'):
        raise ValueError('Unexpected playlist metadata endpoint')
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9',
               'Accept-Encoding': 'identity'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    # No browser cookies, credential files, environment proxies or subprocesses.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    with opener.open(Request(url, data=data, headers=headers), timeout=5) as response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise RuntimeError('Playlist metadata exceeded the size limit')
        return raw.decode('utf-8')


def embedded_json(html, expression):
    for match in re.finditer(expression, html):
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.end():])
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def safe_title(value):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if c.isprintable())[:1000]


def duration(value):
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 86400:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,3}:\d{2}(?::\d{2})?', value):
        result = 0
        for part in value.split(':'):
            result = result * 60 + int(part)
        if 1 <= result <= 86400:
            return result
    # Upcoming/live playlist entries are still permitted without a time limit.
    return None


def text_value(value):
    if not isinstance(value, dict):
        return ''
    return value.get('simpleText') or ''.join(run.get('text', '') for run in value.get('runs', []) if isinstance(run, dict))


def video_entry(item, playlist_id):
    if 'lockupViewModel' in item:
        model = item['lockupViewModel']
        if model.get('contentType') != 'LOCKUP_CONTENT_TYPE_VIDEO':
            return None
        video_id = model.get('contentId')
        endpoint = model.get('rendererContext', {}).get('commandContext', {}).get('onTap', {}).get('innertubeCommand', {}).get('watchEndpoint', {})
        title = model.get('metadata', {}).get('lockupMetadataViewModel', {}).get('title', {}).get('content', '')
        length = None
        overlays = model.get('contentImage', {}).get('thumbnailViewModel', {}).get('overlays', [])
        for overlay in overlays:
            for badge in overlay.get('thumbnailBottomOverlayViewModel', {}).get('badges', []):
                length = duration(badge.get('thumbnailBadgeViewModel', {}).get('text')) or length
    elif 'playlistVideoRenderer' in item:
        model = item['playlistVideoRenderer']
        video_id = model.get('videoId')
        endpoint = model.get('navigationEndpoint', {}).get('watchEndpoint', {})
        title = text_value(model.get('title'))
        length = duration(model.get('lengthSeconds')) or duration(text_value(model.get('lengthText')))
    else:
        return None
    # Recommendations, even inside the response, cannot grow the allowlist.
    if not isinstance(video_id, str) or not guard.VIDEO_ID.fullmatch(video_id):
        return None
    if endpoint.get('videoId') != video_id or endpoint.get('playlistId') != playlist_id:
        return None
    return {'id': video_id, 'title': safe_title(title) or video_id, 'duration_seconds': length}


def parse_items(items, playlist_id):
    if not isinstance(items, list):
        raise RuntimeError('Invalid playlist items')
    videos = []
    tokens = set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError('Invalid playlist item')
        video = video_entry(item, playlist_id)
        if video:
            videos.append(video)
        for key in ('itemSectionRenderer', 'playlistVideoListRenderer', 'playlistVideoListContinuation', 'sectionListRenderer', 'sectionListContinuation'):
            if key in item:
                model = item[key]
                if model.get('playlistId', playlist_id) != playlist_id:
                    raise RuntimeError('Unexpected playlist identity')
                nested, token = parse_items(model.get('contents', []), playlist_id)
                videos.extend(nested)
                if token:
                    tokens.add(token)
        renderer = item.get('continuationItemRenderer', {})
        token = renderer.get('continuationEndpoint', {}).get('continuationCommand', {}).get('token')
        model = item.get('continuationItemViewModel', {})
        token = token or model.get('continuationCommand', {}).get('innertubeCommand', {}).get('continuationCommand', {}).get('token')
        if token is not None:
            if not isinstance(token, str) or not 1 <= len(token) <= 32768:
                raise RuntimeError('Invalid playlist continuation')
            tokens.add(token)
    if len(tokens) > 1:
        raise RuntimeError('Ambiguous playlist continuation')
    return videos, next(iter(tokens), None)


def channel_ids(value):
    if isinstance(value, dict):
        channel = value.get('browseEndpoint', {}).get('browseId')
        if isinstance(channel, str) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel):
            yield channel
        for nested in value.values():
            yield from channel_ids(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from channel_ids(nested)


def initial_page(html, playlist):
    data = next(embedded_json(html, r'(?:var\s+)?ytInitialData\s*=\s*'), None)
    if data is None:
        raise RuntimeError('YouTube playlist metadata unavailable')
    canonical = data.get('microformat', {}).get('microformatDataRenderer', {}).get('urlCanonical', '')
    parsed = urlparse(canonical)
    if parsed.hostname != 'www.youtube.com' or parsed.path != '/playlist' or parse_qs(parsed.query).get('list') != [playlist['id']]:
        raise RuntimeError('YouTube returned a different playlist')
    owner = playlist.get('owner_id')
    owners = set(channel_ids(data.get('header', {}))) | set(channel_ids(data.get('sidebar', {})))
    if owner and owner not in owners:
        raise RuntimeError('Approved playlist owner changed or is unavailable')
    tabs = data.get('contents', {}).get('twoColumnBrowseResultsRenderer', {}).get('tabs', [])
    selected = [tab.get('tabRenderer', {}) for tab in tabs if tab.get('tabRenderer', {}).get('selected')]
    if len(selected) != 1:
        raise RuntimeError('Playlist content unavailable')
    sections = selected[0].get('content', {}).get('sectionListRenderer', {}).get('contents')
    videos, token = parse_items(sections, playlist['id'])
    version = None
    for config in embedded_json(html, r'ytcfg\.set\s*\(\s*(?=\{)'):
        value = config.get('INNERTUBE_CLIENT_VERSION')
        if isinstance(value, str) and CLIENT_VERSION.fullmatch(value):
            version = value
            break
    return videos, token, version


def next_page(data, playlist_id):
    # The continuation is obtained from this playlist's selected contents.
    actions = data.get('onResponseReceivedActions', []) + data.get('onResponseReceivedEndpoints', [])
    item_lists = [action['appendContinuationItemsAction'].get('continuationItems', [])
                  for action in actions if 'appendContinuationItemsAction' in action]
    if len(item_lists) == 1:
        return parse_items(item_lists[0], playlist_id)
    continuation = data.get('continuationContents', {}).get('playlistVideoListContinuation')
    if not item_lists and isinstance(continuation, dict):
        return parse_items([{'playlistVideoListContinuation': continuation}], playlist_id)
    # YouTube appends an optional recommendations continuation after the final
    # playlist entry. Its empty response has this explicit service marker.
    services = data.get('responseContext', {}).get('serviceTrackingParams', [])
    if not item_lists and any(p.get('key') == 'GetRecommendedMusicPlaylists_rid'
                              for service in services for p in service.get('params', [])):
        return [], None
    raise RuntimeError('Unexpected playlist continuation response')


def fetch_playlist(playlist, request=request_public):
    playlist_id = playlist['id']
    if not isinstance(playlist_id, str) or not PLAYLIST_ID.fullmatch(playlist_id):
        raise ValueError('Invalid approved playlist ID')
    end = time.monotonic() + 30
    html = request(YOUTUBE + '/playlist?' + urlencode({'list': playlist_id, 'hl': 'en'}))
    videos, token, version = initial_page(html, playlist)
    if len(videos) > MAX_VIDEOS:
        raise RuntimeError('Approved playlist exceeded the size limit')
    seen = set()
    pages = 1
    while token:
        if token in seen or pages >= MAX_PAGES or time.monotonic() >= end:
            raise RuntimeError('Playlist continuation did not finish')
        if not version:
            raise RuntimeError('YouTube client version unavailable')
        seen.add(token)
        payload = {'context': {'client': {'clientName': 'WEB', 'clientVersion': version, 'hl': 'en', 'gl': 'US'}}, 'continuation': token}
        raw = request(YOUTUBE + '/youtubei/v1/browse?prettyPrint=false', payload)
        entries, token = next_page(json.loads(raw), playlist_id)
        videos.extend(entries)
        if len(videos) > MAX_VIDEOS:
            raise RuntimeError('Approved playlist exceeded the size limit')
        pages += 1
    result = list({video['id']: video for video in videos}.values())
    if not result:
        raise RuntimeError('Playlist has no verified public videos; keeping the previous list')
    return result


def refresh_playlist(playlist, lock, fetch=fetch_playlist):
    # Slow network operations occur outside the same lock used by the timers.
    videos = fetch(playlist)
    with lock():
        policy = guard.read_policy()
        matches = [p for p in policy['playlists'] if p['id'] == playlist['id'] and p.get('auto_refresh') is True and p.get('owner_id') == playlist.get('owner_id')]
        if len(matches) != 1:
            raise RuntimeError('Approved playlist policy changed during refresh')
        target = matches[0]
        target['videos'] = videos
        target['refreshed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        guard.atomic_json(guard.POLICY, policy, mode=0o644)
    return len(videos)


def worker_loop(lock, stop=None):
    stop = stop or threading.Event()
    status = {}
    # Let the browser observer enable the permanent lecture policy first.
    if stop.wait(2):
        return
    while not stop.is_set():
        try:
            playlists = [p for p in guard.read_policy()['playlists'] if p.get('auto_refresh') is True]
            for playlist in playlists:
                entry = {'checked_at': time.time()}
                try:
                    entry.update(ok=True, video_count=refresh_playlist(playlist, lock))
                except Exception as error:
                    entry.update(ok=False, error=str(error)[:300])
                status[playlist['id']] = entry
            guard.atomic_json(STATUS, {'playlists': status}, mode=0o644)
        except Exception as error:
            # Refresh failures never revoke a healthy lecture/social session.
            try:
                guard.atomic_json(STATUS, {'playlists': status, 'error': str(error)[:300]}, mode=0o644)
            except Exception:
                pass
        stop.wait(INTERVAL)


def start_worker(lock):
    worker = threading.Thread(target=worker_loop, args=(lock,), name='approved-playlist-refresh', daemon=True)
    worker.start()
    return worker
