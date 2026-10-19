"""Study dashboard: separate from Anki reviews and their scheduling."""
import json
import time
from pathlib import Path

from aqt import mw, gui_hooks
from aqt.qt import (QAction, QComboBox, QDesktopServices, QDockWidget, QFileDialog,
                    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QProgressBar,
                    QListWidgetItem, QMessageBox, QPushButton, QTimer, Qt, QUrl,
                    QVBoxLayout, QWidget)
from . import bridge

PREFS = Path(__file__).parent / 'user_files/preferences.json'


class StudyDock(QDockWidget):
    def __init__(self):
        super().__init__('Учебные блоки', mw)
        self.setObjectName('slovo-study-blocks')
        self.setMinimumWidth(305)
        self.state = None
        self.busy = False
        self.polling = False
        self.plan_busy = False
        self.tasks = []
        self.last_phase = None
        self.last_plan_at = 0
        self.closed = False
        try:
            self.preferences = json.loads(PREFS.read_text())
        except (OSError, ValueError):
            self.preferences = {}
        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        self.heading = QLabel('Пять основных блоков · шестой по желанию')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.timer_label = QLabel('80:00')
        self.timer_label.setStyleSheet('font-size:40px;font-weight:700;color:#caa65c;padding:12px 0;')
        layout.addWidget(self.timer_label)
        self.progress = QLabel('Подключаем системный таймер…')
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        self.day_progress = QProgressBar()
        self.day_progress.setRange(0, 6)
        self.day_progress.setFormat('%v блоков · цель 5 + 1')
        layout.addWidget(self.day_progress)
        self.task_list = QComboBox()
        self.task_list.addItem('Своя задача')
        self.task_list.currentIndexChanged.connect(self.choose_task)
        layout.addWidget(self.task_list)
        self.title = QLineEdit()
        self.title.setPlaceholderText('Что получится за этот блок?')
        self.title.setMaxLength(300)
        layout.addWidget(self.title)
        row = QHBoxLayout()
        self.refresh = QPushButton('Обновить план')
        self.refresh.clicked.connect(self.load_plan)
        self.pair = QPushButton('Подключить Слово')
        self.pair.clicked.connect(self.pair_slovo)
        row.addWidget(self.refresh)
        row.addWidget(self.pair)
        layout.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск приложений для этого блока')
        self.search.textChanged.connect(self.filter_apps)
        layout.addWidget(self.search)
        self.apps = QListWidget()
        selected = self.preferences.get('apps', ['company.thebrowser.Browser'])
        for bundle, name in bridge.applications():
            item = QListWidgetItem(f'{name} · {bundle}')
            item.setData(Qt.ItemDataRole.UserRole, bundle)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if bundle in selected else Qt.CheckState.Unchecked)
            self.apps.addItem(item)
        layout.addWidget(self.apps)
        note = QLabel('Anki, Фото и системные окна доступны всегда. Лишние приложения скрываются без закрытия документов. Документация и поиск доступны в выбранном браузере; лекции — по существующему белому списку.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.start = QPushButton('Зафиксировать и начать · 80 минут')
        self.start.clicked.connect(self.start_block)
        layout.addWidget(self.start)
        self.emergency = QPushButton('Экстренно прервать…')
        self.emergency.clicked.connect(self.request_emergency)
        layout.addWidget(self.emergency)
        self.proof = QPushButton('Слово: приложить результат')
        self.proof.clicked.connect(self.open_slovo)
        layout.addWidget(self.proof)
        self.receipt = QPushButton('Сохранить снимок фокуса')
        self.receipt.clicked.connect(self.save_receipt)
        layout.addWidget(self.receipt)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.title.setEnabled(False)
        self.start.setEnabled(False)
        self.emergency.setEnabled(False)
        self.poller = QTimer(self)
        self.poller.timeout.connect(self.poll)
        self.poller.start(2000)
        self.poll()
        if self.preferences.get('token'):
            self.load_plan()

    def run(self, function, callback):
        def done(future):
            if self.closed:
                return
            try:
                result = future.result()
            except Exception as error:
                self.message.setText(str(error))
                result = None
            callback(result)
        mw.taskman.run_in_background(function, done)

    def poll(self):
        if self.busy or self.polling:
            return
        self.polling = True
        def updated(value):
            self.polling = False
            if self.busy:
                return
            if value is None:
                self.start.setEnabled(False)
                return
            self.state = value
            session = value['session']
            phase = session['phase'] if session else 'idle'
            locked = bool(session)
            for control in (self.title, self.task_list, self.apps, self.refresh, self.pair):
                control.setEnabled(not locked)
            self.start.setEnabled(not locked and value.get('service_running', False))
            self.emergency.setEnabled(phase == 'focus' and value['emergency_available'])
            seconds = round(session['remaining']) if session else 4800
            self.timer_label.setText(f'{seconds // 60:02}:{seconds % 60:02}')
            count = value['completed_today']
            self.day_progress.setValue(min(count, 6))
            attempts = sum(session.get('attempts', {}).values()) + sum(session.get('app_attempts', {}).values()) if session else 0
            self.progress.setText(f'Сегодня: {count}/5 · шестой по желанию\n' +
                                  ('Отдых. Следующий блок после перерыва.' if phase == 'break' else
                                   f'Блок зафиксирован · срабатываний: {attempts}' if phase == 'focus' else 'Можно подготовить следующий блок.') +
                                  f'\nЗа день срабатываний: {value.get("attempts_today", 0)}')
            if session:
                self.heading.setText(session['task']['title'])
            else:
                self.heading.setText('Пять основных блоков · шестой по желанию')
            if not value.get('service_running', False):
                self.message.setText('Системная служба не запущена. Блокировка не подтверждена; новые блоки закрыты.')
            elif session and session.get('app_guard_error'):
                self.message.setText('Не работает ограничение приложений: ' + session['app_guard_error'])
            if phase == 'focus':
                pending = session.get('emergency')
                self.emergency.setText('Подтвердить прерывание…' if pending else 'Экстренно прервать…')
            if phase == 'idle' and self.preferences.get('token') and time.monotonic() - self.last_plan_at > 300:
                self.load_plan()
            if phase == 'break' and self.last_phase == 'focus':
                self.message.setText('Блок завершён. Отдохни и приложи результат в Слове. Таймер сам задачу не отмечает.')
            self.last_phase = phase
        self.run(lambda: bridge.helper('study-status'), updated)

    def filter_apps(self, text):
        for index in range(self.apps.count()):
            item = self.apps.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def choose_task(self, index):
        if 0 < index <= len(self.tasks):
            self.title.setText(self.tasks[index - 1]['title'])

    def pair_slovo(self):
        token, ok = QInputDialog.getText(self, 'Подключить Слово', 'В Слове: Меню → Учёба в Anki → Создать ключ.\nВставь ключ:', QLineEdit.EchoMode.Password)
        if ok and token.strip():
            candidate = token.strip()
            def paired(value):
                if value is not None:
                    self.preferences['token'] = candidate
                    bridge.save_preferences(PREFS, self.preferences)
                    self.apply_plan(value)
            self.run(lambda: bridge.plan(candidate), paired)

    def load_plan(self):
        if self.plan_busy:
            return
        if not self.preferences.get('token'):
            self.message.setText('Подключи Слово один раз. До подключения можно ввести задачу вручную.')
            return
        self.plan_busy = True
        self.last_plan_at = time.monotonic()
        def loaded(value):
            self.plan_busy = False
            if value is not None:
                self.apply_plan(value)
        self.run(lambda: bridge.plan(self.preferences['token']), loaded)

    def apply_plan(self, value):
        # A late network response must not replace the committed task UI.
        if self.state and self.state.get('session'):
            return
        self.tasks = value['tasks']
        self.task_list.clear()
        self.task_list.addItem('Своя задача')
        for task in self.tasks:
            self.task_list.addItem(task['title'])
        self.message.setText(f'План на {value["date"]}: задач {len(self.tasks)}. Выбери одну и нужные приложения.')

    def start_block(self):
        if self.busy or not self.title.text().strip():
            return
        apps = [self.apps.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.apps.count())
                if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        task = {'title': self.title.text().strip()}
        index = self.task_list.currentIndex()
        if index > 0:
            task.update({key: self.tasks[index - 1][key] for key in ('url', 'occurrence_id')})
        answer = QMessageBox.question(self, 'Начать блок?', '80 минут без изменения задачи и приложений. Затем обязательный перерыв 10 / 30 минут. Экстренный выход: причина, ожидание 60 секунд и подтверждение; один раз в день.\n\nПеред стартом сохрани документы и останови фоновую музыку, если она мешает.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.preferences['apps'] = apps
        bridge.save_preferences(PREFS, self.preferences)
        self.busy = True
        self.start.setEnabled(False)
        for control in (self.title, self.task_list, self.apps, self.refresh, self.pair):
            control.setEnabled(False)
        def started(value):
            self.busy = False
            if value is not None:
                self.message.setText('Условия блока зафиксированы системной службой.')
            self.poll()
        self.run(lambda: bridge.helper('study-start', {'task': task, 'apps': apps}), started)

    def request_emergency(self):
        if self.busy or not self.state or not self.state.get('session'):
            return
        session = self.state['session']
        pending = session.get('emergency')
        if pending:
            text, ok = QInputDialog.getText(self, 'Прервать блок', 'После 60 секунд ожидания введи: ПРЕРВАТЬ БЛОК\nБлок будет прерван, дневной выход потрачен.')
            command = 'study-emergency-confirm'
            payload = {'session_id': session['id'], 'confirmation': text}
        else:
            text, ok = QInputDialog.getMultiLineText(self, 'Экстренный выход', 'Почему необходимо прервать блок? Минимум 15 символов.\nПосле запроса блок продолжится ещё минимум 60 секунд.')
            command = 'study-emergency-request'
            payload = {'session_id': session['id'], 'reason': text}
        if not ok:
            return
        self.busy = True
        def finished(value):
            self.busy = False
            if value is not None:
                self.message.setText('Подожди 60 секунд и подтверди выход той же кнопкой.' if command.endswith('request') else 'Блок прерван. Сегодня экстренный выход недоступен.')
            self.poll()
        self.run(lambda: bridge.helper(command, payload), finished)

    def open_slovo(self):
        session = self.state.get('session') if self.state else None
        url = (session or {}).get('task', {}).get('url', bridge.ORIGIN + '/')
        QDesktopServices.openUrl(QUrl(url))

    def save_receipt(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Снимок фокуса', 'focus.png', 'PNG (*.png)')
        if path:
            self.grab().save(path, 'PNG')


def show_study():
    if not getattr(mw, '_slovo_study_dock', None):
        mw._slovo_study_dock = StudyDock()
        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, mw._slovo_study_dock)
    mw._slovo_study_dock.show()
    mw._slovo_study_dock.raise_()


def loaded():
    action = QAction('Учебные блоки · Слово', mw)
    action.triggered.connect(show_study)
    mw.form.menuTools.addAction(action)
    mw._slovo_study_action = action
    show_study()


gui_hooks.profile_did_open.append(loaded)


def unloaded():
    dock = getattr(mw, '_slovo_study_dock', None)
    if dock:
        dock.closed = True
        dock.poller.stop()
        mw.removeDockWidget(dock)
        dock.hide()
        dock.deleteLater()
        mw._slovo_study_dock = None
    action = getattr(mw, '_slovo_study_action', None)
    if action:
        mw.form.menuTools.removeAction(action)
        action.deleteLater()
        mw._slovo_study_action = None


gui_hooks.profile_will_close.append(unloaded)
