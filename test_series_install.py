import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_series_schedule as installer

class ScheduleInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.patch=patch.object(installer,'BASE',self.base);self.patch.start();self.addCleanup(self.patch.stop)
        (self.base/'module.py').write_text('unchanged')
        (self.base/'study.json').write_text('{"schema":1,"session":null}')
        (self.base/'no-manual-overrides').write_text('keep')
        (self.base/'arc-preview-guard-required').touch()
        self.manifest={'old':{'module.py':hashlib.sha256(b'unchanged').hexdigest()}}
    def test_preflight_is_read_only(self):
        before={p.name:p.read_bytes() for p in self.base.iterdir()}
        installer.assert_current(self.manifest)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.base.iterdir()})
    def test_other_updates_are_preserved(self):
        (self.base/'module.py').write_text('another task')
        with self.assertRaisesRegex(RuntimeError,'изменилась'):installer.assert_current(self.manifest)
    def test_active_lease_and_study_are_not_interrupted(self):
        (self.base/'lease.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'не прерывает'):installer.assert_current(self.manifest)
        (self.base/'lease.json').unlink()
        (self.base/'study.json').write_text('{"schema":1,"session":{"phase":"focus"}}')
        with self.assertRaisesRegex(RuntimeError,'учебного'):installer.assert_current(self.manifest)
    def test_existing_manual_override_lock_is_required(self):
        (self.base/'no-manual-overrides').unlink()
        with self.assertRaisesRegex(RuntimeError,'ограничение'):installer.assert_current(self.manifest)

if __name__=='__main__':unittest.main()
