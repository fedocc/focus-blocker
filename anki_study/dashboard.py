"""Study dashboard with cached review health and one-time review defaults."""
import json
import time
import sys
import math
from pathlib import Path

from aqt import mw, gui_hooks
from aqt.qt import (QAction, QComboBox, QDesktopServices, QDockWidget, QFileDialog, QDialog, QFrame,
                    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                    QListWidgetItem, QMessageBox, QPushButton, QTimer, Qt, QUrl, QAbstractItemView,
                    QVBoxLayout, QWidget, QScrollArea, QToolButton, QSizePolicy, QSize, QRect, QApplication, QCheckBox, QSpinBox,
                    QPainter, QColor, QPen, QFont, QRectF)
from . import bridge, analytics, activity_detail, review_health

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
QLabel#timer { color: #f0d29e; font-size: 54px; font-weight: 600; letter-spacing: -2px; }
QLabel#taskHeading { color: #c9dbdf; font-size: 13px; }
QLabel#notice { color: #c4d8da; background: #19323e; border: 1px solid #2a4b57; border-radius: 10px; padding: 10px; font-size: 11px; }
QFrame#focusHero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1c3d4d,stop:1 #112832); border: 1px solid #385661; border-radius: 20px; }
QFrame#focusCard { background: #132b37; border: 1px solid #25424e; border-radius: 14px; }
QWidget#focusRoot QLineEdit, QDialog#focusPicker QLineEdit, QWidget#focusRoot QComboBox, QDialog#focusPicker QComboBox {
    background: #0e2430; color: #e2ebef; border: 1px solid #304c58; border-radius: 9px; padding: 10px; min-height: 18px; selection-background-color: #477275;
}
QWidget#focusRoot QLineEdit:focus, QDialog#focusPicker QLineEdit:focus { border-color: #b6a17b; }
QWidget#focusRoot QSpinBox { background:#0e2430; color:#e2ebef; border:1px solid #304c58; border-radius:7px; padding:6px 8px; min-height:20px; }
QWidget#focusRoot QSpinBox:disabled { color:#718a95; border-color:#213b47; }
QWidget#focusRoot QComboBox::drop-down, QDialog#focusPicker QComboBox::drop-down { border: none; width: 24px; }
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
QWidget#focusRoot QListWidget#focusTasks::item { padding:6px 4px; }
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


class Disclosure(QFrame):
    """Keep occasional controls available without occupying the daily view."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet('QToolButton {color:#b8cdd4;background:transparent;border:0;padding:5px 0;text-align:left;}')
        self.toggle.setAccessibleName(title)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(9)
        layout.addWidget(self.body)
        self.body.hide()
        def expand(checked):
            self.body.setVisible(checked)
            self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(expand)


class Notice(QLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class MacNative:
    """Typed Objective-C calls on Qt's main thread; no cross-call ABI mutation."""
    def __init__(self):
        import ctypes as c
        self.c = c
        self.objc = c.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes = [c.c_char_p]
        self.objc.objc_getClass.restype = c.c_void_p
        self.objc.sel_registerName.argtypes = [c.c_char_p]
        self.objc.sel_registerName.restype = c.c_void_p
        self.address = c.cast(self.objc.objc_msgSend, c.c_void_p).value

    def send(self, receiver, selector, args=(), types=(), result='pointer'):
        c = self.c
        output = c.c_void_p if result == 'pointer' else result
        call = c.CFUNCTYPE(output, c.c_void_p, c.c_void_p, *types)(self.address)
        return call(receiver, self.objc.sel_registerName(selector.encode()), *args)


class MacRestOverlay(MacNative):
    """Keep independent NSPanel covers in every desktop/fullscreen app's Space."""
    def __init__(self):
        super().__init__()
        import platform
        # NSWindow.h: CanJoinAllSpaces, Stationary, IgnoresCycle,
        # FullScreenAuxiliary; no conflicting MoveToActiveSpace/Primary flags.
        self.behavior = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
        if int(platform.mac_ver()[0].split('.')[0] or 0) >= 13:
            self.behavior |= 1 << 18  # CanJoinAllApplications (Stage Manager).
        self.saved_policy = None
        self.saved_presentation = None

    def application(self):
        return self.send(self.objc.objc_getClass(b'NSApplication'), 'sharedApplication')

    def begin(self):
        app, c = self.application(), self.c
        # A regular foreground app cannot overlay another app's fullscreen
        # Space, even with CanJoinAllSpaces + FullScreenAuxiliary. Temporarily
        # become an accessory; restore the exact original policy on every exit.
        # Apple DTS (macOS 26): developer.apple.com/forums/thread/826308
        if self.saved_policy is None:
            self.saved_policy = self.send(app, 'activationPolicy', result=c.c_long)
            self.saved_presentation = self.send(app, 'presentationOptions', result=c.c_ulong)
        if self.send(app, 'activationPolicy', result=c.c_long) != 1:
            if not self.send(app, 'setActivationPolicy:', [1], [c.c_long], c.c_bool):
                raise RuntimeError('macOS не разрешила режим заставки поверх Spaces.')
        # HideDock, AutoHideMenuBar, DisableProcessSwitching, DisableHideApplication.
        # Keep Force Quit / logout / system authorization available. These are
        # process-local presentation options, never persistent system defaults.
        options = (1 << 1) | (1 << 2) | (1 << 5) | (1 << 8)
        options |= self.saved_presentation & (1 << 10)  # Existing fullscreen.
        if self.send(app, 'presentationOptions', result=c.c_ulong) != options:
            self.send(app, 'setPresentationOptions:', [options], [c.c_ulong], None)

    def end(self):
        if self.saved_policy is None:
            return
        app, c = self.application(), self.c
        self.send(app, 'setPresentationOptions:', [self.saved_presentation], [c.c_ulong], None)
        if not self.send(app, 'setActivationPolicy:', [self.saved_policy], [c.c_long], c.c_bool):
            raise RuntimeError('macOS не восстановила обычный режим Anki; повторяем восстановление.')
        self.saved_policy = self.saved_presentation = None

    def cover(self, widget, order=True):
        # Cocoa QWidget.winId() is an NSView*, NOT an NSWindow*.
        window = self.send(int(widget.winId()), 'window')
        if not window:
            raise RuntimeError('macOS не предоставила окно заставки.')
        c = self.c
        # Qt.Tool supplies an NSPanel but not its nonactivating style. Set it
        # before the first show, so ordering the cover doesn't move Spaces.
        style = self.send(window, 'styleMask', result=c.c_ulong)
        if not style & (1 << 7):
            self.send(window, 'setStyleMask:', [style | (1 << 7)], [c.c_ulong], None)
        self.send(window, 'setFloatingPanel:', [True], [c.c_bool], None)
        self.send(window, 'setCollectionBehavior:', [self.behavior], [c.c_ulong], None)
        self.send(window, 'setHidesOnDeactivate:', [False], [c.c_bool], None)
        self.send(window, 'setCanHide:', [False], [c.c_bool], None)
        self.send(window, 'setIgnoresMouseEvents:', [False], [c.c_bool], None)
        self.send(window, 'setAnimationBehavior:', [2], [c.c_long], None)
        # NSScreenSaverWindowLevel from the public macOS SDK. Applies only to
        # our covers; suspension/hide still works for emergency/auth dialogs.
        self.send(window, 'setLevel:', [1000], [c.c_long], None)
        if order:
            self.send(window, 'orderFrontRegardless', result=None)

    def activate(self):
        self.send(self.application(), 'activateIgnoringOtherApps:', [True], [self.c.c_bool], None)


class MenuTimer(MacNative):
    """Text-only macOS status item; display preferences never control a block."""
    def __init__(self, dock):
        super().__init__()
        c = self.c
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
            notice = self.dock.phase_notice
            if session.get('awaiting_rest'):
                extra = int(bridge.overtime_seconds(state))
                text = f'◉ ДОП. +{extra // 60:02}:{extra % 60:02}'
            if session['phase'] == 'break':
                text = f'{"‼" if notice.event and int(time.monotonic()) % 2 else "☕"} ОТДЫХ {remaining // 60:02}:{remaining % 60:02}'
            elif session['phase'] == 'ready':
                text = '✓ ОТДЫХ ЗАВЕРШЁН'
            elif session['phase'] == 'prepare':
                text = f'→ СТАРТ ЧЕРЕЗ {remaining // 60:02}:{remaining % 60:02}'
            tip = session['task']['title']
        elif not state:
            text = 'Фокус · …'
        elif self.dock.phase_notice.event:
            text = '✓ ОТДЫХ ЗАВЕРШЁН'
        self.send(self.button, 'setTitle:', [self.string(text)], [self.c.c_void_p], None)
        self.send(self.button, 'setToolTip:', [self.string(tip)], [self.c.c_void_p], None)

    def set_visible(self, visible):
        self.send(self.item, 'setVisible:', [bool(visible)], [self.c.c_bool], None)

    def close(self):
        self.timer.stop()
        self.send(self.bar, 'removeStatusItem:', [self.item], [self.c.c_void_p], None)
        self.send(self.item, 'release', result=None)


