"""Build the bundled Python backend, then Linux AppImage and portable archive.

Run with the Python environment containing requirements.txt, requirements-dsh.txt
and PyInstaller. No local .env, model key or session cache is packaged.
"""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
env = os.environ.copy()
# ROS/system PYTHONPATH can leak unrelated packages into a frozen application.
env.pop('PYTHONPATH', None)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
           '--name', 'gentactix-backend', '--distpath', str(ROOT / 'dist-backend'),
           '--workpath', str(ROOT / 'build-backend'), '--specpath', str(ROOT / 'build-backend'),
           '--paths', str(ROOT), '--collect-all', 'deepseek_harness',
           '--collect-all', 'deepseek_harness_runtime', '--collect-all', 'global_land_mask',
           '--copy-metadata', 'deepseek-harness-sdk', '--copy-metadata', 'deepseek-harness-runtime-bin',
           '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto',
           '--hidden-import', 'uvicorn.protocols.http.auto', '--hidden-import', 'uvicorn.protocols.websockets.auto',
           '--hidden-import', 'uvicorn.lifespan.on']
for name in ('corpus', 'adjudication', 'plugins'):
    command += ['--add-data', f'{ROOT / name}:{name}']
for name in ('unit_types.json', 'korean_anchors.json', 'long_march_anchors.json', 'anti_japanese_war_anchors.json', 'missions.json'):
    command += ['--add-data', f'{ROOT / "gentactix" / name}:gentactix']
command += [str(ROOT / 'tools' / 'desktop_backend.py')]
subprocess.run(command, cwd=ROOT, env=env, check=True)
subprocess.run(['npm', 'run', 'dist:linux'], cwd=ROOT / 'frontend', env=env, check=True)
