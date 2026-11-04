import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import install_approved_fixes as update

class ApprovedUpdateTests(unittest.TestCase):
    def test_requires_full_study_completion_and_never_stops_a_session(self):
        with tempfile.TemporaryDirectory() as d,patch.object(update,'BASE',Path(d)):
            root=Path(d);(root/'no-manual-overrides').write_text('protected')
            for session in ({'phase':'focus'},{'phase':'break'},None):
                (root/'study.json').write_text(json.dumps({'schema':1,'session':session}))
                before=(root/'study.json').read_bytes()
                if session is None:update.idle()
                else:
                    with self.assertRaisesRegex(RuntimeError,'учебной'):update.idle()
                self.assertEqual((root/'study.json').read_bytes(),before)
            (root/'lease.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'временного'):update.idle()
    def test_tampered_incoming_code_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as d,patch.object(update,'SOURCE',Path(d)):
            root=Path(d);manifest={'new':{update.CODE[0]:hashlib.sha256(b'approved').hexdigest()}}
            (root/update.CODE[0]).write_text('unapproved = True')
            with self.assertRaisesRegex(RuntimeError,'подготовленный'):update.validate(manifest,{})
    def test_update_scope_excludes_native_apps_study_module_and_sudoers(self):
        self.assertEqual(set(update.CODE),{'focus-helper','focus_youtube_guard.py','focus_open.py'})
        for name in ('study.json','youtube-usage.json','service-usage.json','no-manual-overrides'):
            self.assertIn(name,update.DATA)

if __name__=='__main__':unittest.main()
