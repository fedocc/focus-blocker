"""Build a credential-free Anki add-on and macOS service release."""
import hashlib
from pathlib import Path
import zipfile
import subprocess
import shutil

ROOT = Path(__file__).resolve().parent


def build():
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    addon_files = ('__init__.py', 'dashboard.py', 'bridge.py', 'analytics.py', 'focus-stats.js', 'focus-stats.css', 'manifest.json') + tuple(
        f'wallpaper-{scene}.png' for scene in ('dawn', 'day', 'evening', 'night'))
    addon = destination / 'slovo-study-blocks.ankiaddon'
    with zipfile.ZipFile(addon, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in addon_files:
            archive.write(ROOT / 'anki_study' / name, name)
    release = destination / 'focus-study-macos.zip'
    source_files = ['README.md', 'AGENTS.md', 'START_HERE.md', 'CODEX_SETUP.md', 'prepare_friend.py', 'doctor.py',
                    'theme/focus-study.onigiri', 'install.py', 'install-study.command', 'repair-study.command', 'repair-study.applescript', 'focus-helper',
                    'focus_open.py', 'focus_youtube_guard.py', 'focus_service_policy.py',
                    'focus_playlist_sync.py', 'focus_study.py', 'youtube-policy.json']
    source_files += [path.name for path in sorted(ROOT.glob('test_*.py'))]
    source_files += ['anki_study/' + name for name in addon_files]
    repair_app = destination / 'focus-study' / 'Repair-Focus.app'
    if Path('/usr/bin/osacompile').exists():
        repair_app.parent.mkdir(exist_ok=True)
        subprocess.run(['/usr/bin/osacompile', '-o', str(repair_app), str(ROOT / 'repair-study.applescript')], check=True)
        for name in source_files:
            target = repair_app.parent / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
    with zipfile.ZipFile(release, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files:
            archive.write(ROOT / name, 'focus-study/' + name)
        if repair_app.exists():
            for path in sorted(repair_app.rglob('*')):
                if path.is_file():
                    archive.write(path, 'focus-study/Repair-Focus.app/' + path.relative_to(repair_app).as_posix())
    for path in (addon, release):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(path.suffix + '.sha256').write_text(digest + '  ' + path.name + '\n')
        print(path.name, path.stat().st_size, digest)


if __name__ == '__main__':
    build()
