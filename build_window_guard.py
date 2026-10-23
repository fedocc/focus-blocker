#!/usr/bin/env python3
"""Build the native Little Arc guard app with Apple's local compiler."""
from pathlib import Path
import plistlib
import subprocess
import sys


def build(destination):
    app = Path(destination)
    if app.exists():
        raise SystemExit('Choose a new build directory; existing app was preserved')
    contents = app / 'Contents'
    binary = contents / 'MacOS/FocusWindowGuard'
    binary.parent.mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.focus-open.link-guard',
        'CFBundleName': 'Focus Link Guard',
        'CFBundleExecutable': 'FocusWindowGuard',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1',
        'LSUIElement': True,
        'NSAccessibilityUsageDescription': 'Проверяет адрес окна Little Arc и закрывает только запрещённые сайты.'}))
    subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', str(Path(__file__).with_name('focus_window_guard.swift')), '-o', str(binary)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', 'local.focus-open.link-guard', str(app)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
    return app


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: build_window_guard.py /new/path/Focus Link Guard.app')
    print(build(sys.argv[1]))
