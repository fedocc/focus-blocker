"""Exercise the actual Qt checklist in an isolated, offscreen process."""
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import sys
import unittest


class StudyUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_wallpaper_loading_cannot_commit_a_stale_selection(self):
        source = (Path(__file__).parent / 'anki_study/dashboard.py').read_text()
        function = source[source.index("  let pending='', generation=0"):source.index('  window.slovoUpdateDaylight=')]
        program = r'''
const assert=require('node:assert/strict');
const values=new Map(), loads=[], timers=new Map();let clock=0;
const root={dataset:{},style:{setProperty:(k,v)=>values.set(k,v),removeProperty:k=>values.delete(k)}};
global.document={documentElement:root,querySelector:()=>({})};global.window={};
global.Image=class{constructor(){loads.push(this)}};
global.matchMedia=()=>({matches:false});global.requestAnimationFrame=f=>f();
global.getComputedStyle=()=>({transform:'matrix(1,0,0,1,-500,-300)',filter:'blur(0px)',opacity:'0.4'});
global.setTimeout=f=>{timers.set(++clock,f);return clock};global.clearTimeout=id=>timers.delete(id);
''' + function + r'''
const choose=path=>{window.slovoHomeState={wallpaper:{path,scene:path}};updateDaylight()};
const flush=()=>{const jobs=[...timers.values()];timers.clear();jobs.forEach(f=>f())};
choose('skyline');loads.at(-1).onload();assert.equal(root.dataset.slovoWallpaper,'skyline');
choose('sea');const sea=loads.at(-1);choose('amg');const amg=loads.at(-1);
sea.onload();flush();assert.equal(root.dataset.slovoWallpaper,'skyline');
amg.onload();assert.equal(values.get('--slovo-wallpaper-fade'),'0.4');flush();
assert.equal(root.dataset.slovoWallpaper,'amg');
choose('night');const night=loads.at(-1);choose('amg');night.onload();flush();
assert.equal(root.dataset.slovoWallpaper,'amg','returning to current wallpaper cancels pending load');
choose('missing');loads.at(-1).onerror();choose('missing');loads.at(-1).onload();flush();
assert.equal(root.dataset.slovoWallpaper,'missing','failed load may retry');
console.log('wallpaper loading: passed');
'''
        result = subprocess.run(['node', '-e', program], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_calendar_rules_include_nested_css_style_rules(self):
        script = (Path(__file__).parent / 'anki_study/focus-stats.js').read_text()
        function = script[script.index(' function calendarRules('):script.index(' calendarStyle.textContent=')]
        program = function + """
const rules=[{selectorText:'#onigiri-heatmap-container', cssRules:[],
 cssText:'#onigiri-heatmap-container {border-radius:24px}'},
 {cssText:'@media(max-width:1000px) { ignored }',cssRules:[
  {selectorText:'#onigiri-heatmap-container .heatmap-grid',cssRules:[],
   cssText:'#onigiri-heatmap-container .heatmap-grid {display:grid}'}]},
 {selectorText:'.unrelated',cssRules:[],cssText:'.unrelated {color:red}'}];
process.stdout.write(calendarRules(rules));
"""
        result = subprocess.run(['node', '-e', program], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('#slovo-focus-heatmap {border-radius:24px}', result.stdout)
        self.assertIn('@media(max-width:1000px)', result.stdout)
        self.assertIn('#slovo-focus-heatmap .heatmap-grid {display:grid}', result.stdout)
        self.assertNotIn('.unrelated', result.stdout)

    @unittest.skipUnless(importlib.util.find_spec('aqt'), 'Anki Qt is unavailable in this Python')
    def test_multiselect_survives_plan_refresh_and_locks_with_session(self):
        program = r'''
import tempfile
from pathlib import Path
from aqt.qt import QApplication, QMainWindow, QListWidgetItem, Qt, QTimer, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
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
    # Selected applications come first and saving does not require starting a block.
    for bundle,name,checked in [('z','Z Editor',True),('a','A Unselected',False),('b','B Browser',True)]:
        item=QListWidgetItem(name);item.setData(Qt.ItemDataRole.UserRole,bundle)
        item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        dock.apps.addItem(item)
    dock.sort_apps()
    assert [dock.apps.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)] == ['b','z','a']
    QTimer.singleShot(0, dock.picker.accept)
    dock.pick_apps()
    assert ui.json.loads(ui.PREFS.read_text())['apps'] == ['b','z']
    QTimer.singleShot(0, lambda: (dock.apps.item(0).setCheckState(Qt.CheckState.Unchecked),dock.picker.reject()))
    dock.pick_apps()
    assert dock.apps.item(0).checkState() == Qt.CheckState.Checked
    # Wallpaper settings preserve unrelated private preferences and Cancel is inert.
    def change_wallpaper():
        dialog=app.activeModalWidget()
        theme,period=dialog.findChildren(ui.QComboBox)
        theme.setCurrentIndex(theme.findData('sea'))
        period.setCurrentIndex(period.findData('night'))
        dialog.accept()
    QTimer.singleShot(0, change_wallpaper)
    ui.choose_wallpaper()
    assert dock.preferences['wallpaper_theme']=='sea'
    assert dock.preferences['wallpaper_period']=='night'
    saved=ui.PREFS.read_bytes()
    QTimer.singleShot(0, lambda: app.activeModalWidget().reject())
    ui.choose_wallpaper()
    assert ui.PREFS.read_bytes()==saved
    # A signal test is non-modal, repeats until acknowledgement, and leaves the real timer intact.
    notice=dock.phase_notice
    ui.bridge.play_alert_sound=lambda *_: None
    ui.QApplication.beep=lambda: None
    before_signal=ui.json.dumps(dock.state,sort_keys=True)
    notice.present({'id':'demo','kind':'break'},demo=True)
    assert not notice.isModal()
    assert notice.repeat.isActive()
    assert notice.event['kind']=='break'
    assert ui.json.dumps(dock.state,sort_keys=True)==before_signal
    notice.acknowledge()
    assert not notice.repeat.isActive() and notice.event is None
    assert ui.json.dumps(dock.state,sort_keys=True)==before_signal
    # Silence and window closing are not permission to start the break.
    pending={'id':'extra','phase':'focus','remaining':0,'awaiting_rest':True,'overtime_seconds':125,
             'overtime_anchor_mono':ui.time.clock_gettime(ui.time.CLOCK_MONOTONIC),'task':{'title':'DL'}}
    dock.state={'session':pending,'supports_confirmed_break':True,'service_running':True}
    notice.observe(dock.state)
    assert notice.event['kind']=='overtime' and notice.repeat.isActive()
    assert notice.clock.text().startswith('+02:05')
    notice.silent.setChecked(True)
    assert not notice.repeat.isActive()
    assert dock.preferences['alert_silent_overtime']=='extra'
    notice.reject()
    assert not notice.isVisible() and dock.state['session']['phase']=='focus'
    notice.dismiss();notice.observe(dock.state)
    assert notice.silent.isChecked() and not notice.repeat.isActive()
    notice.close()
    assert not dock.busy and dock.state['session']['phase']=='focus'
    notice.show()
    calls=[]
    ui.bridge.helper=lambda command,payload=None: calls.append((command,payload)) or dict(pending,phase='break',awaiting_rest=False,remaining=600,ends_at=ui.time.time()+600)
    dock.run=lambda work,done: done(work())
    dock.poll=lambda:None
    notice.acknowledge()
    assert calls==[('study-break-start',{'session_id':'extra'})]
    assert dock.state['session']['phase']=='break'
    assert notice.event is None and not notice.repeat.isActive()
    assert dock.state['session']['remaining']==600
    # Wrapping and boundary wheel handling do not depend on the outer dock.
    dock.state['session']=None
    long_tasks=[{'title':('Полное длинное название задачи без обрезки '*4)+str(i),'occurrence_id':str(i)} for i in range(12)]
    dock.render_tasks(long_tasks)
    dock.show();window.show();app.processEvents()
    dock.task_list.resize(280,220);app.processEvents()
    dock.task_list.reflow()
    assert dock.task_list.item(0).sizeHint().height()>60
    wheel=QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(0,0),QPoint(0,120),
                      Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
    dock.task_list.verticalScrollBar().setValue(0)
    wheel.ignore();dock.task_list.wheelEvent(wheel)
    assert wheel.isAccepted(), 'wheel at boundary must not reach the outer scroll area'
    dock.poller.stop()
    dock.display_timer.stop()
    notice.dismiss()
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
