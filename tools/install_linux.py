"""Install an AppImage, icon and Ubuntu menu/desktop entries for the current user.

No sudo is needed. For system installation use the .deb package instead.
"""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('appimage', type=Path)
    parser.add_argument('--project-root', type=Path, help='Use this existing project for encrypted model settings')
    args = parser.parse_args()
    source = args.appimage.resolve(strict=True)
    if source.suffix != '.AppImage':
        parser.error('Expected a built .AppImage file')
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    install = data / 'GenTactix-DSH'
    install.mkdir(parents=True, exist_ok=True)
    executable = install / 'GenTactix-DSH.AppImage'
    temporary = executable.with_suffix('.tmp')
    shutil.copyfile(source, temporary)
    temporary.chmod(0o755)
    temporary.replace(executable)
    icon = data / 'icons/hicolor/512x512/apps/gentactix-dsh.png'
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'frontend/public/icons/linux/512x512.png', icon)
    wrapper = install / 'launch.sh'
    lines = ['#!/bin/sh', 'export APPIMAGE_EXTRACT_AND_RUN=1']
    if args.project_root:
        lines.append('export GENTACTIX_PROJECT_ROOT=' + shlex.quote(str(args.project_root.resolve(strict=True))))
    lines.append('exec ' + shlex.quote(str(executable)) + ' "$@"')
    wrapper.write_text('\n'.join(lines) + '\n')
    wrapper.chmod(0o755)
    entry = '\n'.join([
        '[Desktop Entry]', 'Version=1.0', 'Type=Application', 'Name=GenTactix-DSH',
        'Name[zh_CN]=GenTactix 战役推演', 'Comment=历史战役推演 · 沈阳理工大学装备工程学院深度学习实验室',
        'Exec=' + desktop_quote(wrapper), 'Icon=gentactix-dsh', 'Terminal=false',
        'Categories=Education;', 'StartupWMClass=gentactix-dsh', 'StartupNotify=true', '',
    ])
    menu = data / 'applications/gentactix-dsh.desktop'
    menu.parent.mkdir(parents=True, exist_ok=True)
    menu.write_text(entry)
    menu.chmod(0o755)
    subprocess.run(['desktop-file-validate', str(menu)], check=True)
    subprocess.run(['update-desktop-database', str(menu.parent)], check=True)
    desktop = Path(subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip())
    if desktop != Path.home():
        desktop.mkdir(parents=True, exist_ok=True)
        shortcut = desktop / 'gentactix-dsh.desktop'
        shutil.copyfile(menu, shortcut)
        shortcut.chmod(0o755)
        # GNOME may require a trusted launcher; failure does not affect the menu.
        trusted = subprocess.run(['gio', 'set', str(shortcut), 'metadata::trusted', 'true'], capture_output=True)
        print('Desktop shortcut:', shortcut, '(trusted)' if trusted.returncode == 0 else '(GNOME may require Allow Launching)')
    print('Applications menu entry:', menu)
    print('Executable:', executable)


if __name__ == '__main__':
    main()
