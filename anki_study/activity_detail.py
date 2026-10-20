"""Local foreground metadata only: browser locations and selected window titles.

No page text, screenshots, input, terminal commands or network service. Browser
reads and Accessibility calls run off Qt's collection/main thread. Permission
checks never prompt; an explicit settings action can request browser access.
"""
import ctypes as c
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import unicodedata
from urllib.parse import parse_qs, urlsplit
from xml.parsers.expat import ExpatError

BROWSERS = {
    'company.thebrowser.Browser': ('Arc', 'arc'),
    'com.google.Chrome': ('Google Chrome', 'chrome'),
    'com.apple.Safari': ('Safari', 'safari'),
    'com.microsoft.edgemac': ('Microsoft Edge', 'chrome'),
    'com.brave.Browser': ('Brave', 'chrome'),
}
WINDOW_APPS = {
    'com.microsoft.VSCode': ('Visual Studio Code', 'Файлы и проекты из названия окна'),
    'com.jetbrains.pycharm': ('PyCharm', 'Файлы и проекты из названия окна'),
    'com.jetbrains.CLion': ('CLion', 'Файлы и проекты из названия окна'),
    'com.apple.dt.Xcode': ('Xcode', 'Файлы и проекты из названия окна'),
    'com.apple.Preview': ('Preview', 'Названия PDF и других документов'),
    'com.apple.iWork.Pages': ('Pages', 'Названия документов'),
    'com.apple.Pages': ('Pages Creator Studio', 'Названия документов'),
    'com.apple.iWork.Numbers': ('Numbers', 'Названия таблиц'),
    'com.apple.iWork.Keynote': ('Keynote', 'Названия презентаций'),
    'com.apple.TextEdit': ('TextEdit', 'Названия документов'),
    'com.goodnotesapp.x': ('Goodnotes', 'Название открытого окна, если доступно'),
    'com.apple.Notes': ('Notes', 'Название открытого окна, если доступно'),
    'com.apple.QuickTimePlayerX': ('QuickTime Player', 'Название открытого видео'),
    'us.zoom.xos': ('Zoom', 'Название окна или встречи, если доступно'),
    'org.mozilla.firefox': ('Firefox', 'Названия окон; без адресов сайтов'),
}
STATUS = {
    'ready': 'Детализация работает',
    'permission': 'Разреши Anki доступ к браузеру: macOS → Конфиденциальность → Автоматизация',
    'accessibility': 'Для названий окон разреши Anki универсальный доступ',
    'unsupported': 'Для этого приложения учитывается общее время',
    'timeout': 'Приложение не ответило; учитывается общее время',
    'empty': 'Название или адрес не удалось определить',
    'not_running': 'Сначала открой браузер, затем разреши доступ',
    'error': 'Подробности недоступны; учитывается общее время',
    'private': 'Приватное окно: содержимое не записывается',
}


class AEDesc(c.Structure):
    # AEDataModel.h explicitly uses two-byte packing, including on arm64.
    _pack_ = 2
    _fields_ = [('descriptorType', c.c_uint32), ('dataHandle', c.c_void_p)]


def clean_label(value, limit=180):
    if not isinstance(value, str):
        return ''
    value = ''.join(' ' if unicodedata.category(ch).startswith('C') else ch for ch in value)
    value = ' '.join(value.split())
    # A document title may be a full local path; keep just its last component.
    value = re.sub(r'/(?:Users|Volumes|private|tmp|home)/[^—|]*',
                   lambda match: match.group().rstrip('/ ').rsplit('/', 1)[-1], value)
    return value[:limit]


def context(kind, group, name, identity):
    name = clean_label(name)
    if not name:
        return None
    return {'kind': kind, 'group': clean_label(group), 'name': name,
            'key': hashlib.sha256(identity.encode()).hexdigest()[:32]}


