"""Study dashboard: separate from Anki reviews and their scheduling."""
import json
import time
from pathlib import Path

from aqt import mw, gui_hooks
from aqt.qt import (QAction, QComboBox, QDesktopServices, QDockWidget, QFileDialog, QDialog, QFrame,
                    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QProgressBar,
                    QListWidgetItem, QMessageBox, QPushButton, QTimer, Qt, QUrl,
                    QVBoxLayout, QWidget, QScrollArea, QToolButton)
from . import bridge

PREFS = Path(__file__).parent / 'user_files/preferences.json'

STYLE = '''
QDockWidget#slovo-study-blocks, QWidget#focusRoot, QWidget#focusHeader, QDialog#focusPicker {
    background: #0e222d; color: #e3edf0; font-family: "SF Pro Text"; font-size: 13px;
}
QWidget#focusRoot QLabel, QWidget#focusHeader QLabel, QDialog#focusPicker QLabel { background: transparent; border: none; }
QLabel#brand { color: #e8c58b; font-size: 15px; font-weight: 600; }
QLabel#eyebrow { color: #89aaa9; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#muted { color: #8ea8b4; font-size: 11px; }
QLabel#sectionTitle { color: #dce8eb; font-size: 13px; font-weight: 600; }
QLabel#phase { color: #d7b77d; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#timer { color: #f0d29e; font-size: 64px; font-weight: 600; letter-spacing: -2px; }
QLabel#taskHeading { color: #c9dbdf; font-size: 13px; }
QLabel#notice { color: #c4d8da; background: #19323e; border: 1px solid #2a4b57; border-radius: 10px; padding: 10px; font-size: 11px; }
QFrame#focusHero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1c3d4d,stop:1 #112832); border: 1px solid #385661; border-radius: 20px; }
QFrame#focusCard { background: #132b37; border: 1px solid #25424e; border-radius: 14px; }
QWidget#focusRoot QLineEdit, QDialog#focusPicker QLineEdit, QWidget#focusRoot QComboBox {
    background: #0e2430; color: #e2ebef; border: 1px solid #304c58; border-radius: 9px; padding: 10px; min-height: 18px; selection-background-color: #477275;
}
QWidget#focusRoot QLineEdit:focus, QDialog#focusPicker QLineEdit:focus { border-color: #b6a17b; }
QWidget#focusRoot QComboBox::drop-down { border: none; width: 24px; }
QWidget#focusRoot QComboBox QAbstractItemView { background: #17313e; color: #e2ebef; selection-background-color: #30535c; }
QWidget#focusRoot QPushButton, QDialog#focusPicker QPushButton { color: #c4d9de; background: #1b3643; border: 1px solid #304c58; border-radius: 9px; padding: 9px 12px; min-height: 18px; font-weight: 500; }
QWidget#focusRoot QPushButton:hover, QDialog#focusPicker QPushButton:hover { background: #254653; border-color: #52737a; }
QWidget#focusRoot QPushButton#primary { background: #e3bd7e; color: #152934; border: none; border-radius: 12px; font-size: 14px; font-weight: 600; padding: 14px; }
QWidget#focusRoot QPushButton#primary:hover { background: #f1d3a2; }
QWidget#focusRoot QPushButton#primary:disabled { background: #314650; color: #8b9fa6; }
QWidget#focusRoot QPushButton#quiet { background: transparent; border: none; color: #97b6bd; padding: 4px 0; font-size: 11px; }
QWidget#focusRoot QPushButton#quiet:hover { color: #e9c98f; }
QWidget#focusRoot QPushButton#emergency { background: transparent; border: none; color: #c39289; font-size: 11px; padding: 4px; }
QWidget#focusRoot QPushButton:disabled, QWidget#focusRoot QLineEdit:disabled { color: #718a95; border-color: #213b47; }
QToolButton { background: transparent; color: #8ca7b1; border: none; border-radius: 6px; padding: 4px; }
QToolButton:hover { background: #27414d; color: #e5c38d; }
QProgressBar { background: #223e49; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #d9ba82; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }
QScrollBar::handle:vertical { background: #35515c; border-radius: 2px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QDialog#focusPicker QListWidget { background: #102934; color: #dbe7eb; border: 1px solid #294550; border-radius: 12px; outline: none; padding: 6px; }
QDialog#focusPicker QListWidget::item { padding: 10px; border-radius: 7px; }
QDialog#focusPicker QListWidget::item:hover { background: #203f4c; }
QDialog#focusPicker QListWidget::item:selected { background: #2b4c56; }
QDialog#focusPicker QListWidget::indicator { width: 17px; height: 17px; }
'''


def label(text, name='muted'):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def card(parent_layout, name='focusCard'):
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 15, 16, 15)
    layout.setSpacing(10)
    parent_layout.addWidget(frame)
    return frame, layout


