"""Cached collection operations and the actual injected review widget."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).parent


class ReviewWidgetTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('aqt'), 'Anki Qt is not installed')
    def test_setup_cache_profile_lifecycle_and_repair_navigation(self):
        program = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from aqt.qt import QApplication
from anki.collection import Collection
from anki_study import dashboard as ui
app=QApplication([])
operations=[]
class Operation:
 def __init__(self,parent,op,success):self.op=op;self.success=success
 def failure(self,fn):self.failed=fn;return self
 def run_in_background(self):operations.append(self)
with TemporaryDirectory() as td:
 col=Collection(str(Path(td)/'collection.anki2'))
 calls=[];ui.PREFS=Path(td)/'user_files/preferences.json'
 mw=SimpleNamespace(col=col,apply_collection_options=lambda:calls.append('options'),deckBrowser=object())
 ui.mw=mw;ui.update_home_widget=lambda:calls.append('render')
 with patch('aqt.operations.QueryOp',Operation):
  controller=ui.ReviewHealth();controller.refresh(setup=True)
  controller.refresh();assert len(operations)==1 and controller.pending
  assert not col.get_config('fsrs'),'mutation is background work, not startup repaint'
  op=operations.pop();op.success(op.op(col))
  assert controller.data['fsrs'] and controller.data['configured']
  assert calls==['options','render']
  controller.refresh();assert not operations,'timer repaints use cached data'
  controller.last_at=0;controller.refresh();assert len(operations)==1
  result=operations[0].op(col)
  controller.closed=True;operations.pop().success(result)
  assert calls==['options','render'],'late result never updates a closed profile'
  other=ui.ReviewHealth();other.last_at=0;other.refresh()
  result=operations[0].op(col);mw.col=object();operations.pop().success(result)
  assert calls==['options','render'],'late result never crosses profile boundaries'
  mw.col=col
 with patch.object(ui,'open_review_queue') as opened:
  assert ui.home_message((False,None),'slovo:review-repair',object())==(False,None)
  opened.assert_not_called()
  assert ui.home_message((False,None),'slovo:review-repair',mw.deckBrowser)==(True,None)
  opened.assert_called_once()
 col.close()
print('Review cache / profile lifecycle passed')
'''
        result = subprocess.run([sys.executable, '-B', '-c', program], cwd=ROOT,
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_dom_modes_updates_empty_errors_escape_and_queue_button(self):
        available = subprocess.run(['node', '-e', "require('jsdom')"], capture_output=True)
        if available.returncode:
            self.skipTest('Optional jsdom dependency is not installed')
        program = r'''
const assert=require('assert/strict'),fs=require('fs'),{JSDOM}=require('jsdom');
const dom=new JSDOM('<main><div class="unified-grid"></div></main>',{runScripts:'outside-only'});
const w=dom.window,events=[];w.pycmd=message=>events.push(message);
const health={loading:false,review_due:8,overdue:3,repair_count:2,review_answers:4,recalled:3,retention:75,fsrs:true};
const data={today:'2026-10-23',mode:'cards',records:[],days:{},usage:{days:{}},review_health:health};
const script=fs.readFileSync('anki_study/focus-stats.js','utf8').replaceAll('ADDON_PACKAGE','slovo_study_blocks').replaceAll('PAYLOAD',JSON.stringify(data));
w.eval(script);
const panel=w.document.querySelector('#slovo-review-health');
assert.equal(panel.hidden,false);assert.match(panel.textContent,/75%/);
assert.equal(panel.querySelectorAll('.ss-review-tiles > div').length,3);
panel.querySelector('button').click();assert.deepEqual(events,['slovo:review-repair']);
w.document.querySelector('[data-mode=focus]').click();assert.equal(panel.hidden,true);
w.document.querySelector('[data-mode=cards]').click();assert.equal(panel.hidden,false);
w.slovoFocusStats.update({...data,review_health:{...health,retention:null,review_answers:0,recalled:0,repair_count:9}});
assert.match(panel.textContent,/Пока нет/);assert.match(panel.textContent,/9/);
w.slovoFocusStats.update({...data,review_health:{...health,setup_error:'<img src=x onerror=alert(1)>'}});
assert.match(panel.textContent,/<img/);assert.equal(panel.querySelector('img'),null);
w.slovoFocusStats.update({...data,review_health:{loading:false,error:'database busy'}});
assert.match(panel.textContent,/database busy/);
w.eval(script);assert.equal(w.document.querySelectorAll('#slovo-review-health').length,1);
assert.equal(w.document.querySelectorAll('#slovo-stats-switch').length,1);
assert.equal(w.document.querySelector('.unified-grid').children.length,0,'health never changes fixed grid columns');
console.log('Review DOM passed');
'''
        result = subprocess.run(['node', '-e', program], cwd=ROOT, env=os.environ,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
