"""Only a protected, exact one-use owner permit accepts the Flare exception."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_youtube_guard as fixtures

helper = fixtures.helper

class OwnerDiagnosticPermitTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base=Path(self.directory.name)
        self.scope=patch.object(helper,'BASE',self.base);self.scope.start();self.addCleanup(self.scope.stop)
        (self.base/'no-manual-overrides').touch()
        self.current={'service':'tiktok','origin':'owner_diagnostic','diagnostic_request':helper.OWNER_DIAGNOSTIC_REQUEST,
                      'started_at':1000.,'started_monotonic':2000.,'deadline':1900.,'deadline_monotonic':2900.,
                      'timer_clock':'system_monotonic_v1','observed_browsers':[]}
        self.path=self.base/(helper.OWNER_DIAGNOSTIC_REQUEST+'.json')
        self.path.write_text(json.dumps({'request':helper.OWNER_DIAGNOSTIC_REQUEST,
                                        'lease':{k:v for k,v in self.current.items() if k!='observed_browsers'}}))
        self.path.chmod(0o600)
        self.real_lstat=Path.lstat
    def secure(self,owner=0,mode=None):
        original=self.real_lstat
        def metadata(path):
            info=original(path)
            if path==self.path:
                fields=list(info);fields[4]=owner
                if mode is not None:fields[0]=mode
                return os.stat_result(fields)
            return info
        return patch.object(Path,'lstat',metadata)
    def test_exact_root_owned_live_permit_is_accepted(self):
        with self.secure(),patch.object(helper.time,'time',return_value=1100),patch.object(helper,'lease_clock',return_value=2100):
            self.assertFalse(helper.manual_override_forbidden(self.current))
            self.assertFalse(helper.manual_override_forbidden(dict(self.current,observed_browsers=['Arc'])))
    def test_changed_scope_or_expiry_is_rejected(self):
        with self.secure(),patch.object(helper.time,'time',return_value=1100),patch.object(helper,'lease_clock',return_value=2100):
            for changes in ({'service':'x'},{'deadline':1901.},{'additional_services':['instagram']},{'diagnostic_request':'other'}):
                self.assertTrue(helper.manual_override_forbidden(dict(self.current,**changes)))
        with self.secure(),patch.object(helper.time,'time',return_value=1901),patch.object(helper,'lease_clock',return_value=2100):
            self.assertTrue(helper.manual_override_forbidden(self.current))
    def test_untrusted_missing_or_symlink_permit_is_rejected(self):
        with self.secure(owner=501):self.assertTrue(helper.manual_override_forbidden(self.current))
        with self.secure(mode=0o100666):self.assertTrue(helper.manual_override_forbidden(self.current))
        self.path.unlink()
        self.assertTrue(helper.manual_override_forbidden(self.current))
        self.path.symlink_to(self.base/'no-manual-overrides')
        self.assertTrue(helper.manual_override_forbidden(self.current))
    def test_regular_cli_still_cannot_request_fifteen_minutes(self):
        with patch.object(helper,'require_no_focus'):
            with self.assertRaisesRegex(RuntimeError,'Ручные исключения запрещены'):helper.lease('tiktok',900)

if __name__=='__main__':unittest.main()