class PhaseNotice(QDialog):
    """Non-modal signal. Only its explicit rest button changes timer state."""
    def __init__(self, dock):
        super().__init__(mw, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.dock = dock
        self.gate = bridge.PhaseAlerts(dock.preferences)
        self.event = None
        self.demo = False
        self.player = None
        self.setObjectName('focusPicker')
        self.setWindowTitle('Фокус · сигнал таймера')
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(470)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        self.headline = label('', 'brand')
        self.headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.headline)
        self.clock = label('', 'timer')
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.clock)
        self.detail = label('')
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail)
        self.sound_status = label('')
        self.sound_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sound_status)
        self.silent = QCheckBox('Не повторять звук в этом блоке')
        self.silent.setStyleSheet('color:#c4d9de;')
        self.silent.toggled.connect(self.set_silent)
        layout.addWidget(self.silent)
        self.ack = QPushButton('Я заметил — иду отдыхать')
        self.ack.setDefault(False)
        self.ack.setAutoDefault(False)
        self.ack.clicked.connect(self.acknowledge)
        layout.addWidget(self.ack)
        self.keep_studying = QPushButton('Продолжить учёбу')
        self.keep_studying.setAutoDefault(False)
        self.keep_studying.clicked.connect(self.hide)
        layout.addWidget(self.keep_studying)
        self.repeat = QTimer(self)
        self.repeat.setInterval(20000)
        self.repeat.timeout.connect(self.ring)
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(500)
        self.clock_timer.timeout.connect(self.update_clock)

    def save(self):
        if self.gate.dirty:
            bridge.save_preferences(PREFS, self.dock.preferences)
            self.gate.dirty = False

    def observe(self, state):
        if self.demo:
            return
        event = self.gate.observe(state)
        self.save()
        if event is None:
            self.dismiss()
        elif not self.event or (event['id'], event['kind']) != (self.event['id'], self.event['kind']):
            self.present(event)

    def present(self, event, demo=False):
        self.dismiss()
        self.event = event
        self.demo = demo
        rest = event['kind'] == 'break'
        overtime = event['kind'] == 'overtime' and not demo
        minutes = ((self.dock.state or {}).get('session') or {}).get('focus_seconds', 4800) // 60
        self.headline.setText('ПРОВЕРКА СИГНАЛА' if demo else f'{minutes} МИНУТ ВЫПОЛНЕНЫ' if overtime else 'БЛОК ЗАВЕРШЁН · ПОРА ОТДОХНУТЬ' if rest else 'ПЕРЕРЫВ ЗАВЕРШЁН')
        self.detail.setText('Это проверка. Учебный таймер не меняется.' if demo else
                            'Дополнительное время прибавляется к этому блоку.\nПолный перерыв начнётся по кнопке «Начать отдых».' if overtime else
                            'Встань, пройдись, отдохни от экрана.\nПерерыв уже идёт; подтверждение не меняет таймер.' if rest else
                            'Можно вернуться к учёбе.\nСледующий блок запускается вручную.')
        self.ack.setText('Сигнал замечен' if demo else 'Начать отдых' if overtime else 'Я заметил — иду отдыхать' if rest else 'Я заметил')
        self.ack.setEnabled(True)
        self.silent.setVisible(overtime)
        self.keep_studying.setVisible(overtime)
        self.silent.blockSignals(True)
        self.silent.setChecked(overtime and self.dock.preferences.get('alert_silent_overtime') == event['id'])
        self.silent.blockSignals(False)
        self.sound_status.setText('Звук повторяется каждые 20 секунд до подтверждения.\nЕсли его не слышно, проверь громкость и выбранный аудиовыход.')
        self.update_clock()
        self.show()
        self.clock_timer.start()
        if not self.silent.isChecked():
            self.repeat.start()
            self.ring()
        else:
            self.sound_status.setText('Повторный звук выключен для этого блока.\nОтдых пока не начат.')
        QTimer.singleShot(0, self.bring_forward)

    def bring_forward(self):
        if not self.event:
            return
        self.raise_()
        self.activateWindow()
        QApplication.alert(self, 0)
        native = getattr(self.dock, 'menu_timer', None)
        if native:
            try:
                app = native.send(native.objc.objc_getClass(b'NSApplication'), 'sharedApplication')
                native.send(app, 'activateIgnoringOtherApps:', [True], [native.c.c_bool], None)
                native.tick()
            except Exception:
                pass  # The Qt window and audio remain independent fallbacks.

    def update_clock(self):
        if not self.event:
            return
        seconds = 600 if self.demo else math.ceil(bridge.remaining_seconds(self.dock.state))
        if self.event['kind'] == 'overtime':
            extra = int(bridge.overtime_seconds(self.dock.state))
            self.clock.setText(f'+{extra // 60:02}:{extra % 60:02}')
        else:
            self.clock.setText(f'{seconds // 60:02}:{seconds % 60:02}' if self.event['kind'] == 'break' else 'ГОТОВО')

    def set_silent(self, checked):
        if not self.event or self.event['kind'] != 'overtime' or self.demo:
            return
        self.dock.preferences['alert_silent_overtime'] = self.event['id'] if checked else None
        bridge.save_preferences(PREFS, self.dock.preferences)
        if checked:
            self.repeat.stop()
            if self.player and self.player.poll() is None:
                self.player.terminate()
            self.sound_status.setText('Повторный звук выключен для этого блока.\nОтдых пока не начат.')
        else:
            self.repeat.start()
            self.sound_status.setText('Звук повторяется каждые 20 секунд.\nОтдых начнётся только по кнопке.')

    def ring(self):
        if not self.event:
            return
        try:
            if self.player and self.player.poll() is None:
                self.player.terminate()
            self.player = bridge.play_alert_sound(self.event['kind'])
            if self.player is None:
                QApplication.beep()
        except OSError:
            QApplication.beep()

    def acknowledge(self):
        if not self.demo and self.event and self.event['kind'] == 'overtime':
            self.dock.begin_rest()
            return
        if not self.demo:
            self.gate.acknowledge()
            self.save()
        self.dismiss()

    def dismiss(self):
        self.repeat.stop()
        self.clock_timer.stop()
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None
        self.event = None
        self.demo = False
        self.hide()

    def reject(self):
        if not self.demo and self.event and self.event['kind'] == 'overtime':
            self.hide()  # Closing/Escape must never count as consent to rest.
        else:
            self.acknowledge()

    def closeEvent(self, event):
        self.reject()
        event.accept()


class TaskList(QListWidget):
    """Keep trackpad scrolling inside the list and show complete task titles."""
    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()  # At either boundary Qt must not hand this gesture to the dock.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        width = max(80, self.viewport().width() - 46)
        metrics = self.fontMetrics()
        for index in range(self.count()):
            item = self.item(index)
            text = metrics.boundingRect(QRect(0, 0, width, 10000),
                                        Qt.TextFlag.TextWordWrap, item.text())
            item.setSizeHint(QSize(0, max(32, text.height() + 16)))


