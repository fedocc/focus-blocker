import contextlib
import io
import subprocess
import unittest
from unittest.mock import Mock, patch
import focus_open as f

URL='https://youtu.be/ApJpaO7XUR8'

class MetadataTests(unittest.TestCase):
    def test_markdown_and_autolinks_normalize(self):
        for wrapped in (f'[{URL}]({URL})', f'[video]({URL})', f'<{URL}>'):
            self.assertEqual(f.validate_url('youtube',wrapped).normalized_url,URL)
            self.assertEqual(f.extract_youtube_video_id(wrapped),'ApJpaO7XUR8')
        self.assertFalse(f.validate_url('youtube','[YouTube](https://example.com/)').allowed)

    def test_success_uses_bounded_metadata_only_command(self):
        with patch.object(f.shutil,'which',return_value='/test/yt-dlp'), patch.object(f.subprocess,'run',return_value=Mock(returncode=0,stdout='120.5\n')) as run:
            self.assertEqual(f.get_youtube_duration_seconds(f'[video]({URL})'),120)
        args=run.call_args.args[0]
        self.assertIn('--no-playlist',args)
        self.assertIn('--skip-download',args)
        self.assertIn('--ignore-no-formats-error',args)
        self.assertEqual(args[args.index('--extractor-retries')+1],'0')
        self.assertEqual(args[-1],URL)
        self.assertEqual(run.call_args.kwargs['timeout'],12)

    def test_timeout_has_quiet_twelve_minute_fallback(self):
        out,err=io.StringIO(),io.StringIO()
        with patch.object(f.shutil,'which',return_value='yt-dlp'), patch.object(f.subprocess,'run',side_effect=subprocess.TimeoutExpired('yt-dlp',12)), contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            duration=f.get_youtube_duration_seconds(URL)
            access=f.compute_access_seconds('youtube',URL,f.validate_url('youtube',URL),duration)
        self.assertIsNone(duration)
        self.assertEqual(access,720)
        self.assertEqual(out.getvalue()+err.getvalue(),'')

    def test_missing_binary_and_extractor_error_are_quiet(self):
        err=io.StringIO()
        with contextlib.redirect_stderr(err),patch.object(f.shutil,'which',return_value=None):
            self.assertIsNone(f.get_youtube_duration_seconds(URL))
        with contextlib.redirect_stderr(err),patch.object(f.shutil,'which',return_value='yt-dlp'),patch.object(f.subprocess,'run',return_value=Mock(returncode=1,stderr='technical details')):
            self.assertIsNone(f.get_youtube_duration_seconds(URL))
        self.assertEqual(err.getvalue(),'')

    def test_unavailable_or_malformed_duration_uses_fallback(self):
        with patch.object(f.shutil,'which',return_value='yt-dlp'):
            for value in ('NA','', 'ERROR 403', '120\n300', 'inf'):
                with patch.object(f.subprocess,'run',return_value=Mock(returncode=0,stdout=value)):
                    self.assertIsNone(f.get_youtube_duration_seconds(URL))

    def test_timeout_always_restores_metadata_unlock(self):
        with patch.object(f,'helper_command'),patch.object(f,'get_youtube_duration_seconds',return_value=None),patch.object(f,'restore_all_hosts_blocks') as restore:
            self.assertIsNone(f.get_youtube_duration_with_temporary_hosts_unlock(URL))
        restore.assert_called_once()

if __name__=='__main__':
    unittest.main()
