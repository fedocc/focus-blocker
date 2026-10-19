"""Study dashboard: separate from Anki reviews and their scheduling."""
import json
import time
import sys
import math
from pathlib import Path

from aqt import mw, gui_hooks
from aqt.qt import (QAction, QComboBox, QDesktopServices, QDockWidget, QFileDialog, QDialog, QFrame,
                    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QProgressBar,
                    QListWidgetItem, QMessageBox, QPushButton, QTimer, Qt, QUrl, QAbstractItemView,
                    QVBoxLayout, QWidget, QScrollArea, QToolButton, QSizePolicy)
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
QWidget#focusRoot QListWidget#focusTasks { background:#102934; color:#dbe7eb; border:1px solid #294550; border-radius:9px; outline:none; padding:4px; }
QWidget#focusRoot QListWidget#focusTasks::item { padding:9px 4px; }
QWidget#focusRoot QListWidget#focusTasks::indicator { width:17px; height:17px; }
'''


def label(text, name='muted'):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
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


class MenuTimer:
    """Text-only macOS status item; display preferences never control a block.

    All AppKit calls run on Qt's main thread. Each objc_msgSend signature has
    its own callable, so setting argument types cannot race another bridge.
    """
    def __init__(self, dock):
        import ctypes as c
        self.c = c
        self.objc = c.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes = [c.c_char_p]
        self.objc.objc_getClass.restype = c.c_void_p
        self.objc.sel_registerName.argtypes = [c.c_char_p]
        self.objc.sel_registerName.restype = c.c_void_p
        self.address = c.cast(self.objc.objc_msgSend, c.c_void_p).value
        self.dock = dock
        self.bar = self.send(self.objc.objc_getClass(b'NSStatusBar'), 'systemStatusBar')
        self.item = self.send(self.bar, 'statusItemWithLength:', [-1.0], [c.c_double])
        # AppKit returns an autoreleased item and NSStatusBar does not own it.
        # Keep it alive across Qt event-loop autorelease-pool drains.
        self.item = self.send(self.item, 'retain')
        self.button = self.send(self.item, 'button')
        font = self.send(self.objc.objc_getClass(b'NSFont'), 'monospacedDigitSystemFontOfSize:weight:', [12.0, 0.0], [c.c_double, c.c_double])
        self.send(self.button, 'setFont:', [font], [c.c_void_p], None)
        self.timer = QTimer(dock)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.seen_at = time.monotonic()
        self.tick()

    def send(self, receiver, selector, args=(), types=(), result='pointer'):
        c = self.c
        output = c.c_void_p if result == 'pointer' else result
        call = c.CFUNCTYPE(output, c.c_void_p, c.c_void_p, *types)(self.address)
        return call(receiver, self.objc.sel_registerName(selector.encode()), *args)

    def string(self, text):
        return self.send(self.objc.objc_getClass(b'NSString'), 'stringWithUTF8String:', [text.encode()], [self.c.c_char_p])

    def tick(self):
        state = self.dock.state
        session = (state or {}).get('session')
        text = 'Фокус · готов'
        tip = 'Учебные блоки · Anki'
        if session:
            remaining = math.ceil(bridge.remaining_seconds(state))
            prefix = '◉' if session['phase'] == 'focus' else '☕'
            text = f'{prefix} {remaining // 60:02}:{remaining % 60:02}'
            tip = session['task']['title']
        elif not state:
            text = 'Фокус · …'
        self.send(self.button, 'setTitle:', [self.string(text)], [self.c.c_void_p], None)
        self.send(self.button, 'setToolTip:', [self.string(tip)], [self.c.c_void_p], None)

    def set_visible(self, visible):
        self.send(self.item, 'setVisible:', [bool(visible)], [self.c.c_bool], None)

    def close(self):
        self.timer.stop()
        self.send(self.bar, 'removeStatusItem:', [self.item], [self.c.c_void_p], None)
        self.send(self.item, 'release', result=None)


class StudyDock(QDockWidget):
    def __init__(self):
        super().__init__('Учебные блоки', mw)
        self.setObjectName('slovo-study-blocks')
        self.setMinimumWidth(355)
        self.setMaximumWidth(420)
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setStyleSheet(STYLE)
        self.state = None
        self.busy = False
        self.polling = False
        self.plan_busy = False
        self.tasks = []
        self.selected_ids = set()
        self.plan_date = ''
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
        self.heading = label('Выбери задачи для блока', 'taskHeading')
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
        self.day_progress.setRange(0, 5)
        self.day_progress.setTextVisible(False)
        self.day_progress.setFixedHeight(6)
        layout.addWidget(self.day_progress)
        self.progress = label('Пять основных блоков. Шестой — по желанию.')
        layout.addWidget(self.progress)
        self.task_card, task_layout = card(layout)
        task_layout.addWidget(label('01   Задачи блока', 'sectionTitle'))
        self.task_list = QListWidget()
        self.task_list.setObjectName('focusTasks')
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.task_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.task_list.itemChanged.connect(self.selection_changed)
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
        self.message.setMinimumWidth(0)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        self.display_timer = QTimer(self)
        self.display_timer.timeout.connect(self.tick_display)
        self.display_timer.start(250)
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
        # Helper/network I/O never touches the collection. Using Anki's default
        # collection executor here makes every review wait for the status poll.
        mw.taskman.run_in_background(function, done, uses_collection=False)

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
            status_item = getattr(self, 'menu_timer', None)
            if status_item:
                status_item.seen_at = time.monotonic()
                status_item.tick()
            update_home_widget()
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
            self.tick_display()
            count = value['completed_today']
            self.day_progress.setValue(min(count, 6))
            self.day_count.setText(f'{count} / 5')
            attempts = sum(session.get('attempts', {}).values()) + sum(session.get('app_attempts', {}).values()) if session else 0
            self.progress.setText(('Перерыв перед следующим блоком.' if phase == 'break' else
                                   f'Настройки зафиксированы · блокировок: {attempts}' if phase == 'focus' else 'Пять основных блоков. Шестой — по желанию.') +
                                  f'\nСрабатываний за день: {value.get("attempts_today", 0)}')
            if session:
                self.heading.setText(session['task']['title'])
                committed = session['task'].get('items', [session['task']])
                if self.last_phase != phase:
                    self.render_tasks(committed, locked=True)
            else:
                self.heading.setText('Выбери задачи для блока')
                if self.last_phase in ('focus', 'break'):
                    self.render_tasks(self.tasks)
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

    def tick_display(self):
        seconds = math.ceil(bridge.remaining_seconds(self.state))
        self.timer_label.setText(f'{seconds // 60:02}:{seconds % 60:02}')

    def selected_tasks(self):
        return [task for task in self.tasks if task['occurrence_id'] in self.selected_ids]

    def render_tasks(self, tasks, locked=False):
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for task in tasks:
            item = QListWidgetItem(task['title'])
            item.setToolTip(task['title'])
            item.setData(Qt.ItemDataRole.UserRole, task.get('occurrence_id'))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if locked or task.get('occurrence_id') in self.selected_ids else Qt.CheckState.Unchecked)
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        self.task_list.setFixedHeight(min(220, max(75, len(tasks) * 43 + 14)))
        self.task_list.setVisible(bool(tasks))
        self.task_list.setEnabled(not locked)
        self.title.setVisible(not locked and not self.selected_ids)

    def selection_changed(self, *_):
        if self.busy or (self.state or {}).get('session'):
            return
        self.selected_ids = {self.task_list.item(i).data(Qt.ItemDataRole.UserRole)
                             for i in range(self.task_list.count())
                             if self.task_list.item(i).checkState() == Qt.CheckState.Checked}
        self.title.setVisible(not self.selected_ids)
        self.update_start_enabled()
        update_home_widget()

    def filter_apps(self, text):
        for index in range(self.apps.count()):
            item = self.apps.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def update_start_enabled(self, *_):
        self.start.setEnabled(bool(self.selected_ids or self.title.text().strip()) and not self.busy and
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

    def toggle_task(self, index):
        if self.busy or (self.state or {}).get('session') or not 0 <= index < len(self.tasks):
            return
        item = self.task_list.item(index)
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)

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
        update_home_widget()
        self.last_plan_at = time.monotonic()
        def loaded(value):
            self.plan_busy = False
            if value is not None:
                self.apply_plan(value)
            update_home_widget()
        self.run(lambda: bridge.plan(self.preferences['token']), loaded)

    def apply_plan(self, value):
        # A late network response must not replace the committed task UI.
        if self.state and self.state.get('session'):
            return
        self.tasks = value['tasks']
        self.plan_date = value['date']
        self.selected_ids.intersection_update(task['occurrence_id'] for task in self.tasks)
        self.render_tasks(self.tasks)
        self.pair.setText('Слово подключено')
        self.message.setText(f'План на {value["date"]}: задач {len(self.tasks)}. Отметь нужные задачи и выбери приложения.')
        self.update_start_enabled()
        update_home_widget()

    def start_block(self):
        if self.busy or (self.state or {}).get('session') or not (self.selected_ids or self.title.text().strip()):
            return
        apps = [self.apps.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.apps.count())
                if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        selected = self.selected_tasks()
        if len(selected) > 1 and not (self.state or {}).get('supports_task_groups'):
            self.message.setText('Для нескольких задач нужно обновить системную службу: запусти repair-study.command из подготовленного пакета.')
            return
        try:
            task = bridge.block_task(selected, self.title.text())
        except ValueError as error:
            self.message.setText(str(error))
            return
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
                self.state = dict(self.state or {}, session=value)
                self.tick_display()
                update_home_widget()
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


def ensure_study():
    if not getattr(mw, '_slovo_study_dock', None):
        mw._slovo_study_dock = StudyDock()
        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, mw._slovo_study_dock)
        mw._slovo_study_dock.hide()
    return mw._slovo_study_dock


def show_study():
    dock = ensure_study()
    dock.show()
    mw.resizeDocks([dock], [380], Qt.Orientation.Horizontal)
    dock.raise_()


def loaded():
    mw.addonManager.setWebExports(__name__, r'wallpaper-(dawn|day|evening|night)\.png')
    action = QAction('Учебные блоки · Слово', mw)
    action.triggered.connect(show_study)
    mw.form.menuTools.addAction(action)
    mw._slovo_study_action = action
    archive = QAction('Архивировать текущие колоды…', mw)
    archive.triggered.connect(archive_decks)
    mw.form.menuTools.addAction(archive)
    mw._slovo_archive_action = archive
    restore = QAction('Вернуть колоды из архива…', mw)
    restore.triggered.connect(restore_decks)
    mw.form.menuTools.addAction(restore)
    mw._slovo_restore_action = restore
    ensure_study()
    if sys.platform == 'darwin':
        dock = mw._slovo_study_dock
        try:
            dock.menu_timer = MenuTimer(dock)
            visible = dock.preferences.get('menu_timer_visible', True)
            dock.menu_timer.set_visible(visible)
            menu_timer = QAction('Таймер в строке меню macOS', mw)
            menu_timer.setCheckable(True)
            menu_timer.setChecked(visible)
            def toggle(checked):
                dock.menu_timer.set_visible(checked)
                dock.preferences['menu_timer_visible'] = checked
                bridge.save_preferences(PREFS, dock.preferences)
            menu_timer.toggled.connect(toggle)
            mw.form.menuTools.addAction(menu_timer)
            mw._slovo_menu_timer_action = menu_timer
        except Exception as error:
            dock.message.setText('Таймер в строке меню недоступен: ' + str(error))



gui_hooks.profile_did_open.append(loaded)


def unloaded():
    dock = getattr(mw, '_slovo_study_dock', None)
    if dock:
        dock.closed = True
        dock.poller.stop()
        if getattr(dock, 'menu_timer', None):
            dock.menu_timer.close()
        mw.removeDockWidget(dock)
        dock.hide()
        dock.deleteLater()
        mw._slovo_study_dock = None
    action = getattr(mw, '_slovo_study_action', None)
    if action:
        mw.form.menuTools.removeAction(action)
        action.deleteLater()
        mw._slovo_study_action = None
    for name in ('_slovo_archive_action', '_slovo_restore_action', '_slovo_menu_timer_action'):
        extra = getattr(mw, name, None)
        if extra:
            mw.form.menuTools.removeAction(extra)
            extra.deleteLater()
            setattr(mw, name, None)


gui_hooks.profile_will_close.append(unloaded)


HOME_SCRIPT = r'''
(() => {
 const host = document.querySelector('.sidebar-expanded-content');
 if (!host) return;
 if (!document.getElementById('slovo-daylight-style')) {
  const style=document.createElement('style');style.id='slovo-daylight-style';
  style.textContent=`
   html body .container.modern-main-menu::before {
    background-image:var(--slovo-wallpaper) !important;
   }
   #onigiri-heatmap-container .heatmap-day-cell.today {
    outline:1px solid #e26666 !important;outline-offset:1px;border-radius:3px;
   }
   #onigiri-heatmap-container .heatmap-day-cell.today .day-shape {animation:none !important;}
  `;
  document.head.appendChild(style);
  const updateDaylight=()=>{
   const hour=new Date().getHours();
   const scene=hour>=6&&hour<10?'dawn':hour>=10&&hour<17?'day':hour>=17&&hour<21?'evening':'night';
   if(document.documentElement.dataset.slovoScene===scene)return;
   const url='/_addons/ADDON_PACKAGE/wallpaper-'+scene+'.png';
   const preload=new Image();preload.onload=()=>{
    document.documentElement.style.setProperty('--slovo-wallpaper','url("'+url+'")');
    document.documentElement.dataset.slovoScene=scene;
   };preload.src=url;
  };
  updateDaylight();
  const clock=setInterval(()=>{if(!style.isConnected){clearInterval(clock);return;}updateDaylight();},30000);
  document.addEventListener('visibilitychange',updateDaylight);
 }
 let el = document.getElementById('slovo-home-focus');
 if (!el) {
  el = document.createElement('section'); el.id = 'slovo-home-focus';
  el.innerHTML = `<style>
   #slovo-home-focus {order:1000;margin:22px 10px 18px;margin-top:auto;padding:20px;border:1px solid #59717a70;border-radius:20px;
    background:linear-gradient(135deg,#183747e8,#102833eb);color:#dce9eb;box-shadow:0 12px 36px #06192322;text-align:left;box-sizing:border-box}
   #slovo-home-focus .sf-top {display:flex;justify-content:space-between;color:#b9cdd0;font-size:10px;letter-spacing:1.5px}
   #slovo-home-focus .sf-time {font-size:52px;font-weight:600;letter-spacing:-2px;color:#efd09b;line-height:1.3;font-variant-numeric:tabular-nums}
   #slovo-home-focus .sf-task {font-size:12px;line-height:1.5;overflow-wrap:anywhere;margin:2px 0 12px}
   #slovo-home-focus .sf-track {height:4px;background:#35505b;border-radius:4px;overflow:hidden;margin:12px 0}
   #slovo-home-focus .sf-fill {height:100%;background:#d9ba82;width:0%;transition:width .4s}
   #slovo-home-focus .sf-sub {font-size:10px;color:#9ab5be;line-height:1.5}
   #slovo-home-focus button {box-sizing:border-box;border:0;border-radius:10px;background:#e3bd7e;color:#132a35;padding:10px 12px;width:100%;font:inherit;font-size:12px;font-weight:600;margin-top:14px;cursor:pointer}
   #slovo-archive-link {order:999;margin:10px 20px;font-size:11px;color:inherit;opacity:.65;cursor:pointer;background:transparent;border:0;text-align:left}
  </style><div class="sf-top"><span class="sf-phase">ФОКУС</span><span class="sf-count">0 / 5</span></div>
  <div class="sf-time">80:00</div><div class="sf-task">Выбери задачи для блока</div>
  <div class="sf-track"><div class="sf-fill"></div></div>
  <div class="sf-sub">80 мин учёбы · отдых 10 / 30 мин</div>
  <button type="button">Настроить блок →</button>`;
  el.querySelector('button').onclick = () => pycmd('slovo:open');
  host.appendChild(el);
  const timer = setInterval(() => {
   if (!el.isConnected) {clearInterval(timer);return;}
   const s = el._snapshot; if (!s) return;
   const sec = Math.max(0,Math.ceil(s.remaining-(s.active?(performance.now()-s.at)/1000:0)));
   el.querySelector('.sf-time').textContent = String(Math.floor(sec/60)).padStart(2,'0')+':'+String(sec%60).padStart(2,'0');
  },250);
 }
 const s = PAYLOAD;
 el._snapshot = {...s,at:performance.now()};
 el.querySelector('.sf-phase').textContent = s.phase;
 el.querySelector('.sf-count').textContent = s.count+' / 5';
 el.querySelector('.sf-task').textContent = s.title;
 el.querySelector('.sf-fill').style.width = Math.min(s.count/5*100,100)+'%';
 el.querySelector('.sf-sub').textContent = s.subtitle;
 el.querySelector('button').textContent = s.active ? 'Открыть управление →' : 'Настроить блок →';
 const grid = document.querySelector('.unified-grid');
 if (grid) {
  let tasks = document.getElementById('slovo-home-tasks');
  if (!tasks) {
   tasks = document.createElement('section'); tasks.id = 'slovo-home-tasks';
   tasks.innerHTML = `<style>
    #slovo-home-tasks {min-width:0;min-height:0;box-sizing:border-box;border:1px solid #65838b80;border-radius:24px;padding:20px;
     background:linear-gradient(135deg,#153846d9,#102a37cf);box-shadow:0 10px 28px #06192320;color:#dce9eb;text-align:left;display:flex;flex-direction:column;gap:14px;overflow:hidden}
    #slovo-home-tasks .st-head {display:flex;align-items:center;justify-content:space-between;gap:10px}
    #slovo-home-tasks .st-brand {font-size:10px;letter-spacing:2px;color:#dbbe88;margin-bottom:5px}
    #slovo-home-tasks h2 {font-size:17px;font-weight:600;margin:0;color:#e2ecee}
    #slovo-home-tasks .st-refresh {flex:none;background:#284b5866;color:#b8d0d4;border:1px solid #55768166;border-radius:10px;width:32px;height:32px;font-size:18px;cursor:pointer;padding:0}
    #slovo-home-tasks .st-list {display:flex;flex-direction:column;gap:6px;overflow:auto;min-height:0;flex:1;scrollbar-width:thin}
    #slovo-home-tasks .st-task {display:flex;align-items:center;gap:12px;width:100%;box-sizing:border-box;background:#17384599;border:1px solid #49687470;border-radius:12px;padding:8px 10px;color:#d9e7e9;text-align:left;cursor:pointer;font:inherit;font-size:12px;line-height:1.45;flex-shrink:0}
    #slovo-home-tasks .st-task:hover {background:#2b505ed9;border-color:#c9b17c}
    #slovo-home-tasks .st-task:disabled {cursor:default;opacity:.65}
    #slovo-home-tasks .st-num {color:#dfc38f;font-size:13px;flex:none;border:1px solid #67828c;border-radius:4px;width:17px;height:17px;display:grid;place-items:center}
    #slovo-home-tasks .st-task[aria-checked="true"] {border-color:#d2b67d;background:#294c57bb}
    #slovo-home-tasks .st-title {overflow-wrap:anywhere;min-width:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;flex:1}
    #slovo-home-tasks .st-arrow {color:#8eaeb8;flex:none}
    #slovo-home-tasks .st-note {font-size:11px;color:#9bb9c2;line-height:1.5}
    #slovo-home-tasks .st-empty {font-size:13px;color:#aec4cb;line-height:1.6;padding:8px 0}
   </style><div class="st-head"><div><div class="st-brand">СЛОВО · ПЛАН НА ДЕНЬ</div><h2>Задачи на сегодня</h2></div>
   <button class="st-refresh" type="button" aria-label="Обновить задачи" title="Обновить задачи">↻</button></div>
   <div class="st-list"></div><div class="st-head"><div class="st-note"></div><button class="st-refresh st-setup" type="button" aria-label="Настроить выбранные задачи" title="Настроить блок">→</button></div>`;
   tasks.querySelector('.st-refresh').onclick = () => pycmd('slovo:refresh');
   tasks.querySelector('.st-setup').onclick = () => pycmd('slovo:open');
   grid.appendChild(tasks);
  }
  // Fill the two free columns below Activity, immediately left of Stats.
  const stats = grid.querySelector('.learner-stats-widget[data-widget-id="deck_stats"]')?.closest('.onigiri-widget-container');
  tasks.style.gridArea = stats && Number(stats.style.gridColumnStart) > 1
   ? `${stats.style.gridRowStart} / 1 / span ${stats.style.gridRowEnd.replace('span ', '') || 2} / span ${Number(stats.style.gridColumnStart)-1}`
   : '5 / 1 / span 2 / span 2';
  const key = JSON.stringify([s.tasks,s.active,s.connected,s.planBusy,s.selected]);
  if (tasks._key !== key) {
   tasks._key = key;
   const list = tasks.querySelector('.st-list'); list.replaceChildren();
   s.tasks.forEach((task,i) => {
    const button = document.createElement('button'); button.type='button';button.className='st-task';button.disabled=s.active;button.title=task.title;
    button.setAttribute('role','checkbox');button.setAttribute('aria-checked',String(task.selected));
    const num=document.createElement('span');num.className='st-num';num.textContent=task.selected?'✓':'';
    const title=document.createElement('span');title.className='st-title';title.textContent=task.title;
    const arrow=document.createElement('span');arrow.className='st-arrow';arrow.textContent='';
    button.append(num,title,arrow);button.onclick=()=>pycmd('slovo:task:'+i);list.appendChild(button);
   });
   if (!s.tasks.length) {
    const empty=document.createElement('div');empty.className='st-empty';
    empty.textContent=s.planBusy?'Загружаем план из Слова…':s.connected?'В плане пока нет задач. Добавь их в Слове и обнови список.':'Подключи Слово в настройках блока, чтобы видеть свой план здесь.';
    list.appendChild(empty);
   }
   tasks.querySelector('.st-refresh').disabled=s.active||s.planBusy;
   tasks.querySelector('.st-note').textContent=s.active?'Блок идёт · задачи зафиксированы':s.tasks.length?(s.selected?'Выбрано: '+s.selected+' · настрой блок →':'Отметь одну или несколько задач'):'План обновляется автоматически';
  }
 }
 // Hide only the explicitly archived root. Newly created decks stay visible.
 const archive = s.archive;
 if(archive) {
  document.querySelectorAll('tr[data-did="'+archive+'"], tr[id="'+archive+'"]').forEach(row=>row.style.display='none');
  if(!document.getElementById('slovo-archive-link')) {
   const link=document.createElement('button');link.id='slovo-archive-link';
   link.textContent='Архив старых колод ↗';link.onclick=()=>pycmd('slovo:archive');
   el.before(link);
  }
 }
})();
'''


def update_home_widget(*_):
    if not mw.col or mw.state != 'deckBrowser':
        return
    dock = getattr(mw, '_slovo_study_dock', None)
    state = dock.state if dock else None
    session = (state or {}).get('session')
    phase = (session or {}).get('phase', 'idle')
    committed = (session or {}).get('task', {})
    committed_items = committed.get('items', [committed] if committed else [])
    chosen = {item.get('occurrence_id') for item in committed_items} if session else (dock.selected_ids if dock else set())
    tasks = dock.tasks if dock and dock.tasks else committed_items
    payload = {
        'phase': {'idle': 'ГОТОВ К СТАРТУ', 'focus': 'ВРЕМЯ ФОКУСА', 'break': 'ВРЕМЯ ОТДЫХА'}[phase],
        'remaining': bridge.remaining_seconds(state), 'active': bool(session),
        'count': (state or {}).get('completed_today', 0),
        'title': committed.get('title', f'Выбрано задач: {len(chosen)}' if chosen else 'Выбери задачи для блока'),
        'subtitle': ('80 мин учёбы · отдых 10 / 30 мин' if state else 'Проверяем системный таймер…'),
        'archive': str((mw.col.get_config('slovo_archive') or {}).get('deck_id', '')),
        'tasks': [{'title': task['title'], 'selected': task.get('occurrence_id') in chosen} for task in tasks],
        'selected': len(chosen),
        'connected': bool(dock and dock.preferences.get('token')),
        'planBusy': bool(dock and dock.plan_busy),
    }
    mw.deckBrowser.web.eval(HOME_SCRIPT.replace('ADDON_PACKAGE', mw.addonManager.addonFromModule(__name__)).replace('PAYLOAD', json.dumps(payload, ensure_ascii=False)))


def home_message(handled, message, context):
    if handled[0] or context is not mw.deckBrowser:
        return handled
    if message == 'slovo:open':
        show_study()
        return True, None
    if message == 'slovo:refresh':
        dock = ensure_study()
        if not (dock.state or {}).get('session'):
            dock.load_plan()
        return True, None
    if message.startswith('slovo:task:'):
        dock = ensure_study()
        index = message.removeprefix('slovo:task:')
        if index.isdecimal() and 0 <= int(index) < len(dock.tasks) and not (dock.state or {}).get('session'):
            dock.toggle_task(int(index))
        return True, None
    if message == 'slovo:archive':
        from aqt import dialogs
        browser = dialogs.open('Browser', mw)
        name = (mw.col.get_config('slovo_archive') or {}).get('name', '')
        browser.form.searchEdit.lineEdit().setText('deck:' + json.dumps(name, ensure_ascii=False))
        browser.onSearchActivated()
        return True, None
    return handled


def archive_decks():
    """Explicit, reversible archive. Keep cards, deck IDs and review history."""
    from aqt.operations import QueryOp
    from aqt.utils import showInfo
    if mw.col.get_config('slovo_archive'):
        showInfo('Архив уже создан. Старые карточки доступны через Обзор и меню «Вернуть колоды из архива».')
        return
    if mw.state == 'review':
        mw.moveToState('deckBrowser')
    def work(col):
        return bridge.archive_collection(col, PREFS.parent / 'backups')
    def done(count):
        mw.reset()
        showInfo(f'Старые колоды скрыты, {count} карточек исключены из повторений.\nКарточки и история сохранены. Восстановление: Инструменты → Вернуть колоды из архива.')
    QueryOp(parent=mw, op=work, success=done).with_progress('Архивируем колоды…').run_in_background()


def restore_decks():
    from aqt.operations import QueryOp
    from aqt.utils import showInfo
    archive = mw.col.get_config('slovo_archive')
    if not archive:
        showInfo('Архивированных колод нет.')
        return
    def work(col):
        bridge.restore_collection(col)
    QueryOp(parent=mw, op=work, success=lambda _: mw.reset()).with_progress('Возвращаем колоды…').run_in_background()


gui_hooks.deck_browser_did_render.append(update_home_widget)
gui_hooks.webview_did_receive_js_message.append(home_message)