class TimelineBar(QWidget):
    COLORS = {'focus':'#df747b', 'rest':'#64a6e4', 'prepare':'#d6b77c'}

    def __init__(self, select, parent):
        super().__init__(parent)
        self.select = select
        self.nodes, self.hits, self.selected = [], [], None
        self.setFixedHeight(82)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Шкала серии: бот, отдых и переходы')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont('SF Pro Text', 9))
        available = max(1, self.width() - 16)
        total = max(1, sum(n['minutes'] for n in self.nodes))
        count = max(1,sum(bool(n['minutes']) for n in self.nodes))
        minimum = min(22,available/count*.7)
        flexible = max(0,available-minimum*count)
        x, self.hits = 8, []
        for node in self.nodes:
            if node['minutes']:
                # A schematic, rather than a time axis: keep short rests and
                # their manual gates readable alongside long study blocks.
                width = minimum + flexible * node['minutes'] / total
                rect = QRectF(x + .6, 24, max(.8, width - 1.2), 26)
                color = QColor(self.COLORS[node['kind']])
                painter.setPen(Qt.PenStyle.NoPen)
                if node['kind']=='rest' and not node.get('protected'):
                    color.setAlpha(85)
                    painter.setPen(QPen(QColor(self.COLORS['rest']),1,Qt.PenStyle.DashLine))
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 3, 3)
                if width >= 16:
                    painter.setPen(QColor('#d2e5f1' if node['kind']=='rest' and not node.get('protected') else '#132631'))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(round(node['minutes'])))
                if node['kind'] == 'focus' and width >= 18:
                    painter.setPen(QColor('#9cb2bc'))
                    painter.drawText(QRectF(x, 2, width, 18), Qt.AlignmentFlag.AlignCenter, str(node['block']))
                if node.get('active'):
                    painter.setPen(QPen(QColor('#f5e8c9'), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(-1,-2,1,2), 4, 4)
                    progress = node.get('progress', 0)
                    painter.drawLine(int(x), 55, int(x + width * progress), 55)
                if node['id'] == self.selected:
                    painter.setPen(QPen(QColor('#edf3f5'), 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(-1,-2,1,2), 4, 4)
                self.hits.append((QRectF(x,20,max(2,width),38),node))
                x += width
            else:
                painter.setPen(QPen(QColor('#91a7b0'), 1))
                painter.drawLine(int(x),51,int(x),63)
                painter.setBrush(QColor('#d6b77c') if node['kind']=='automatic' else QColor('#132b37'))
                painter.setPen(QPen(QColor('#f5e8c9') if node.get('active') or node['id']==self.selected else QColor('#91a7b0'),2))
                if node['kind']=='automatic':
                    painter.save();painter.translate(x,69);painter.rotate(45)
                    painter.drawRect(QRectF(-2.5,-2.5,5,5));painter.restore()
                else:
                    painter.drawEllipse(QRectF(x-3.5,65.5,7,7))
                self.hits.append((QRectF(x-6,59,12,22),node))
        painter.end()

    def mouseMoveEvent(self, event):
        node = next((n for rect,n in reversed(self.hits) if rect.contains(event.position())),None)
        self.setToolTip(node['title'] + '\n' + node['detail'] if node else 'Нажми на участок или переход, чтобы посмотреть правила.')

    def mousePressEvent(self, event):
        if event.button()==Qt.MouseButton.LeftButton:
            node=next((n for rect,n in reversed(self.hits) if rect.contains(event.position())),None)
            if node:self.select(node['id'])

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right) and self.nodes:
            index=next((i for i,n in enumerate(self.nodes) if n['id']==self.selected),0)
            self.select(self.nodes[(index+(1 if event.key()==Qt.Key.Key_Right else -1))%len(self.nodes)]['id'])
            event.accept()
        else:
            super().keyPressEvent(event)


class SeriesTimeline(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName('focusTimeline')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,4,0,0);layout.setSpacing(4)
        self.setToolTip('Нажми на участок, чтобы увидеть длительность и правила перехода.')
        self.total=label('');layout.addWidget(self.total)
        self.bar=TimelineBar(self.choose,self);layout.addWidget(self.bar)
        legend=QLabel('<span style="color:#df747b">■ Бот</span> &nbsp; <span style="color:#64a6e4">■ Отдых</span> &nbsp; <span style="color:#d6b77c">■ Выбор</span><br>○ По кнопке, без лимита &nbsp; ◆ Автоматически')
        legend.setStyleSheet('font-size:10px;color:#98b0bc;');legend.setWordWrap(True);layout.addWidget(legend)
        self.title=label('', 'taskHeading');layout.addWidget(self.title);self.title.hide()
        self.detail=label('');layout.addWidget(self.detail);self.detail.hide();self.details_requested=False
        self.policy=label('');self.policy.setParent(self);self.policy.hide()
        self.model=None

    def set_plan(self, model):
        previous=self.model or {};self.model=model
        ids={n['id'] for n in model['nodes']}
        if self.bar.selected not in ids or model['active_id']!=previous.get('active_id'):
            self.bar.selected=model['active_id'] or 'focus:0'
        self.bar.nodes=model['nodes']
        minutes=round(model['timer_minutes'])
        self.total.setText(f'Учёба {model["focus_minutes"]} мин · всего {minutes//60} ч {minutes%60:02} мин')
        self.total.setToolTip('План без дополнительного бота и ожиданий по кнопке. Подготовку можно закончить раньше. Ширина участков условная, длительности подписаны в минутах.')
        self.policy.setText(('Отдых защищён чёрным экраном.' if model['blackout'] else 'Пунктирный отдых — без чёрного экрана.') + '\nПосле серии — свободное время.')
        self.bar.setAccessibleDescription(' → '.join(n['title'] for n in model['nodes']))
        self.bar.setToolTip(self.policy.text())
        self.choose(self.bar.selected, reveal=False)

    def choose(self, identity, reveal=True):
        if not self.model:return
        node=next((n for n in self.model['nodes'] if n['id']==identity),None)
        if not node:return
        self.details_requested = self.details_requested or reveal
        self.title.setVisible(self.details_requested)
        self.detail.setVisible(self.details_requested)
        self.bar.selected=identity
        self.title.setText(('Сейчас: ' if node.get('active') else '') + node['title'])
        self.detail.setText(node['detail'])
        self.bar.update()


class RestWindow(QDialog):
    """A black, non-destructive cover. The service owns rest and app enforcement."""
    REST_HINT = 'Встань из-за стола. Пройдись или разомнись.'
    def __init__(self, dock, screen):
        # A transient child of Anki follows its parent's Space. Each cover must
        # instead be an independent panel, owned and disposed by RestScreen.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.dock = dock
        self.setWindowTitle('Отдых · Фокус')
        self.setStyleSheet('QDialog {background:#050608;color:#c9d4d6;} QLabel {color:#c9d4d6;background:transparent;} QPushButton {padding:14px;background:#15282c;color:#dfc18c;border:1px solid #354b4e;border-radius:10px;}')
        self.setGeometry(screen.geometry())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addStretch()
        self.heading = QLabel('ОТДЫХ')
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.heading)
        self.clock = QLabel('05:00')
        self.clock.setStyleSheet('font-size:72px;color:#9cb3b7;')
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.clock)
        self.hint = QLabel(self.REST_HINT)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.back = QPushButton('Я вернулся · выбрать следующий блок')
        self.back.clicked.connect(dock.prepare_next)
        layout.addWidget(self.back, alignment=Qt.AlignmentFlag.AlignCenter)
        self.shorten = QPushButton('Сократить до короткого отдыха')
        self.shorten.clicked.connect(dock.shorten_rest)
        layout.addWidget(self.shorten, alignment=Qt.AlignmentFlag.AlignCenter)
        emergency = QPushButton('Экстренное прерывание')
        emergency.clicked.connect(dock.request_emergency)
        layout.addWidget(emergency, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


class RestScreen:
    def __init__(self, dock):
        self.dock = dock
        self.windows = []
        self.suspended = False
        self.last_ready = None
        self.native = None
        self.native_error = None
        # Offscreen/minimal Qt winIds are not Cocoa pointers, even on macOS.
        if sys.platform == 'darwin' and QApplication.platformName() == 'cocoa':
            try:
                self.native = MacRestOverlay()
            except Exception as error:
                self.native_error = str(error)

    def cover(self, window, order=True):
        if self.native:
            try:
                self.native.cover(window, order=order)
            except Exception as error:
                self.native_error = str(error)

    def native_mode(self, active):
        if self.native:
            try:
                self.native_error = None
                self.native.begin() if active else self.native.end()
            except Exception as error:
                self.native_error = str(error)

    def update(self):
        session = ((self.dock.state or {}).get('session') or {})
        active = session.get('blackout') and session.get('phase') in ('break', 'ready') and not self.suspended
        if not active:
            for window in self.windows:
                window.hide()
            self.native_mode(False)
            if self.native_error and hasattr(self.dock, 'message'):
                self.dock.message.setText(self.native_error)
            return
        self.native_mode(True)
        screens = QApplication.screens()
        if len(self.windows) != len(screens):
            for window in self.windows:
                window.hide()
                window.deleteLater()
            self.windows = [RestWindow(self.dock, screen) for screen in screens]
        ready = session['phase'] == 'ready'
        seconds = math.ceil(bridge.remaining_seconds(self.dock.state))
        for window, screen in zip(self.windows, screens):
            window.setGeometry(screen.geometry())
            window.heading.setText('ПЕРЕРЫВ ЗАВЕРШЁН' if ready else 'ОТДЫХ')
            window.clock.setText('00:00' if ready else f'{seconds // 60:02}:{seconds % 60:02}')
            window.back.setVisible(ready)
            window.shorten.setVisible(self.dock.can_shorten_rest())
            window.shorten.setEnabled(not self.dock.busy)
            window.heading.setText('ПЕРЕРЫВ ЗАВЕРШЁН' if ready else 'ДЛИННЫЙ ОТДЫХ' if session.get('break_kind') == 'long' else 'ОТДЫХ')
            if not window.isVisible():
                self.cover(window, order=False)
                window.show()
                window.raise_()
                window.activateWindow()
            # Reassert after Qt maps/reorders its panel, and after switching
            # Spaces. Works independently of the optional menu-bar timer.
            self.cover(window)
            window.hint.setText('Не удалось включить защиту от переключений macOS.\n' + self.native_error if self.native_error else RestWindow.REST_HINT)
        if QApplication.applicationState() != Qt.ApplicationState.ApplicationActive and self.windows:
            # Independent of slow browser scans in the system guard. AppKit
            # activates Anki itself; no Apple Events permission to another app.
            self.windows[0].raise_()
            self.windows[0].activateWindow()
            if self.native:
                try:
                    self.native.activate()
                except Exception:
                    pass
        if ready and self.last_ready != session['id']:
            self.last_ready = session['id']
            bridge.play_alert_sound('ready')

    def dispose(self):
        for window in self.windows:
            window.hide()
            window.deleteLater()
        self.windows = []
        self.native_mode(False)


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
        self.stats_seen_at = 0
        self.usage_tracker = None
        self.foreground = None
        self.detail_reader = None
        self.detail_status = {}
        self.usage_sampling = False
        self.usage_generation = 0
        self.next_saving = False
        try:
            self.preferences = json.loads(PREFS.read_text())
        except (OSError, ValueError):
            self.preferences = {}
        self.phase_notice = PhaseNotice(self)
        self.rest_screen = RestScreen(self)
        self.next_save = QTimer(self)
        self.next_save.setSingleShot(True)
        self.next_save.setInterval(300)
        self.next_save.timeout.connect(self.save_next_selection)
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
        hero.setContentsMargins(16, 14, 16, 14)
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
        self.cycle = label('')
        self.cycle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rhythm_card, rhythm = card(layout)
        layout.removeWidget(rhythm_card)
        self.cycle.setAlignment(Qt.AlignmentFlag.AlignLeft)
        rhythm.addWidget(self.cycle)
        self.rhythm_settings = Disclosure('Изменить ритм')
        rhythm.addWidget(self.rhythm_settings)
        modes = self.rhythm_settings.content
        timing = bridge.timing_settings(self.preferences)
        self.timing_controls = {}
        for key, title, maximum in (('focus', 'Учёба', 240), ('short', 'Короткий отдых', 60),
                                     ('long', 'Длинный отдых', 180), ('every', 'Блоков до длинного', 12)):
            row = QHBoxLayout()
            row.addWidget(label(title))
            row.addStretch()
            control = QSpinBox()
            control.setRange(1, maximum)
            control.setValue(timing[key])
            control.setSuffix(' бл.' if key == 'every' else ' мин')
            control.setAccessibleName(title)
            control.setFixedWidth(110)
            row.addWidget(control)
            modes.addLayout(row)
            self.timing_controls[key] = control
        self.timing_controls['long'].setMinimum(self.timing_controls['short'].value())
        self.timing_controls['short'].valueChanged.connect(self.timing_controls['long'].setMinimum)
        self.auto_next = QCheckBox('Автопродолжение серии')
        self.auto_next.setStyleSheet('color:#c4d9de;')
        self.auto_next.setChecked(self.preferences.get('auto_next', True))
        self.behavior_settings = Disclosure('Дополнительно')
        self.behavior_settings.content.addWidget(self.auto_next)
        self.series_blocks = QSpinBox()
        self.series_blocks.setRange(1, 24)
        self.series_blocks.setValue(min(24, max(1, self.preferences.get('series_blocks', 4))))
        self.series_blocks.setPrefix('Блоков в серии: ')
        modes.addWidget(self.series_blocks)
        modes.addWidget(self.behavior_settings)
        self.blackout = QCheckBox('Чёрный экран на отдыхе')
        self.blackout.setStyleSheet('color:#c4d9de;')
        self.blackout.setChecked(self.preferences.get('rest_blackout', True))
        self.behavior_settings.content.addWidget(self.blackout)
        self.preparation_note = label('После отдыха — «Я вернулся», затем 5 минут на задачи. Без изменений продолжатся прежние. После последнего блока серия завершится.')
        self.behavior_settings.content.addWidget(self.preparation_note)
        self.timeline = SeriesTimeline()
        rhythm.addWidget(self.timeline)
        for control, signal in ([(control, control.valueChanged) for control in self.timing_controls.values()] +
                                [(self.auto_next, self.auto_next.toggled), (self.series_blocks, self.series_blocks.valueChanged), (self.blackout, self.blackout.toggled)]):
            signal.connect(self.save_mode)
        self.cycle.setText(bridge.cycle_text(self.timing_values()))
        self.timer_label.setText(f'{self.timing_values()["focus"]:02}:00')
        self.update_timeline()
        day_row = QHBoxLayout()
        day_row.addWidget(label('СЕГОДНЯ', 'eyebrow'))
        self.day_count = label('0 за день', 'sectionTitle')
        day_row.addStretch()
        day_row.addWidget(self.day_count)
        self.progress = label('');self.progress.hide()
        layout.addWidget(self.progress)
        self.task_card, task_layout = card(layout)
        task_layout.addWidget(label('Задачи', 'sectionTitle'))
        self.task_list = TaskList()
        self.task_list.setObjectName('focusTasks')
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.task_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.task_list.itemChanged.connect(self.selection_changed)
        self.task_list.hide()
        task_layout.addWidget(self.task_list)
        self.title = QLineEdit()
        self.title.setPlaceholderText('Или своя задача…')
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
        apps_title.addWidget(label('Приложения', 'sectionTitle'))
        apps_title.addStretch()
        self.app_count = label('1 выбрано')
        apps_title.addWidget(self.app_count)
        apps_layout.addLayout(apps_title)
        self.app_summary = label('Arc', 'taskHeading')
        apps_layout.addWidget(self.app_summary)
        self.auto_apps_note = label('Проверяю автоматический допуск учебных приложений…')
        apps_layout.addWidget(self.auto_apps_note)
        self.app_picker_button = QPushButton('Выбрать приложения  →')
        self.app_picker_button.clicked.connect(self.pick_apps)
        apps_layout.addWidget(self.app_picker_button)
        self.app_summary.setToolTip('Anki, Фото и системные окна доступны во время учёбы.')
        layout.addWidget(rhythm_card)
        layout.addLayout(day_row)
        self.picker = QDialog(self)
        self.picker.setObjectName('focusPicker')
        self.picker.setWindowTitle('Приложения для блока')
        self.picker.setStyleSheet(STYLE)
        self.picker.resize(420, 560)
        picker_layout = QVBoxLayout(self.picker)
        picker_layout.setContentsMargins(22, 22, 22, 22)
        picker_layout.setSpacing(14)
        picker_layout.addWidget(label('Что понадобится для учёбы?', 'sectionTitle'))
        self.picker_note = label('Выбор сохраняется для следующих блоков.')
        picker_layout.addWidget(self.picker_note)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск приложений…')
        self.search.textChanged.connect(self.filter_apps)
        picker_layout.addWidget(self.search)
        self.apps = QListWidget()
        selected = self.preferences.get('apps', ['company.thebrowser.Browser'])
        permanent = {'net.ankiweb.anki', 'com.apple.Photos', 'com.apple.finder', 'com.apple.systempreferences'}
        self.application_names = dict(bridge.applications())
        for bundle, name in self.application_names.items():
            if bundle in permanent:
                continue
            item = QListWidgetItem(name)
            item.setToolTip(name)
            item.setData(Qt.ItemDataRole.UserRole, bundle)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if bundle in selected else Qt.CheckState.Unchecked)
            self.apps.addItem(item)
        picker_layout.addWidget(self.apps)
        self.apps.setToolTip('Во время учёбы Anki, Фото и системные окна доступны. Остальные приложения скрываются без закрытия документов.')
        picker_actions = QHBoxLayout()
        cancel = QPushButton('Отмена')
        cancel.clicked.connect(self.picker.reject)
        done = QPushButton('Готово')
        done.clicked.connect(self.picker.accept)
        picker_actions.addWidget(cancel)
        picker_actions.addWidget(done)
        picker_layout.addLayout(picker_actions)
        self.sort_apps()
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
        self.title.textChanged.connect(self.queue_next_selection)
        footer_layout.addWidget(self.start)
        self.start.setToolTip('После старта длительности фиксируются. Задачи и приложения можно изменить в подготовке следующего блока.')
        self.emergency = QPushButton('Экстренно прервать…')
        self.emergency.setObjectName('emergency')
        self.emergency.clicked.connect(self.request_emergency)
        self.emergency.hide()
        footer_layout.addWidget(self.emergency)
        self.shorten = QPushButton('Сократить длинный отдых до короткого')
        self.shorten.clicked.connect(self.shorten_rest)
        self.shorten.hide()
        footer_layout.addWidget(self.shorten)
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
        self.result_actions = Disclosure('Результат')
        self.result_actions.content.addLayout(result_row)
        footer_layout.addWidget(self.result_actions)
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
            self.phase_notice.observe(value)
            self.stats_seen_at = time.monotonic()
            if self.usage_tracker:
                try:
                    self.usage_tracker.remember(value.get('history', []))
                except Exception as error:
                    self.usage_error = str(error)
            status_item = getattr(self, 'menu_timer', None)
            if status_item:
                status_item.seen_at = time.monotonic()
                status_item.tick()
            update_home_widget()
            session = value['session']
            phase = session['phase'] if session else 'idle'
            prepare_minutes = value.get('prepare_seconds', 120) // 60
            self.preparation_note.setText(f'После «Я вернулся» — {prepare_minutes} мин на выбор, затем автостарт.' if (session or {}).get('auto_continue', self.auto_next.isChecked()) else 'Один блок без автопродолжения.')
            overtime = bool(session and session.get('awaiting_rest'))
            locked = bool(session) and phase != 'prepare'
            for control in (self.title, self.task_list, self.apps, self.refresh, self.pair, self.app_picker_button):
                control.setEnabled(not locked)
            # During focus this opens a read-only list explaining which forgotten
            # tools are already admitted by the protected service.
            self.app_picker_button.setEnabled(not locked or phase == 'focus')
            self.update_auto_apps()
            for control in (*self.timing_controls.values(), self.auto_next, self.series_blocks, self.blackout):
                control.setEnabled(not session)
            self.series_blocks.setEnabled(not session and self.auto_next.isChecked())
            self.shorten.setVisible(self.can_shorten_rest())
            self.shorten.setEnabled(not self.busy)
            self.update_start_enabled()
            self.emergency.setEnabled(bool(session) and value['emergency_available'])
            self.emergency.setVisible(bool(session))
            self.phase_tag.setText('ДОПОЛНИТЕЛЬНОЕ ВРЕМЯ' if overtime else {'idle': 'ГОТОВ К СТАРТУ', 'focus': 'ВРЕМЯ ФОКУСА', 'break': 'ВРЕМЯ ОТДЫХА', 'ready': 'ПЕРЕРЫВ ЗАВЕРШЁН', 'prepare': 'ВЫБОР СЛЕДУЮЩЕГО БЛОКА'}[phase])
            self.start.setText('Начать отдых   →' if overtime else {'idle': 'Начать серию   →', 'focus': 'Идёт учёба', 'break': 'Идёт отдых', 'ready': 'Я вернулся →', 'prepare': 'Начать сейчас →'}[phase])
            self.cycle.setText(bridge.cycle_text(session or self.timing_values()))
            self.update_timeline()
            self.tick_display()
            count = value['completed_today']
            total = (session or {}).get('blocks_total', 0)
            self.day_count.setText(f'{count} за день' + (f' · серия {total - session.get("blocks_left", total) + 1}/{total}' if total else ''))
            self.progress.setText('Отдых начнётся по кнопке.' if overtime else
                                  f'Автостарт через {prepare_minutes} мин.' if phase == 'prepare' else '')
            self.progress.setVisible(overtime or phase == 'prepare')

            if session:
                self.heading.setText(session['task']['title'])
                committed = session['task'].get('items', [session['task']])
                if self.last_phase != phase:
                    if phase == 'prepare':
                        self.selected_ids = {t.get('occurrence_id') for t in committed if t.get('occurrence_id')}
                        if not self.selected_ids:
                            self.title.setText(session['task']['title'])
                        available = {t.get('occurrence_id') for t in self.tasks}
                        self.tasks += [t for t in committed if t.get('occurrence_id') and t['occurrence_id'] not in available]
                        self.render_tasks(self.tasks)
                        for i in range(self.apps.count()):
                            item = self.apps.item(i)
                            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in session['apps'] else Qt.CheckState.Unchecked)
                        self.sort_apps()
                        self.update_app_summary()
                    else:
                        if self.picker.isVisible():
                            self.picker.reject()
                        self.render_tasks(committed, locked=True)
            else:
                self.heading.setText('Выбери задачи для блока')
                if self.last_phase in ('focus', 'break', 'ready', 'prepare'):
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
            self.last_phase = phase
        self.run(lambda: bridge.helper('study-status'), updated)

    def tick_display(self):
        self.rest_screen.update()
        if ((self.state or {}).get('session') or {}).get('awaiting_rest'):
            extra = int(bridge.overtime_seconds(self.state))
            self.timer_label.setText(f'+{extra // 60:02}:{extra % 60:02}')
            return
        seconds = math.ceil(bridge.remaining_seconds(self.state)) if (self.state or {}).get('session') else self.timing_values()['focus'] * 60
        self.timer_label.setText(f'{seconds // 60:02}:{seconds % 60:02}')

    def can_edit(self):
        return not (self.state or {}).get('session') or self.state['session']['phase'] == 'prepare'

    def timing_values(self):
        return {key: control.value() for key, control in self.timing_controls.items()}

    def update_timeline(self):
        model = bridge.series_plan(self.timing_values(),self.series_blocks.value(),
            self.auto_next.isChecked(),self.blackout.isChecked(),self.state)
        self.timeline.set_plan(model)
        t = bridge.timing_settings(((self.state or {}).get('session')) or self.timing_values())
        self.cycle.setText(f'{t["focus"]} мин учёбы · {t["short"]} мин отдыха\n'
                           f'Длинный {t["long"]} мин после {t["every"]} бл. · серия {model["blocks"]} бл.')

    def save_mode(self, *_):
        if (self.state or {}).get('session'):
            return
        self.preferences.pop('focus_mode', None)
        self.preferences.update(timing=self.timing_values(), auto_next=self.auto_next.isChecked(),
                                series_blocks=self.series_blocks.value(), rest_blackout=self.blackout.isChecked())
        self.series_blocks.setEnabled(self.auto_next.isChecked())
        bridge.save_preferences(PREFS, self.preferences)
        self.cycle.setText(bridge.cycle_text(self.timing_values()))
        self.update_timeline()
        self.tick_display()
        update_home_widget()

    def prepare_next(self):
        session = (self.state or {}).get('session') or {}
        if self.busy or session.get('phase') != 'ready':
            return
        self.busy = True
        def done(value):
            self.busy = False
            if value:
                self.state = dict(self.state, session=value)
                self.rest_screen.update()
                show_study()
            self.poll()
        self.run(lambda: bridge.helper('study-next-ready', {'session_id': session['id']}), done)

    def queue_next_selection(self, *_):
        if ((self.state or {}).get('session') or {}).get('phase') == 'prepare':
            self.next_save.start()

    def save_next_selection(self):
        session = (self.state or {}).get('session') or {}
        if session.get('phase') != 'prepare':
            return
        if self.busy or self.next_saving:
            self.next_save.start()
            return
        try:
            task = bridge.block_task(self.selected_tasks(), self.title.text())
            if not task['title']:
                return
        except ValueError:
            return
        apps = [self.apps.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.apps.count())
                if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        self.next_saving = True
        def saved(value):
            self.next_saving = False
            if value and ((self.state or {}).get('session') or {}).get('id') == session['id'] and self.state['session']['phase'] == 'prepare':
                self.state = dict(self.state, session=value)
            self.poll()
        self.run(lambda: bridge.helper('study-next-update', {'session_id': session['id'], 'task': task, 'apps': apps}), saved)

    def selected_tasks(self):
        return [task for task in self.tasks if task['occurrence_id'] in self.selected_ids]

    def render_tasks(self, tasks, locked=False):
        position = self.task_list.verticalScrollBar().value()
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for task in tasks:
            item = QListWidgetItem(bridge.task_display_title(task))
            item.setToolTip(task['title'])
            item.setData(Qt.ItemDataRole.UserRole, task.get('occurrence_id'))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if locked or task.get('occurrence_id') in self.selected_ids else Qt.CheckState.Unchecked)
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        self.task_list.setFixedHeight(min(220, max(75, len(tasks) * 43 + 14)))
        self.task_list.reflow()
        self.task_list.verticalScrollBar().setValue(position)
        self.task_list.setVisible(bool(tasks))
        self.task_list.setEnabled(not locked)
        self.title.setVisible(not locked and not self.selected_ids)

    def selection_changed(self, *_):
        if self.busy or not self.can_edit():
            return
        self.selected_ids = {self.task_list.item(i).data(Qt.ItemDataRole.UserRole)
                             for i in range(self.task_list.count())
                             if self.task_list.item(i).checkState() == Qt.CheckState.Checked}
        self.title.setVisible(not self.selected_ids)
        self.update_start_enabled()
        update_home_widget()
        self.queue_next_selection()

    def filter_apps(self, text):
        query = ''.join(text.casefold().split())
        for index in range(self.apps.count()):
            item = self.apps.item(index)
            bundle = item.data(Qt.ItemDataRole.UserRole)
            names = item.text() + ' ' + bundle
            if bundle == 'com.microsoft.VSCode':
                names += ' VS Code vscode Code Visual Studio'
            item.setHidden(query not in ''.join(names.casefold().split()))

    def update_start_enabled(self, *_):
        phase = (((self.state or {}).get('session')) or {}).get('phase')
        if phase == 'ready':
            self.start.setEnabled(not self.busy)
            return
        if ((self.state or {}).get('session') or {}).get('awaiting_rest'):
            self.start.setEnabled(not self.busy)
            return
        self.start.setEnabled(bool(self.selected_ids or self.title.text().strip()) and not self.busy and
                              bool(self.state) and self.can_edit() and
                              self.state.get('service_running', False))

    def update_app_summary(self):
        names = [self.apps.item(i).text() for i in range(self.apps.count())
                 if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        self.app_summary.setText(' · '.join(names[:3]) + (f'  +{len(names)-3}' if len(names) > 3 else '') if names else 'Только Anki и всегда доступные приложения')
        self.app_summary.setToolTip(', '.join(names))
        self.app_count.setText(f'Выбрано: {len(names)}')

    def sort_apps(self):
        items = [self.apps.takeItem(0) for _ in range(self.apps.count())]
        for item in sorted(items, key=lambda item: (
                item.checkState() != Qt.CheckState.Checked, item.text().casefold())):
            self.apps.addItem(item)

    def update_auto_apps(self):
        automatic = (self.state or {}).get('auto_study_apps', {})
        names = [name for bundle, name in self.application_names.items() if bundle in automatic]
        self.auto_apps_note.setText(
            'Учебные приложения — автоматически.'
            if automatic else 'Автодопуск ещё не установлен. Обнови службу через install-study.command.')
        self.auto_apps_note.setToolTip('Автоматически: ' + ', '.join(names) if automatic else '')
        for index in range(self.apps.count()):
            item = self.apps.item(index)
            bundle = item.data(Qt.ItemDataRole.UserRole)
            name = self.application_names[bundle]
            item.setText(name + (' · автоматически' if bundle in automatic else ''))
            item.setToolTip(name + (' — доступно даже без галочки во время фокуса; на отдыхе закрыто.' if bundle in automatic else ' — выбери до начала блока или между блоками.'))
        self.update_app_summary()

    def pick_apps(self):
        editable = self.can_edit()
        automatic = (self.state or {}).get('auto_study_apps', {})
        self.picker_note.setText(
            ('Автоматические доступны. Остальной выбор зафиксирован.'
             if automatic else 'Блок идёт. Выбор зафиксирован; автодопуск требует обновления службы.')
            if not editable else
            ('Автоматические уже доступны. Выбери остальные.'
             if automatic else 'Выбор сохраняется для следующих блоков.'))
        previous = {self.apps.item(i).data(Qt.ItemDataRole.UserRole): self.apps.item(i).checkState()
                    for i in range(self.apps.count())}
        self.search.clear()
        self.sort_apps()
        self.apps.scrollToTop()
        if self.picker.exec() != QDialog.DialogCode.Accepted or not self.can_edit() or not editable:
            for index in range(self.apps.count()):
                item = self.apps.item(index)
                item.setCheckState(previous[item.data(Qt.ItemDataRole.UserRole)])
        else:
            self.preferences['apps'] = [self.apps.item(i).data(Qt.ItemDataRole.UserRole)
                                        for i in range(self.apps.count())
                                        if self.apps.item(i).checkState() == Qt.CheckState.Checked]
            bridge.save_preferences(PREFS, self.preferences)
        self.sort_apps()
        self.update_app_summary()
        self.queue_next_selection()

    def toggle_task(self, index):
        if self.busy or not self.can_edit() or not 0 <= index < len(self.tasks):
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
        if not self.can_edit():
            return
        self.tasks = value['tasks']
        self.plan_date = value['date']
        self.selected_ids.intersection_update(task['occurrence_id'] for task in self.tasks)
        self.render_tasks(self.tasks)
        self.pair.setText('Слово · подключено')
        self.message.setText('')  # Successful background refresh needs no banner.
        self.update_start_enabled()
        update_home_widget()

    def start_block(self):
        session = ((self.state or {}).get('session') or {})
        if session.get('phase') == 'ready':
            self.prepare_next()
            return
        if ((self.state or {}).get('session') or {}).get('awaiting_rest'):
            self.begin_rest()
            return
        if self.busy or not self.can_edit() or not (self.selected_ids or self.title.text().strip()):
            return
        apps = [self.apps.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.apps.count())
                if self.apps.item(i).checkState() == Qt.CheckState.Checked]
        selected = self.selected_tasks()
        if len(selected) > 1 and not (self.state or {}).get('supports_task_groups'):
            self.message.setText('Для нескольких задач нужно обновить системную службу: запусти install-study.command после серии.')
            return
        try:
            task = bridge.block_task(selected, self.title.text())
        except ValueError as error:
            self.message.setText(str(error))
            return
        payload = {'task': task, 'apps': apps}
        command = 'study-start'
        if session.get('phase') == 'prepare':
            command = 'study-next-start'
            payload['session_id'] = session['id']
        else:
            if not (self.state or {}).get('supports_pomodoro'):
                self.message.setText('Обнови системную службу через install-study.command: она ещё не поддерживает новые режимы, экран отдыха и автопродолжение.')
                return
            if not (self.state or {}).get('supports_custom_timing'):
                self.message.setText('Обнови системную службу через install-study.command: для своих длительностей и сокращения отдыха нужна новая версия.')
                return
            payload.update(timing=self.timing_values(), auto_continue=self.auto_next.isChecked(),
                           blocks=self.series_blocks.value(), blackout=self.blackout.isChecked())
            count = self.series_blocks.value() if self.auto_next.isChecked() else 1
            answer = QMessageBox.question(self, 'Начать серию?', bridge.cycle_text(self.timing_values()) +
                f'\nБлоков: {count}. После старта длительности и длина серии фиксируются.\n' +
                ('После отдыха — подтверждение возвращения и 5 минут выбора, затем автозапуск.\n' if self.auto_next.isChecked() else '') +
                ('Во время отдыха экран чёрный, переключение приложений возвращает в Anki.\n' if self.blackout.isChecked() else '') +
                'Экстренное прерывание: «Да», затем ПОДТВЕРЖДАЮ. Один раз в день.\nСохрани документы перед стартом.')
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.save_mode()
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
                self.message.setText('')
            self.poll()
        self.run(lambda: bridge.helper(command, payload), started)

    def begin_rest(self):
        session = (self.state or {}).get('session') or {}
        if self.busy or not session.get('awaiting_rest'):
            return
        self.busy = True
        self.start.setEnabled(False)
        self.phase_notice.ack.setEnabled(False)
        def finished(value):
            self.busy = False
            self.phase_notice.ack.setEnabled(True)
            if value is not None:
                self.state = dict(self.state, session=value)
                self.phase_notice.observe(self.state)
                self.tick_display()
                update_home_widget()
                self.message.setText('')
            else:
                self.phase_notice.detail.setText('Не удалось начать отдых.\n' + self.message.text())
            self.update_start_enabled()
            self.poll()
        self.run(lambda: bridge.helper('study-break-start', {'session_id': session['id']}), finished)

    def can_shorten_rest(self):
        state = self.state or {}
        session = state.get('session') or {}
        return bool(state.get('supports_break_shorten') and session.get('phase') == 'break' and
                    session.get('break_kind') == 'long' and not session.get('break_shortened'))

    def shorten_rest(self):
        if self.busy or not self.can_shorten_rest():
            return
        session_id = self.state['session']['id']
        self.busy = True
        self.rest_screen.update()
        def finished(value):
            self.busy = False
            if value is not None:
                self.state = dict(self.state, session=value['session'])
                self.tick_display()
                update_home_widget()
                self.message.setText('Отдых сокращён.')
            self.poll()
        self.run(lambda: bridge.helper('study-break-shorten', {'session_id': session_id}), finished)

    def request_emergency(self):
        if not (self.state or {}).get('supports_simple_emergency'):
            self.legacy_emergency()
            return
        if self.busy or not (self.state or {}).get('session'):
            return
        session = self.state['session']
        self.rest_screen.suspended = True
        self.rest_screen.update()
        answer = QMessageBox.question(self, 'Экстренное прерывание', 'Остановить всю серию и снять её ограничения?\nДоступно один раз в день, минимум через 20 часов после прошлого выхода.')
        if answer != QMessageBox.StandardButton.Yes:
            self.rest_screen.suspended = False
            self.rest_screen.update()
            return
        text, ok = QInputDialog.getText(self, 'Подтверди прерывание', 'Введи точно: ПОДТВЕРЖДАЮ')
        if not ok or text != 'ПОДТВЕРЖДАЮ':
            self.rest_screen.suspended = False
            self.rest_screen.update()
            return
        self.busy = True
        def stopped(value):
            self.busy = False
            if value:
                self.state = dict(self.state, session=None)
                self.message.setText('Серия прервана. Экстренный выход использован.')
            else:
                QMessageBox.warning(self, 'Не удалось прервать', self.message.text())
            self.rest_screen.suspended = False
            self.rest_screen.update()
            self.poll()
        def requested(value):
            if value is None:
                stopped(None)
                return
            self.run(lambda: bridge.helper('study-emergency-confirm', {'session_id': session['id'], 'confirmation': text}), stopped)
        self.run(lambda: bridge.helper('study-emergency-request', {'session_id': session['id'], 'reason': 'Экстренное прерывание по двойному подтверждению'}), requested)

    def legacy_emergency(self):
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


