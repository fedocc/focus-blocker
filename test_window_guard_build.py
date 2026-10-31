import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import build_window_guard as builder

class WindowGuardBuildTests(unittest.TestCase):
    def test_explicit_minimum_os_ignores_future_toolchain_default(self):
        for architecture in ('arm64','x86_64'):
            with tempfile.TemporaryDirectory() as folder,patch.object(builder.platform,'machine',return_value=architecture),patch.object(builder.subprocess,'run') as run,patch.dict(os.environ,{'MACOSX_DEPLOYMENT_TARGET':'99.0'}):
                app=builder.build(Path(folder)/'Guard.app',series=True)
                compile_command=run.call_args_list[0].args[0]
                self.assertEqual(compile_command[compile_command.index('-target')+1],architecture+'-apple-macosx13.0')
                self.assertIn('SERIES_SCHEDULE',compile_command)
                info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
                self.assertEqual(info['LSMinimumSystemVersion'],'13.0')
                self.assertEqual(info['CFBundleIdentifier'],'local.focus-open.series-guard')
    def test_existing_app_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(builder.subprocess,'run') as run:
            app=Path(folder)/'Guard.app';app.mkdir()
            with self.assertRaises(SystemExit):builder.build(app)
            run.assert_not_called()

if __name__=='__main__':unittest.main()
