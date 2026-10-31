#!/usr/bin/env python3
"""Build the native Little Arc guard app with Apple's local compiler."""
from pathlib import Path
import plistlib
import subprocess
import sys


def build(destination, series=False):
    app = Path(destination)
    if app.exists():
        raise SystemExit('Choose a new build directory; existing app was preserved')
    identifier = 'local.focus-open.series-guard' if series else 'local.focus-open.preview-guard'
    title = 'Focus Series Guard' if series else 'Focus Preview Guard'
    contents = app / 'Contents'
    binary = contents / 'MacOS/FocusWindowGuard'
    binary.parent.mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': identifier,
        'CFBundleName': title,
        'CFBundleExecutable': 'FocusWindowGuard',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1',
        'LSUIElement': True,
        'NSAccessibilityUsageDescription': 'Проверяет адреса Little Arc и Peek и закрывает только запрещённые предпросмотры.'}))
    subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', *(['-D', 'SERIES_SCHEDULE'] if series else []), str(Path(__file__).with_name('focus_window_guard.swift')), '-o', str(binary)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', identifier, str(app)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
    return app


if __name__ == '__main__':
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != '--series'):
        raise SystemExit('Usage: build_window_guard.py /new/path/Focus Link Guard.app')
    print(build(sys.argv[1], series=len(sys.argv) == 3))
