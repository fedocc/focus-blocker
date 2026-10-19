"""Integration checks against an installed Anki backend, using temporary data."""
from pathlib import Path
import tempfile
import unittest
from anki_study import bridge
try:
    from anki.collection import Collection
except ImportError:
    Collection = None


@unittest.skipIf(Collection is None, 'Anki backend is not installed in this Python')
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.col = Collection(str(self.root / 'collection.anki2'))
        self.addCleanup(self.col.close)
        self.deck = self.col.decks.id('Учёба::DL')
        self.model = self.col.models.all()[0]
        for i in range(3):
            self.add_card('Q' + str(i), self.deck)
        self.ids = self.col.find_cards('')
        self.col.sched.suspend_cards(self.ids[:1])

    def add_card(self, text, deck):
        note = self.col.new_note(self.model)
        note.fields[0] = text
        self.col.add_note(note, deck)

    def test_backup_archive_restore_preserves_cards_and_preexisting_suspensions(self):
        backup = self.root / 'missing-parent' / 'user_files' / 'backups'
        self.assertEqual(bridge.archive_collection(self.col, backup), 2)
        self.assertTrue(list(backup.glob('*.colpkg')))
        self.assertEqual(len(self.col.find_cards('is:suspended')), 3)
        self.assertTrue(self.col.decks.name(self.deck).startswith('Архив · '))
        self.add_card('New card', self.col.decks.id('Новая колода'))
        bridge.restore_collection(self.col)
        self.assertEqual(self.col.decks.name(self.deck), 'Учёба::DL')
        self.assertEqual(self.col.find_cards('is:suspended'), self.ids[:1])
        self.assertEqual(len(self.col.find_cards('')), 4)
        self.assertIsNone(self.col.get_config('slovo_archive'))

    def test_conflicting_new_deck_prevents_partial_restore(self):
        bridge.archive_collection(self.col, self.root / 'backups')
        self.col.decks.id('Учёба')
        before = self.col.decks.name(self.deck)
        with self.assertRaises(ValueError):
            bridge.restore_collection(self.col)
        self.assertEqual(self.col.decks.name(self.deck), before)
        self.assertEqual(len(self.col.find_cards('is:suspended')), 3)
        self.assertIsNotNone(self.col.get_config('slovo_archive'))


@unittest.skipIf(Collection is None, 'Anki backend is not installed in this Python')
class BackgroundQueueTests(unittest.TestCase):
    def test_slow_focus_poll_does_not_hold_up_collection_work(self):
        # Use Anki's real two executors. No UI, network or user collection.
        import ast
        import concurrent.futures
        import threading
        from types import SimpleNamespace
        from aqt.qt import QCoreApplication
        from aqt.taskman import TaskManager
        app = QCoreApplication.instance() or QCoreApplication([])
        taskman = TaskManager(SimpleNamespace(weakref=lambda: None))
        tree = ast.parse((Path(__file__).parent / 'anki_study/dashboard.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'StudyDock')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        scope = {'mw': SimpleNamespace(taskman=taskman)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<study-background>', 'exec'), scope)
        started = threading.Event()
        release = threading.Event()
        def slow_io():
            started.set()
            release.wait(2)
        fake_dock = SimpleNamespace(closed=False)
        try:
            scope['run'](fake_dock, slow_io, lambda result: None)
            self.assertTrue(started.wait(1))
            future = taskman.run_in_background(lambda: 'review can save', uses_collection=True)
            self.assertEqual(future.result(timeout=.5), 'review can save')
        finally:
            release.set()
            taskman._collection_executor.shutdown(wait=True)
            taskman._no_collection_executor.shutdown(wait=True)
            app.processEvents()