def browser_context(url, title='', private=False):
    if private:
        return context('private', 'Приватное окно', 'Без записи сайтов и страниц', 'private')
    if not isinstance(url, str) or len(url) > 16384:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            return None
        host = parsed.hostname.lower().rstrip('.')
        if host.startswith('www.'):
            host = host[4:]
        port = parsed.port
        if port and port != {'https': 443, 'http': 80}[parsed.scheme]:
            host += ':' + str(port)
    except ValueError:
        return None
    path = parsed.path or '/'
    # Distinguish lectures; do not store search queries, fragments or credentials.
    video = ''
    if host == 'youtu.be':
        video = path.strip('/')
    elif host in ('youtube.com', 'm.youtube.com'):
        video = parse_qs(parsed.query).get('v', [''])[0] if path == '/watch' else path.removeprefix('/shorts/') if path.startswith('/shorts/') else ''
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
        host, path = 'youtube.com', '/watch/' + video
    # Path is hashed for grouping, never persisted as a URL.
    return context('site', host, clean_label(title) or host, host + path)


def window_context(bundle, title):
    if bundle not in WINDOW_APPS:
        return None
    title = clean_label(title)
    if not title:
        return None
    if bundle == 'org.mozilla.firefox' and re.search(r'private browsing|приватн', title, re.I):
        return context('private', 'Приватное окно', 'Без записи названия страницы', 'private')
    group = 'Окна и документы'
    if bundle == 'com.microsoft.VSCode':
        pieces = title.split(' — ')
        if pieces[-1] in ('Visual Studio Code', 'Code'):
            pieces.pop()
        if len(pieces) == 2:
            title, group = pieces
    return context('window', group, title, bundle + '\n' + group + '\n' + title)


def browser_path(bundle):
    name = BROWSERS[bundle][0]
    for root in (Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications'):
        path = root / (name + (' Browser' if bundle == 'com.brave.Browser' else '') + '.app')
        try:
            info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
            if isinstance(info, dict) and info.get('CFBundleIdentifier') == bundle:
                return str(path)
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
            continue
    return None


def browser_script(bundle, path=None):
    if bundle not in BROWSERS:
        raise ValueError('Unsupported metadata browser')
    kind = BROWSERS[bundle][1]
    # An explicit verified bundle path also resolves scripting terminology when
    # LaunchServices cannot resolve application IDs in a child process.
    target = 'application ' + json.dumps(path, ensure_ascii=False) if path else 'application id ' + json.dumps(bundle)
    tab = 'current tab' if kind == 'safari' else 'active tab'
    title = '""' if kind == 'safari' else 'title of t'
    private = ('if incognito of w then return "PRIVATE"' if kind == 'arc' else
               'if mode of w is "incognito" then return "PRIVATE"' if kind == 'chrome' else '')
    # Safari has no supported per-window private flag in its scripting dictionary.
    # It is intentionally domain-only, so page titles are not exposed there.
    return '\n'.join([
        f'if not ({target} is running) then return "NOT_RUNNING"',
        'with timeout of 2 seconds', f'tell {target}',
        'if (count of windows) is 0 then return "EMPTY"', 'set w to front window',
        private, f'set t to {tab} of w',
        f'return (URL of t) & (ASCII character 31) & ({title})',
        'end tell', 'end timeout',
    ])


class NativeMetadata:
    """Owned CoreFoundation references; bounded AX calls and non-prompting TCC."""
    def __init__(self):
        self.cf = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = c.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.ae = c.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
        self.AEDesc = AEDesc
        self.ae.AECreateDesc.argtypes = [c.c_uint32, c.c_void_p, c.c_long, c.POINTER(AEDesc)]
        self.ae.AECreateDesc.restype = c.c_int16
        self.ae.AEDisposeDesc.argtypes = [c.POINTER(AEDesc)]
        self.ae.AEDeterminePermissionToAutomateTarget.argtypes = [c.POINTER(AEDesc), c.c_uint32, c.c_uint32, c.c_bool]
        self.ae.AEDeterminePermissionToAutomateTarget.restype = c.c_int32
        self.cf.CFRelease.argtypes = [c.c_void_p]
        self.cf.CFStringCreateWithCString.argtypes = [c.c_void_p, c.c_char_p, c.c_uint32]
        self.cf.CFStringCreateWithCString.restype = c.c_void_p
        self.cf.CFStringGetCString.argtypes = [c.c_void_p, c.c_void_p, c.c_long, c.c_uint32]
        self.cf.CFStringGetCString.restype = c.c_bool
        self.cf.CFGetTypeID.argtypes = [c.c_void_p]
        self.cf.CFGetTypeID.restype = c.c_ulong
        self.cf.CFStringGetTypeID.restype = c.c_ulong
        self.ax.AXIsProcessTrusted.restype = c.c_bool
        self.ax.AXUIElementCreateApplication.argtypes = [c.c_int32]
        self.ax.AXUIElementCreateApplication.restype = c.c_void_p
        self.ax.AXUIElementSetMessagingTimeout.argtypes = [c.c_void_p, c.c_float]
        self.ax.AXUIElementCopyAttributeValue.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
        self.ax.AXUIElementCopyAttributeValue.restype = c.c_int32

    def automation_allowed(self, bundle):
        desc = self.AEDesc()
        raw = bundle.encode()
        if self.ae.AECreateDesc(int.from_bytes(b'bund', 'big'), raw, len(raw), c.byref(desc)):
            return False
        try:
            return self.ae.AEDeterminePermissionToAutomateTarget(c.byref(desc), int.from_bytes(b'core', 'big'),
                                                               int.from_bytes(b'getd', 'big'), False) == 0
        finally:
            self.ae.AEDisposeDesc(c.byref(desc))

    def attribute(self, element, name):
        key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        if not key:
            return None
        value = c.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, c.byref(value))
            return value.value if error == 0 else None
        finally:
            self.cf.CFRelease(key)

    def window_title(self, pid):
        if not self.ax.AXIsProcessTrusted():
            return '', 'accessibility'
        app = self.ax.AXUIElementCreateApplication(pid)
        window = title = None
        try:
            self.ax.AXUIElementSetMessagingTimeout(app, .25)
            window = self.attribute(app, 'AXFocusedWindow')
            if not window:
                return '', 'empty'
            self.ax.AXUIElementSetMessagingTimeout(window, .25)
            title = self.attribute(window, 'AXTitle')
            if not title or self.cf.CFGetTypeID(title) != self.cf.CFStringGetTypeID():
                return '', 'empty'
            buffer = c.create_string_buffer(4096)
            if not self.cf.CFStringGetCString(title, buffer, len(buffer), 0x08000100):
                return '', 'empty'
            return buffer.value.decode('utf-8'), 'ready'
        finally:
            for value in (title, window, app):
                if value:
                    self.cf.CFRelease(value)