class Notice(QLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class StudyDock(QDockWidget):
    def __init__(self):
        super().__init__('Учебные блоки', mw)
        self.setObjectName('slovo-study-blocks')
        self.setMinimumWidth(355)
        self.setStyleSheet(STYLE)
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
        header = QWidget()
        header.setObjectName('focusHeader')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 14, 6)
        header_layout.addWidget(label('◉  Фокус', 'brand'))
        header_layout.addStretch()
        close = QToolButton()
        close.setText('×')
        close.setAccessibleName('Скрыть панель фокуса')
        close.clicked.connect(self.hide)
        header_layout.addWidget(close)
        self.setTitleBarWidget(header)
        shell = QWidget()
        shell.setObjectName('focusRoot')
        self.setWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName('focusRoot')
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 12, 18, 16)
        layout.setSpacing(14)
        _, hero = card(layout, 'focusHero')
        hero.setContentsMargins(16, 20, 16, 20)
        hero.setSpacing(4)
        self.phase_tag = label('ГОТОВ К СТАРТУ', 'phase')
        self.phase_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(self.phase_tag)
        self.timer_label = label('80:00', 'timer')
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(self.timer_label)
        self.heading = label('Один блок — одна задача', 'taskHeading')
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heading.setMaximumHeight(56)
        hero.addWidget(self.heading)
        cycle = label('80 мин фокуса  ·  10 / 30 мин отдыха')
        cycle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(cycle)
        day_row = QHBoxLayout()
        day_row.addWidget(label('ТВОЙ ДЕНЬ', 'eyebrow'))
        self.day_count = label('0 / 5', 'sectionTitle')
        day_row.addStretch()
        day_row.addWidget(self.day_count)
        layout.addLayout(day_row)
        self.day_progress = QProgressBar()
        self.day_progress.setRange(0, 6)
        self.day_progress.setTextVisible(False)
        self.day_progress.setFixedHeight(6)
        layout.addWidget(self.day_progress)
        self.progress = label('Пять основных блоков. Шестой — по желанию.')
        layout.addWidget(self.progress)
        self.task_card, task_layout = card(layout)
        task_layout.addWidget(label('01   Задача блока', 'sectionTitle'))
        self.task_list = QComboBox()
        self.task_list.addItem('Своя задача')
        self.task_list.currentIndexChanged.connect(self.choose_task)
        self.task_list.hide()
        task_layout.addWidget(self.task_list)
        self.title = QLineEdit()
        self.title.setPlaceholderText('Лекция по DL + конспект…')
        self.title.setAccessibleName('Результат учебного блока')
        self.title.setMaxLength(300)
        task_layout.addWidget(self.title)
        row = QHBoxLayout()
        self.refresh = QPushButton('↻  Обновить план')
        self.refresh.setObjectName('quiet')
        self.refresh.clicked.connect(self.load_plan)
        self.pair = QPushButton('Подключить Слово')
        self.pair.setObjectName('quiet')
        self.pair.clicked.connect(self.pair_slovo)
        row.addWidget(self.refresh)
        row.addStretch()
        row.addWidget(self.pair)
        task_layout.addLayout(row)
        _, apps_layout = card(layout)
        apps_title = QHBoxLayout()
        apps_title.addWidget(label('02   Приложения', 'sectionTitle'))
        apps_title.addStretch()
        self.app_count = label('1 выбрано')
        apps_title.addWidget(self.app_count)
        apps_layout.addLayout(apps_title)
        self.app_summary = label('Arc', 'taskHeading')
        apps_layout.addWidget(self.app_summary)
        self.app_picker_button = QPushButton('Выбрать приложения  →')
        self.app_picker_button.clicked.connect(self.pick_apps)
        apps_layout.addWidget(self.app_picker_button)
        apps_layout.addWidget(label('Anki и Фото доступны всегда'))
        self.picker = QDialog(self)
        self.picker.setObjectName('focusPicker')
        self.picker.setWindowTitle('Приложения для блока')
        self.picker.setStyleSheet(STYLE)
        self.picker.resize(420, 560)
        picker_layout = QVBoxLayout(self.picker)
        picker_layout.setContentsMargins(22, 22, 22, 22)
        picker_layout.setSpacing(14)
        picker_layout.addWidget(label('Что понадобится для учёбы?', 'sectionTitle'))
        picker_layout.addWidget(label('Выбери до старта. Твой выбор сохранится для следующего блока.'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск приложений…')
        self.search.textChanged.connect(self.filter_apps)
        picker_layout.addWidget(self.search)
        self.apps = QListWidget()
        selected = self.preferences.get('apps', ['company.thebrowser.Browser'])
        permanent = {'net.ankiweb.anki', 'com.apple.Photos', 'com.apple.finder', 'com.apple.systempreferences'}
        for bundle, name in bridge.applications():
            if bundle in permanent:
                continue
            item = QListWidgetItem(name)
            item.setToolTip(name)
            item.setData(Qt.ItemDataRole.UserRole, bundle)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if bundle in selected else Qt.CheckState.Unchecked)
            self.apps.addItem(item)
        picker_layout.addWidget(self.apps)
        picker_layout.addWidget(label('Anki, Фото и системные окна остаются доступны. Остальные приложения скрываются без закрытия документов.'))
        picker_actions = QHBoxLayout()
        cancel = QPushButton('Отмена')
        cancel.clicked.connect(self.picker.reject)
        done = QPushButton('Готово')
        done.clicked.connect(self.picker.accept)
        picker_actions.addWidget(cancel)
        picker_actions.addWidget(done)
        picker_layout.addLayout(picker_actions)
        self.update_app_summary()
        self.message = Notice('')
        self.message.setObjectName('notice')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.hide()
        layout.addWidget(self.message)
        layout.addStretch()
        footer = QWidget()
        footer.setObjectName('focusRoot')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 8, 18, 18)
        footer_layout.setSpacing(8)
        outer.addWidget(footer)
        self.start = QPushButton('Начать блок   →')
        self.start.setObjectName('primary')
        self.start.clicked.connect(self.start_block)
        self.title.textChanged.connect(self.update_start_enabled)
        footer_layout.addWidget(self.start)
        lock_note = label('80 минут. После старта настройки фиксируются.')
        lock_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_layout.addWidget(lock_note)
        self.emergency = QPushButton('Экстренно прервать…')
        self.emergency.setObjectName('emergency')
        self.emergency.clicked.connect(self.request_emergency)
        self.emergency.hide()
        footer_layout.addWidget(self.emergency)
        result_row = QHBoxLayout()
        self.proof = QPushButton('↗  Результат в Слове')
        self.proof.setObjectName('quiet')
        self.proof.clicked.connect(self.open_slovo)
        result_row.addWidget(self.proof)
        result_row.addStretch()
        self.receipt = QPushButton('Снимок фокуса')
        self.receipt.setObjectName('quiet')
        self.receipt.clicked.connect(self.save_receipt)
        result_row.addWidget(self.receipt)
        footer_layout.addLayout(result_row)
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
            for control in (self.title, self.task_list, self.apps, self.refresh, self.pair, self.app_picker_button):
                control.setEnabled(not locked)
            self.update_start_enabled()
            self.emergency.setEnabled(phase == 'focus' and value['emergency_available'])
            self.emergency.setVisible(phase == 'focus')
            self.phase_tag.setText({'idle': 'ГОТОВ К СТАРТУ', 'focus': 'ВРЕМЯ ФОКУСА', 'break': 'ВРЕМЯ ОТДЫХА'}[phase])
            self.start.setText({'idle': 'Начать блок   →', 'focus': 'Блок идёт · настройки закрыты', 'break': 'Сначала восстановись'}[phase])
            seconds = round(session['remaining']) if session else 4800
            self.timer_label.setText(f'{seconds // 60:02}:{seconds % 60:02}')
            count = value['completed_today']
            self.day_progress.setValue(min(count, 6))
            self.day_count.setText(f'{count} / 5')
            attempts = sum(session.get('attempts', {}).values()) + sum(session.get('app_attempts', {}).values()) if session else 0
            self.progress.setText(('Перерыв перед следующим блоком.' if phase == 'break' else
                                   f'Настройки зафиксированы · блокировок: {attempts}' if phase == 'focus' else 'Пять основных блоков. Шестой — по желанию.') +
                                  f'\nСрабатываний за день: {value.get("attempts_today", 0)}')
            if session:
                self.heading.setText(session['task']['title'])
            else:
                self.heading.setText('Один блок — одна задача')
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

    def update_start_enabled(self, *_):
        self.start.setEnabled(bool(self.title.text().strip()) and not self.busy and
                              bool(self.state) and not self.state.get('session') and
                              self.state.get('service_running', False))

    def update_app_summary(self):
        names = [self.apps.item(i).text() for i in range(self.apps.count())
                 if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        self.app_summary.setText(' · '.join(names[:3]) + (f'  +{len(names)-3}' if len(names) > 3 else '') if names else 'Только Anki и всегда доступные приложения')
        self.app_summary.setToolTip(', '.join(names))
        self.app_count.setText(f'Выбрано: {len(names)}')

    def pick_apps(self):
        previous = [self.apps.item(i).checkState() for i in range(self.apps.count())]
        self.search.clear()
        if self.picker.exec() != QDialog.DialogCode.Accepted:
            for index, state in enumerate(previous):
                self.apps.item(index).setCheckState(state)
        self.update_app_summary()

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
        self.task_list.setVisible(bool(self.tasks))
        self.pair.setText('Слово подключено')
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
        for control in (self.title, self.task_list, self.apps, self.refresh, self.pair, self.app_picker_button):
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