def choose_wallpaper():
    dock = ensure_study()
    dialog = QDialog(mw)
    dialog.setObjectName('focusPicker')
    dialog.setWindowTitle('Обои · время суток')
    dialog.setStyleSheet(STYLE)
    dialog.setMinimumWidth(370)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(14)
    layout.addWidget(label('Обои', 'sectionTitle'))
    theme = QComboBox()
    theme.setAccessibleName('Тема обоев')
    for key, name in bridge.WALLPAPER_THEMES.items():
        theme.addItem(name, key)
    current = bridge.wallpaper_settings(dock.preferences)
    theme.setCurrentIndex(theme.findData(current['theme']))
    layout.addWidget(theme)
    period = QComboBox()
    period.setAccessibleName('Время суток для обоев')
    period.addItem('Автоматически · по времени Mac', 'auto')
    for key, name in bridge.WALLPAPER_PERIODS.items():
        period.addItem(name, key)
    period.setCurrentIndex(max(0, period.findData(dock.preferences.get('wallpaper_period', 'auto'))))
    layout.addWidget(period)
    note = label('Автоматически — по времени Mac.')
    note.setWordWrap(True)
    layout.addWidget(note)
    buttons = QHBoxLayout()
    cancel = QPushButton('Отмена')
    cancel.clicked.connect(dialog.reject)
    apply = QPushButton('Применить')
    apply.clicked.connect(dialog.accept)
    buttons.addWidget(cancel)
    buttons.addWidget(apply)
    layout.addLayout(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        dock.preferences.update(wallpaper_theme=theme.currentData(), wallpaper_period=period.currentData())
        bridge.save_preferences(PREFS, dock.preferences)
        update_home_widget()


def configure_activity_details():
    dock = ensure_study()
    dialog = QDialog(mw)
    dialog.setObjectName('focusPicker')
    dialog.setWindowTitle('Детализация учёбы')
    dialog.setStyleSheet(STYLE)
    dialog.resize(520, 620)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(14)
    layout.addWidget(label('Что происходило внутри приложения', 'brand'))
    enabled = QCheckBox('Собирать подробности во время учебных блоков')
    enabled.setStyleSheet('color:#c4d9de;')
    enabled.setChecked(dock.preferences.get('track_details', True))
    layout.addWidget(enabled)
    def toggle(checked):
        dock.preferences['track_details'] = checked
        if dock.usage_tracker:
            dock.usage_tracker.context_previous = None
        bridge.save_preferences(PREFS, dock.preferences)
        update_home_widget()
    enabled.toggled.connect(toggle)
    layout.addWidget(label('Подробности хранятся на этом Mac: сайты, названия страниц и окон. Текст страниц, переписки, команды терминала и нажатия не собираются. Старые блоки задним числом не детализируются.'))
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    body.setObjectName('focusRoot')
    rows = QVBoxLayout(body)
    rows.setContentsMargins(6, 6, 12, 6)
    rows.setSpacing(12)
    area.setWidget(body)
    layout.addWidget(area)
    info = label('Доступ к браузеру разрешается отдельно. Для редакторов и документов нужен универсальный доступ Anki в macOS.')
    for bundle, name in bridge.applications():
        if bundle in activity_detail.BROWSERS:
            _, row = card(rows)
            row.addWidget(label(name, 'sectionTitle'))
            row.addWidget(label('Сайты и названия страниц' if bundle != 'com.apple.Safari' else 'Домены сайтов'))
            row.addWidget(label(dock.detail_reader.status_message(bundle, dock.detail_status.get(bundle, 'empty')) if dock.detail_reader else activity_detail.STATUS['error']))
            button = QPushButton('Проверить доступ к ' + name)
            def request_access(_=False, bundle=bundle, button=button):
                if not dock.detail_reader:
                    info.setText('Сбор подробностей недоступен. Перезапусти Anki после обновления.')
                    return
                button.setEnabled(False)
                info.setText('Открой нужный браузер и ответь на запрос macOS, если он появится.')
                def done(result):
                    button.setEnabled(True)
                    status = result[1] if result else 'error'
                    dock.detail_status[bundle] = status
                    info.setText(dock.detail_reader.status_message(bundle, status))
                    update_home_widget()
                dock.run(lambda: dock.detail_reader.browser(bundle, request_access=True), done)
            button.clicked.connect(request_access)
            row.addWidget(button)
        elif bundle in activity_detail.WINDOW_APPS:
            rows.addWidget(label(name + ' · ' + activity_detail.WINDOW_APPS[bundle][1]))
    rows.addStretch()
    layout.addWidget(info)
    automation = QPushButton('Настройки macOS · Автоматизация браузеров')
    automation.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('x-apple.systempreferences:com.apple.preference.security?Privacy_Automation')))
    layout.addWidget(automation)
    access = QPushButton('Настройки macOS · Универсальный доступ')
    access.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility')))
    layout.addWidget(access)
    done = QPushButton('Готово')
    done.clicked.connect(dialog.accept)
    layout.addWidget(done)
    dialog.exec()


