"""Exercise the actual Qt checklist in an isolated, offscreen process."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


class StudyUiTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('aqt'), 'Anki Qt is unavailable in this Python')
    def test_multiselect_survives_plan_refresh_and_locks_with_session(self):
        program = r'''
import tempfile
from pathlib import Path
from aqt.qt import QApplication, QMainWindow
from anki_study import dashboard as ui
app = QApplication([])
window = QMainWindow()
window.col = None
ui.mw = window
ui.bridge.applications = lambda: []
ui.StudyDock.run = lambda *args: None
with tempfile.TemporaryDirectory() as folder:
    ui.PREFS = Path(folder) / 'preferences.json'
    dock = ui.StudyDock()
    window._slovo_study_dock = dock
    dock.state = {'session':None, 'service_running':True, 'supports_task_groups':True}
    tasks = [{'title':'Лекция DL','occurrence_id':'dl','url':ui.bridge.ORIGIN+'/?id=dl'},
             {'title':'Практика','occurrence_id':'algo','url':ui.bridge.ORIGIN+'/?id=algo'}]
    dock.apply_plan({'date':'2026-10-19','tasks':tasks})
    dock.toggle_task(0)
    dock.toggle_task(1)
    assert dock.selected_ids == {'dl','algo'}
    assert dock.title.isHidden(), 'custom input must not duplicate the checklist'
    assert dock.start.isEnabled()
    dock.apply_plan({'date':'2026-10-19','tasks':list(reversed(tasks))})
    assert dock.selected_ids == {'dl','algo'}
    dock.toggle_task(0)
    assert dock.selected_ids == {'dl'}
    dock.toggle_task(1)
    assert dock.selected_ids == set()
    assert not dock.title.isHidden()
    dock.state['session'] = {'phase':'focus','task':{'title':'Две задачи','items':tasks}}
    dock.render_tasks(tasks, locked=True)
    dock.toggle_task(0)
    assert not dock.task_list.isEnabled()
    assert dock.selected_ids == set(), 'active block selection must not be mutable'
    assert dock.task_list.count() == 2
    dock.poller.stop()
    dock.display_timer.stop()
    dock.close()
window.close()
print('Qt checklist: passed')
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=Path(__file__).parent,
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
