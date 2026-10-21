"""Atomic battle checkpoints, independent of SDK session memory."""
import copy
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def directory():
    return Path(os.environ.get('GENTACTIX_SESSION_DIR', str(ROOT / '.cache' / 'sessions')))


def serializable(session):
    return copy.deepcopy({k: v for k, v in session.items() if k not in ('adj_spec', 'active_observer', 'progress')})


def save(session):
    path = directory() / (session['id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'format': 'gentactix-session-v1', 'session': serializable(session)}
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_all():
    sessions, errors = {}, []
    for path in directory().glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            session = value['session']
            if value['format'] != 'gentactix-session-v1' or not re.fullmatch(r'[a-f0-9]{12}', session['id']) or path.stem != session['id']:
                raise ValueError('invalid checkpoint identity')
            session.update(adj_spec=None, active_observer=None, progress=None, cancel_requested=False)
            sessions[session['id']] = session
        except (ValueError, KeyError, TypeError, OSError):
            errors.append(path.name)
    return sessions, errors


def remove(session_id):
    (directory() / (session_id + '.json')).unlink(missing_ok=True)
