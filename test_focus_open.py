#!/usr/bin/env python3
import datetime as dt
import unittest

import focus_open


class FocusOpenPureFunctionTests(unittest.TestCase):
    def test_youtube_allows_watch_and_shortlink(self):
        self.assertTrue(focus_open.validate_url("youtube", "https://www.youtube.com/watch?v=abc123").allowed)
        self.assertTrue(focus_open.validate_url("youtube", "https://youtu.be/abc123").allowed)

    def test_youtube_rejects_impulsive_surfaces(self):
        for url in [
            "https://youtube.com/",
            "https://www.youtube.com/feed/subscriptions",
            "https://www.youtube.com/results?search_query=test",
            "https://www.youtube.com/shorts/abc123",
            "https://www.youtube.com/@channel",
        ]:
            self.assertFalse(focus_open.validate_url("youtube", url).allowed, url)

    def test_instagram_allows_posts_and_reels_only(self):
        self.assertTrue(focus_open.validate_url("instagram", "https://www.instagram.com/p/ABC123/").allowed)
        self.assertTrue(focus_open.validate_url("instagram", "https://instagram.com/reel/ABC123/").allowed)
        self.assertTrue(focus_open.validate_url("instagram", "https://www.instagram.com/reels/ABC123/").allowed)
        self.assertFalse(focus_open.validate_url("instagram", "https://instagram.com/").allowed)
        self.assertFalse(focus_open.validate_url("instagram", "https://instagram.com/explore/").allowed)
        self.assertFalse(focus_open.validate_url("instagram", "https://instagram.com/stories/name/1/").allowed)

    def test_reason_requires_five_words_and_twelve_chars(self):
        self.assertFalse(focus_open.reason_is_valid("надо видео"))
        self.assertFalse(focus_open.reason_is_valid("a b c d e"))
        self.assertTrue(focus_open.reason_is_valid("нужно проверить конкретный пример для задачи"))

    def test_youtube_video_id_matching_for_browser_monitor(self):
        expected = "https://youtu.be/jIiTbllBF4Q"
        same = "https://www.youtube.com/watch?v=jIiTbllBF4Q&feature=share"
        other_video = "https://www.youtube.com/watch?v=other"
        home = "https://www.youtube.com/"
        self.assertTrue(focus_open.browser_url_allowed("youtube", expected, same))
        self.assertFalse(focus_open.browser_url_allowed("youtube", expected, other_video))
        self.assertFalse(focus_open.browser_url_allowed("youtube", expected, home))

    def test_instagram_shortcode_matching_for_browser_monitor(self):
        expected = "https://www.instagram.com/reel/ABC123/"
        same = "https://instagram.com/reel/ABC123/?utm_source=ig_web_copy_link"
        plural_same = "https://www.instagram.com/reels/ABC123/"
        other_reel = "https://www.instagram.com/reel/OTHER/"
        home = "https://www.instagram.com/"
        reels_surface = "https://www.instagram.com/reels/"
        self.assertTrue(focus_open.browser_url_allowed("instagram", expected, same))
        self.assertTrue(focus_open.browser_url_allowed("instagram", expected, plural_same))
        self.assertFalse(focus_open.browser_url_allowed("instagram", expected, other_reel))
        self.assertFalse(focus_open.browser_url_allowed("instagram", expected, home))
        self.assertFalse(focus_open.browser_url_allowed("instagram", expected, reels_surface))

    def test_transient_browser_urls_are_limited_to_internal_loading_pages(self):
        self.assertTrue(focus_open.is_transient_browser_url("about:blank"))
        self.assertTrue(focus_open.is_transient_browser_url("arc://newtab/"))
        self.assertFalse(focus_open.is_transient_browser_url("https://www.youtube.com/"))

    def test_monitor_guard_decision_allows_initial_transient_only_during_grace(self):
        expected = "https://www.instagram.com/reel/ABC123/"
        self.assertEqual(
            focus_open.browser_monitor_decision("instagram", expected, "arc://newtab/", within_grace=True),
            "wait",
        )
        self.assertEqual(
            focus_open.browser_monitor_decision("instagram", expected, "arc://newtab/", within_grace=False),
            "block",
        )
        self.assertEqual(
            focus_open.browser_monitor_decision("instagram", expected, "https://www.instagram.com/reels/", within_grace=True),
            "block",
        )
        self.assertEqual(
            focus_open.browser_monitor_decision("instagram", expected, "https://www.instagram.com/reel/ABC123/", within_grace=False),
            "allow",
        )

    def test_cooldown_remaining(self):
        now = dt.datetime(2026, 6, 16, 18, 30, tzinfo=dt.timezone.utc)
        last = now - dt.timedelta(seconds=7100)
        self.assertEqual(focus_open.cooldown_remaining(last, now, 7200), 100)
        self.assertEqual(focus_open.cooldown_remaining(last, now, 7000), 0)

    def test_youtube_buffer_is_two_minutes(self):
        self.assertEqual(focus_open.YOUTUBE_BUFFER_SECONDS, 2 * 60)

    def test_browser_guard_defaults_to_arc_without_url_monitor(self):
        self.assertEqual(focus_open.DEFAULT_BROWSER_APP, "Arc")
        self.assertTrue(focus_open.should_use_browser_guard("youtube", disabled=False))
        self.assertTrue(focus_open.should_use_browser_guard("instagram", disabled=False))
        self.assertFalse(focus_open.should_use_browser_guard("youtube", disabled=True))
        self.assertFalse(focus_open.should_use_browser_guard("instagram", disabled=True))
        self.assertFalse(focus_open.DEFAULT_URL_MONITOR_ENABLED)

    def test_browser_reopen_policy(self):
        self.assertTrue(focus_open.should_reopen_browser_after_guard(browser_guard=True, was_running=True, disabled=False))
        self.assertFalse(focus_open.should_reopen_browser_after_guard(browser_guard=True, was_running=False, disabled=False))
        self.assertFalse(focus_open.should_reopen_browser_after_guard(browser_guard=False, was_running=True, disabled=False))
        self.assertFalse(focus_open.should_reopen_browser_after_guard(browser_guard=True, was_running=True, disabled=True))

    def test_hosts_unlock_and_restore_are_scoped_and_idempotent(self):
        hosts = """127.0.0.1 unrelated.test
# === FOCUS_BLOCK:YOUTUBE START ===
127.0.0.1 youtube.com
::1 www.youtube.com
127.0.0.1 www.youtube.com
# comment remains
# === FOCUS_BLOCK:YOUTUBE END ===
127.0.0.1 instagram.com
"""
        unlocked = focus_open.transform_hosts_block(hosts, "YOUTUBE", unlock=True)
        self.assertIn("# TEMP_FOCUS_UNLOCK 127.0.0.1 youtube.com", unlocked)
        self.assertIn("# TEMP_FOCUS_UNLOCK ::1 www.youtube.com", unlocked)
        self.assertIn("127.0.0.1 unrelated.test", unlocked)
        self.assertIn("127.0.0.1 instagram.com", unlocked)
        self.assertEqual(unlocked, focus_open.transform_hosts_block(unlocked, "YOUTUBE", unlock=True))

        restored = focus_open.transform_hosts_block(unlocked, "YOUTUBE", unlock=False)
        self.assertIn("127.0.0.1 youtube.com", restored)
        self.assertNotIn("TEMP_FOCUS_UNLOCK", restored)
        self.assertEqual(hosts, restored)

    def test_hosts_missing_block_fails_without_partial_text(self):
        with self.assertRaises(focus_open.HostsBlockError):
            focus_open.transform_hosts_block("127.0.0.1 youtube.com\n", "YOUTUBE", unlock=True)


if __name__ == "__main__":
    unittest.main()