class MacDetails:
    def __init__(self, native=None):
        self.native = native or NativeMetadata()

    def browser(self, bundle, request_access=False):
        if not request_access and not self.native.automation_allowed(bundle):
            return None, 'permission'
        try:
            result = subprocess.run(['/usr/bin/osascript', '-e', browser_script(bundle, browser_path(bundle))], capture_output=True,
                                    text=True, timeout=60 if request_access else 2.5)
        except subprocess.TimeoutExpired:
            return None, 'timeout'
        if result.returncode:
            return None, 'permission' if '-1743' in result.stderr else 'error'
        raw = result.stdout.rstrip('\r\n')
        if raw == 'NOT_RUNNING':
            return None, 'not_running'
        if raw == 'PRIVATE':
            return browser_context('', private=True), 'private'
        if len(raw) > 32768 or chr(31) not in raw:
            return None, 'empty'
        url, title = raw.split(chr(31), 1)
        if BROWSERS[bundle][1] == 'safari':
            title = ''
        item = browser_context(url, title)
        if item and BROWSERS[bundle][1] == 'safari':
            item = context('site', item['group'], item['group'], item['group'])
        return item, 'ready' if item else 'empty'

    def capture(self, bundle, pid):
        try:
            if bundle in BROWSERS:
                return self.browser(bundle)
            if bundle in WINDOW_APPS:
                title, status = self.native.window_title(pid)
                item = window_context(bundle, title)
                return item, status if not item else 'ready'
            return None, 'unsupported'
        except (OSError, ValueError):
            return None, 'error'