class ReviewHealth:
    """Collection work is serialized and cached, never run on timer repaint."""
    def __init__(self):
        self.data = {'loading': True}
        self.pending = False
        self.closed = False
        self.last_at = 0
        self.collection = mw.col

    def refresh(self, setup=False):
        if self.closed or self.pending or mw.col is not self.collection:
            return
        if not setup and time.monotonic() - self.last_at < 60:
            return
        from aqt.operations import QueryOp
        self.pending = True
        folder = PREFS.parent / 'review-backups'
        setup_error = self.data.get('setup_error')
        def work(col):
            error = setup_error
            if setup:
                try:
                    review_health.apply_once(col, folder)
                except Exception as exc:
                    error = str(exc)
            data = review_health.snapshot(col)
            if error:
                data['setup_error'] = error
            return data
        def done(data):
            self.pending = False
            if self.closed or mw.col is not self.collection:
                return
            self.last_at = time.monotonic()
            self.data = data
            if setup:
                mw.apply_collection_options()
            update_home_widget()
        def failed(error):
            self.pending = False
            if self.closed or mw.col is not self.collection:
                return
            self.last_at = time.monotonic()
            self.data = {'loading': False, 'error': str(error)}
            update_home_widget()
        QueryOp(parent=mw, op=work, success=done).failure(failed).run_in_background()


