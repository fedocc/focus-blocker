"""Build a credential-free Anki add-on and macOS service release."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def build():
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    addon_files = ('__init__.py', 'dashboard.py', 'bridge.py', 'manifest.json') + tuple(
        f'wallpaper-{scene}.png' for scene in ('dawn', 'day', 'evening', 'night'))
    addon = destination / 'slovo-study-blocks.ankiaddon'
    with zipfile.ZipFile(addon, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in addon_files:
            archive.write(ROOT / 'anki_study' / name, name)
    release = destination / 'focus-study-macos.zip'
    source_files = ['README.md', 'install.py', 'install-study.command', 'focus-helper',
                    'focus_open.py', 'focus_youtube_guard.py', 'focus_service_policy.py',
                    'focus_playlist_sync.py', 'focus_study.py', 'youtube-policy.json']
    source_files += [path.name for path in sorted(ROOT.glob('test_*.py'))]
    source_files += ['anki_study/' + name for name in addon_files]
    with zipfile.ZipFile(release, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files:
            archive.write(ROOT / name, 'focus-study/' + name)
    for path in (addon, release):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(path.suffix + '.sha256').write_text(digest + '  ' + path.name + '\n')
        print(path.name, path.stat().st_size, digest)


if __name__ == '__main__':
    build()