def open_review_queue():
    if not mw.col:
        return
    from aqt import dialogs
    browser = dialogs.open('Browser', mw)
    browser.form.searchEdit.lineEdit().setText(review_health.repair_query(mw.col))
    browser.onSearchActivated()


def restore_review_settings():
    from aqt.operations import QueryOp
    from aqt.utils import showInfo
    if not mw.col:
        return
    def done(_):
        mw.apply_collection_options()
        health = getattr(mw, '_slovo_review_health', None)
        if health:
            health.last_at = 0
            health.refresh()
        showInfo('Исходные настройки повторения восстановлены. Карточки и сроки не перенесены.')
    QueryOp(parent=mw, op=lambda col: review_health.restore_settings(col, PREFS.parent / 'review-backups'),
            success=done).with_progress('Возвращаем настройки повторения…').run_in_background()


def loaded():
    mw.addonManager.setWebExports(__name__, r'(wallpaper-([a-z]+-)?(predawn|dawn|morning|day|golden|evening|night)\.png|focus-stats\.(js|css))')
    study_menu = mw.form.menuTools.addMenu('Фокус и учёба')
    mw._slovo_study_menu = study_menu
    action = QAction('Открыть фокус', mw)
    action.triggered.connect(show_study)
    study_menu.addAction(action)
    mw._slovo_study_action = action
    settings_menu = study_menu.addMenu('Настройки')
    service_menu = study_menu.addMenu('Обслуживание')
    wallpaper = QAction('Обои · время суток…', mw)
    wallpaper.triggered.connect(choose_wallpaper)
    settings_menu.addAction(wallpaper)
    mw._slovo_wallpaper_action = wallpaper
    signal = QAction('Проверить сигнал окончания блока…', mw)
    signal.triggered.connect(lambda: ensure_study().phase_notice.present({'id': 'demo', 'kind': 'break'}, demo=True))
    service_menu.addAction(signal)
    mw._slovo_signal_action = signal
    archive = QAction('Архивировать текущие колоды…', mw)
    archive.triggered.connect(archive_decks)
    service_menu.addAction(archive)
    mw._slovo_archive_action = archive
    restore = QAction('Вернуть колоды из архива…', mw)
    restore.triggered.connect(restore_decks)
    service_menu.addAction(restore)
    mw._slovo_restore_action = restore
    ensure_study()
    for text, name, callback in (
        ('Карточки на переработку…', '_slovo_repair_action', open_review_queue),
        ('Вернуть прежние настройки повторения…', '_slovo_review_restore_action', restore_review_settings),
    ):
        extra = QAction(text, mw)
        extra.triggered.connect(callback)
        if name == '_slovo_repair_action':
            study_menu.insertAction(settings_menu.menuAction(), extra)
        else:
            service_menu.addAction(extra)
        setattr(mw, name, extra)
    mw._slovo_review_health = ReviewHealth()
    mw._slovo_review_health.refresh(setup=True)
    if sys.platform == 'darwin':
        dock = mw._slovo_study_dock
        try:
            dock.usage_tracker = analytics.UsageTracker(PREFS.parent / 'focus-usage.sqlite3')
            dock.foreground = analytics.MacForeground()
            try:
                dock.detail_reader = activity_detail.MacDetails()
            except Exception:
                dock.detail_status['_error'] = 'error'
            dock.usage_timer = QTimer(dock)
            def sample_usage():
                dock.usage_generation += 1
                generation = dock.usage_generation
                if not dock.preferences.get('track_apps', True):
                    dock.usage_tracker.previous = None
                    dock.usage_tracker.context_previous = None
                    return
                try:
                    state = dict(dock.state or {})
                    if state.get('session'):
                        state['session'] = dict(state['session'], remaining=bridge.remaining_seconds(state))
                    # No app observation outside a fresh, running focus block.
                    fresh = time.monotonic() - dock.stats_seen_at < 8
                    active = fresh and (state.get('session') or {}).get('phase') == 'focus'
                    app = dock.foreground.current() if active else None
                    now, mono = time.time(), time.monotonic()
                    dock.usage_tracker.sample(state, app, now, mono, fresh=fresh, defer_detail=True)
                    if not app or not dock.detail_reader or not dock.preferences.get('track_details', True) or dock.usage_sampling:
                        dock.usage_tracker.sample_detail(state, app, now, mono, fresh=fresh)
                        return
                    bundle = app[0]
                    if bundle not in activity_detail.BROWSERS and bundle not in activity_detail.WINDOW_APPS:
                        dock.detail_status[bundle] = 'unsupported'
                        dock.usage_tracker.sample_detail(state, app, now, mono, fresh=fresh)
                        return
                    pid = dock.foreground.pid()
                    dock.usage_sampling = True
                    def observed(result):
                        dock.usage_sampling = False
                        if result is None:
                            result = (None, 'error')
                        detail, status = result
                        dock.detail_status[bundle] = status
                        # Never attribute a slow result to the next app/block/profile.
                        current = dock.state or {}
                        session = current.get('session') or {}
                        valid = (generation == dock.usage_generation and dock.preferences.get('track_apps', True) and
                                 time.monotonic() - dock.stats_seen_at < 8 and
                                 session.get('phase') == 'focus' and
                                 session.get('id') == state['session']['id'] and
                                 dock.foreground.current() == app and dock.foreground.pid() == pid)
                        if generation == dock.usage_generation:
                            dock.usage_tracker.sample_detail(state, app if valid else None, now, mono, fresh=valid,
                                                            detail=detail if dock.preferences.get('track_details', True) else None)
                    dock.run(lambda: dock.detail_reader.capture(bundle, pid), observed)
                except Exception as error:
                    dock.usage_error = str(error)
            dock.usage_timer.timeout.connect(sample_usage)
            dock.usage_timer.start(5000)
            track_apps = QAction('Учитывать время приложений в учебных блоках', mw)
            track_apps.setCheckable(True)
            track_apps.setChecked(dock.preferences.get('track_apps', True))
            def toggle_tracking(checked):
                dock.preferences['track_apps'] = checked
                dock.usage_tracker.previous = None
                bridge.save_preferences(PREFS, dock.preferences)
                update_home_widget()
            track_apps.toggled.connect(toggle_tracking)
            settings_menu.addAction(track_apps)
            mw._slovo_tracking_action = track_apps
            details_action = QAction('Детализация сайтов и приложений…', mw)
            details_action.triggered.connect(configure_activity_details)
            settings_menu.addAction(details_action)
            mw._slovo_details_action = details_action
        except Exception as error:
            dock.usage_error = str(error)
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
            settings_menu.addAction(menu_timer)
            mw._slovo_menu_timer_action = menu_timer
        except Exception as error:
            dock.message.setText('Таймер в строке меню недоступен: ' + str(error))



gui_hooks.profile_did_open.append(loaded)


def unloaded():
    health = getattr(mw, '_slovo_review_health', None)
    if health:
        health.closed = True
        mw._slovo_review_health = None
    dock = getattr(mw, '_slovo_study_dock', None)
    if dock:
        dock.closed = True
        dock.next_save.stop()
        dock.rest_screen.dispose()
        dock.phase_notice.dismiss()
        dock.phase_notice.deleteLater()
        dock.poller.stop()
        dock.display_timer.stop()
        if getattr(dock, 'usage_timer', None):
            dock.usage_timer.stop()
        if dock.usage_tracker:
            dock.usage_tracker.close()
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
    for name in ('_slovo_repair_action', '_slovo_review_restore_action', '_slovo_archive_action', '_slovo_restore_action', '_slovo_menu_timer_action', '_slovo_tracking_action', '_slovo_details_action', '_slovo_wallpaper_action', '_slovo_signal_action'):
        extra = getattr(mw, name, None)
        if extra:
            mw.form.menuTools.removeAction(extra)
            extra.deleteLater()
            setattr(mw, name, None)

    menu = getattr(mw, '_slovo_study_menu', None)
    if menu:
        mw.form.menuTools.removeAction(menu.menuAction())
        menu.deleteLater()
        mw._slovo_study_menu = None


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
   html body .container.modern-main-menu::after {
    content:'';position:absolute;top:50%;left:50%;width:100%;height:100%;
    transform:var(--slovo-wallpaper-transform,translate(-50%,-50%));
    background-image:var(--slovo-wallpaper-next,none) !important;
    background-size:cover;background-position:center;background-repeat:no-repeat;
    filter:var(--slovo-wallpaper-filter,none);z-index:0;pointer-events:none;
    opacity:var(--slovo-wallpaper-fade,0) !important;
    transition:var(--slovo-wallpaper-transition,opacity 1.2s ease) !important;
   }
   @media(prefers-reduced-motion:reduce){html body .container.modern-main-menu::after{transition:none !important;}}
   #onigiri-heatmap-container .heatmap-day-cell.today {
    outline:1px solid #e26666 !important;outline-offset:1px;border-radius:3px;
   }
   #onigiri-heatmap-container .heatmap-day-cell.today .day-shape {animation:none !important;}
  `;
  document.head.appendChild(style);
  let pending='', generation=0, fadeTimer;
  const updateDaylight=()=>{
   const wallpaper=window.slovoHomeState?.wallpaper;
   if(!wallpaper)return;
   const root=document.documentElement, key=wallpaper.path;
   if(root.dataset.slovoWallpaper===key){
    if(pending){++generation;pending='';clearTimeout(fadeTimer);root.style.setProperty('--slovo-wallpaper-fade','0');}
    return;
   }
   if(pending===key)return;
   pending=key;const token=++generation;
   clearTimeout(fadeTimer);
   root.style.setProperty('--slovo-wallpaper-fade','0');
   const url='/_addons/ADDON_PACKAGE/'+key, value='url("'+url+'")';
   const preload=new Image();preload.onerror=()=>{if(token===generation)pending='';};
   preload.onload=()=>{
    if(token!==generation)return;
    const finish=()=>{
     if(token!==generation)return;
     root.style.setProperty('--slovo-wallpaper',value);
     root.style.setProperty('--slovo-wallpaper-transition','none');
     root.style.setProperty('--slovo-wallpaper-fade','0');
     root.dataset.slovoWallpaper=key;root.dataset.slovoScene=wallpaper.scene;pending='';
     requestAnimationFrame(()=>requestAnimationFrame(()=>{if(token===generation)root.style.removeProperty('--slovo-wallpaper-transition');}));
    };
    if(!root.dataset.slovoWallpaper||matchMedia('(prefers-reduced-motion:reduce)').matches){finish();return;}
    const main=document.querySelector('.container.modern-main-menu');
    const appearance=getComputedStyle(main,'::before');
    root.style.setProperty('--slovo-wallpaper-transform',appearance.transform);
    root.style.setProperty('--slovo-wallpaper-filter',appearance.filter);
    root.style.setProperty('--slovo-wallpaper-next',value);
    root.style.setProperty('--slovo-wallpaper-fade',appearance.opacity);
    fadeTimer=setTimeout(finish,1250);
   };preload.src=url;
  };
  window.slovoUpdateDaylight=updateDaylight;
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
   #slovo-home-focus .sf-wallpaper {margin-top:8px;padding:5px;background:transparent;color:#a9c4cc;font-size:10px;font-weight:400}
   #slovo-archive-link {order:999;margin:10px 20px;font-size:11px;color:inherit;opacity:.65;cursor:pointer;background:transparent;border:0;text-align:left}
  </style><div class="sf-top"><span class="sf-phase">ФОКУС</span><span class="sf-count">0 / 5</span></div>
  <div class="sf-time">80:00</div><div class="sf-task">Выбери задачи для блока</div>
  <div class="sf-track"><div class="sf-fill"></div></div>
  <div class="sf-sub">80 мин учёбы · отдых 10 / 30 мин</div>
  <button type="button">Настроить блок →</button><button class="sf-wallpaper" type="button">Обои · время суток</button>`;
  el.querySelector('button').onclick = () => pycmd(el._snapshot?.awaitingRest?'slovo:rest':el._snapshot?.readyNext?'slovo:next':'slovo:open');
  el.querySelector('.sf-wallpaper').onclick = () => pycmd('slovo:wallpaper');
  host.appendChild(el);
  const timer = setInterval(() => {
   if (!el.isConnected) {clearInterval(timer);return;}
   const s = el._snapshot; if (!s) return;
   const elapsed=(performance.now()-s.at)/1000;
   const sec = s.awaitingRest?Math.floor(s.overtime+elapsed):Math.max(0,Math.ceil(s.remaining-(s.active?elapsed:0)));
   el.querySelector('.sf-time').textContent = (s.awaitingRest?'+':'')+String(Math.floor(sec/60)).padStart(2,'0')+':'+String(sec%60).padStart(2,'0');
  },250);
 }
 const s = PAYLOAD;
 window.slovoHomeState=s;window.slovoUpdateDaylight?.();
 el._snapshot = {...s,at:performance.now()};
 el.querySelector('.sf-phase').textContent = s.phase;
 el.querySelector('.sf-count').textContent = s.seriesTotal?'Серия '+s.seriesIndex+'/'+s.seriesTotal:s.count+' за день';
 el.querySelector('.sf-task').textContent = s.title;
 el.querySelector('.sf-fill').style.width = Math.min((s.seriesTotal?(s.seriesIndex-1)/s.seriesTotal:s.count/5)*100,100)+'%';
 el.querySelector('.sf-sub').textContent = s.subtitle;
 el.querySelector('button').textContent = s.awaitingRest?'Начать отдых →':s.readyNext?'Я вернулся →':s.active ? 'Открыть фокус →' : 'Выбрать задачи →';
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
    #slovo-home-tasks .st-list {display:flex;flex-direction:column;gap:5px;box-sizing:border-box;width:100%;max-width:100%;min-width:0;overflow-x:hidden;overflow-y:auto;overscroll-behavior-x:none;overscroll-behavior-y:contain;touch-action:pan-y;min-height:0;flex:1;scrollbar-width:thin}
    #slovo-home-tasks .st-task {display:grid;grid-template-columns:17px minmax(0,1fr);align-items:center;justify-items:start;gap:10px;width:100%;max-width:100%;min-width:0;margin:0;box-sizing:border-box;background:#17384599;border:1px solid #49687470;border-radius:10px;padding:7px 10px;color:#d9e7e9;text-align:left;cursor:pointer;font:inherit;font-size:12px;line-height:1.35;flex-shrink:0}
    #slovo-home-tasks .st-task:hover {background:#2b505ed9;border-color:#c9b17c}
    #slovo-home-tasks .st-task:disabled {cursor:default;opacity:.65}
    #slovo-home-tasks .st-num {color:#dfc38f;font-size:13px;flex:none;border:1px solid #67828c;border-radius:4px;width:17px;height:17px;box-sizing:border-box;display:grid;place-items:center}
    #slovo-home-tasks .st-task[aria-checked="true"] {border-color:#d2b67d;background:#294c57bb}
    #slovo-home-tasks .st-title {overflow-wrap:anywhere;word-break:normal;min-width:0;width:100%;display:block;white-space:normal;text-align:left}
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
  const key = JSON.stringify([s.tasks,s.active,s.editable,s.connected,s.planBusy,s.selected]);
  if (tasks._key !== key) {
   tasks._key = key;
   const list = tasks.querySelector('.st-list'), position=list.scrollTop;
   list.querySelector('.st-empty')?.remove();
   const existing=new Map([...list.querySelectorAll('.st-task')].map(b=>[b.dataset.id,b]));
   s.tasks.forEach((task,i) => {
    const id=task.id||String(i);
    let button=existing.get(id);
    if(!button){
     button=document.createElement('button');button.type='button';button.className='st-task';button.dataset.id=id;
     button.setAttribute('role','checkbox');
     const num=document.createElement('span');num.className='st-num';
     const title=document.createElement('span');title.className='st-title';
     button.append(num,title);
     button.onclick=()=>pycmd('slovo:task:'+button.dataset.index);
    }
    existing.delete(id);button.dataset.index=i;button.disabled=!s.editable;button.title=task.title;
    button.setAttribute('aria-checked',String(task.selected));
    button.querySelector('.st-num').textContent=task.selected?'✓':'';
    button.querySelector('.st-title').textContent=task.title;
    // Reuse nodes on selection updates: focus and scroll stay where the user left them.
    if(list.children[i]!==button)list.insertBefore(button,list.children[i]||null);
   });
   existing.forEach(button=>button.remove());
   list.scrollTop=position;
   if (!s.tasks.length) {
    const empty=document.createElement('div');empty.className='st-empty';
    empty.textContent=s.planBusy?'Загружаем план из Слова…':s.connected?'В плане пока нет задач. Добавь их в Слове и обнови список.':'Подключи Слово в настройках блока, чтобы видеть свой план здесь.';
    list.appendChild(empty);
   }
   tasks.querySelector('.st-refresh').disabled=s.active||s.planBusy;
   tasks.querySelector('.st-note').textContent=!s.editable?'Блок идёт · задачи зафиксированы':s.tasks.length?(s.selected?'Выбрано: '+s.selected+' · настрой блок →':'Отметь одну или несколько задач'):'План обновляется автоматически';
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

STATS_SCRIPT = (Path(__file__).parent / 'focus-stats.js').read_text()


def update_home_widget(*_):
    if not mw.col or mw.state != 'deckBrowser':
        return
    dock = getattr(mw, '_slovo_study_dock', None)
    state = dock.state if dock else None
    session = (state or {}).get('session')
    phase = (session or {}).get('phase', 'idle')
    committed = (session or {}).get('task', {})
    committed_items = committed.get('items', [committed] if committed else [])
    editable = not session or phase == 'prepare'
    chosen = {item.get('occurrence_id') for item in committed_items} if session and not editable else (dock.selected_ids if dock else set())
    tasks = dock.tasks if dock and dock.tasks else committed_items
    payload = {
        'phase': 'ДОПОЛНИТЕЛЬНОЕ ВРЕМЯ' if (session or {}).get('awaiting_rest') else {'idle': 'ГОТОВ К СТАРТУ', 'focus': 'ВРЕМЯ ФОКУСА', 'break': 'ВРЕМЯ ОТДЫХА', 'ready': 'ПЕРЕРЫВ ЗАВЕРШЁН', 'prepare': 'ВЫБОР ЗАДАЧ · АВТОСТАРТ'}[phase],
        'remaining': bridge.remaining_seconds(state) if session else (dock.timing_values() if dock else bridge.timing_settings())['focus'] * 60, 'active': bool(session),
        'awaitingRest': bool((session or {}).get('awaiting_rest')), 'overtime': bridge.overtime_seconds(state),
        'readyNext': phase == 'ready', 'editable': editable,
        'count': (state or {}).get('completed_today', 0),
        'seriesTotal': (session or {}).get('blocks_total', 0),
        'seriesIndex': (session or {}).get('blocks_total', 0) - (session or {}).get('blocks_left', 0) + 1,
        'title': committed.get('title', f'Выбрано задач: {len(chosen)}' if chosen else 'Выбери задачи для блока'),
        'subtitle': ('Отдых начнётся после подтверждения' if (session or {}).get('awaiting_rest') else
                     f'{(state or {}).get("prepare_seconds", 300) // 60} мин на выбор. Затем автоматический старт.' if phase == 'prepare' else
                     bridge.cycle_text(session or (dock.timing_values() if dock else bridge.timing_settings())) if state else 'Проверяем системный таймер…'),
        'archive': str((mw.col.get_config('slovo_archive') or {}).get('deck_id', '')),
        'tasks': [{'id': task.get('occurrence_id'), 'title': bridge.task_display_title(task),
                   'selected': task.get('occurrence_id') in chosen} for task in tasks],
        'wallpaper': bridge.wallpaper_settings(dock.preferences if dock else {}),
        'selected': len(chosen),
        'connected': bool(dock and dock.preferences.get('token')),
        'planBusy': bool(dock and dock.plan_busy),
    }
    mw.deckBrowser.web.eval(HOME_SCRIPT.replace('ADDON_PACKAGE', mw.addonManager.addonFromModule(__name__)).replace('PAYLOAD', json.dumps(payload, ensure_ascii=False)))
    usage = dock.usage_tracker.summary() if dock and dock.usage_tracker else {}
    stats_state = dict(state or {})
    if dock and dock.usage_tracker:
        stats_state['history'] = list(dock.usage_tracker.blocks.values()) + (state or {}).get('history', [])
    stats = analytics.snapshot(stats_state, usage, remaining=bridge.remaining_seconds(state), overtime=bridge.overtime_seconds(state))
    health = getattr(mw, '_slovo_review_health', None)
    if health:
        health.refresh()
    stats['review_health'] = health.data if health else {'loading': True}
    stats['mode'] = dock.preferences.get('stats_mode', 'focus') if dock else 'focus'
    stats['tracking'] = bool(dock and dock.usage_tracker and dock.preferences.get('track_apps', True) and not getattr(dock, 'usage_error', None))
    stats['detail_tracking'] = bool(dock and dock.detail_reader and dock.preferences.get('track_details', True))
    stats['detail_status'] = dock.detail_status if dock else {}
    stats['detail_supported'] = list(activity_detail.BROWSERS) + list(activity_detail.WINDOW_APPS)
    stats['detail_messages'] = activity_detail.STATUS
    mw.deckBrowser.web.eval(STATS_SCRIPT.replace('ADDON_PACKAGE', mw.addonManager.addonFromModule(__name__)).replace('PAYLOAD', json.dumps(stats, ensure_ascii=False)))


def home_message(handled, message, context):
    if handled[0] or context is not mw.deckBrowser:
        return handled
    if message == 'slovo:review-repair':
        open_review_queue()
        return True, None
    if message == 'slovo:rest':
        ensure_study().begin_rest()
        return True, None
    if message == 'slovo:next':
        ensure_study().prepare_next()
        return True, None
    if message == 'slovo:details-settings':
        configure_activity_details()
        return True, None
    if message in ('slovo:stats-mode:cards', 'slovo:stats-mode:focus'):
        dock = ensure_study()
        dock.preferences['stats_mode'] = message.rsplit(':', 1)[1]
        bridge.save_preferences(PREFS, dock.preferences)
        return True, None
    if message == 'slovo:open':
        show_study()
        return True, None
    if message == 'slovo:wallpaper':
        choose_wallpaper()
        return True, None
    if message == 'slovo:refresh':
        dock = ensure_study()
        if not (dock.state or {}).get('session'):
            dock.load_plan()
        return True, None
    if message.startswith('slovo:task:'):
        dock = ensure_study()
        index = message.removeprefix('slovo:task:')
        if index.isdecimal() and 0 <= int(index) < len(dock.tasks) and dock.can_edit():
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
        showInfo(f'Старые колоды скрыты, {count} карточек исключены из повторений.\nКарточки и история сохранены. Восстановление: Инструменты → Фокус и учёба → Обслуживание → Вернуть колоды из архива.')
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
