"""Atomic battle checkpoints, independent of SDK session memory."""
import copy
import json
import os
from pathlib import Path
import re
import threading

_WRITE_LOCK = threading.RLock()

ROOT = Path(__file__).resolve().parent.parent


def directory():
    return Path(os.environ.get('GENTACTIX_SESSION_DIR', str(ROOT / '.cache' / 'sessions')))


def serializable(session):
    return copy.deepcopy({k: v for k, v in session.items() if k not in ('adj_spec', 'active_observer', 'progress')})


def save(session):
    # Creation and checkpoints may overlap; a shared .tmp must have one writer.
    with _WRITE_LOCK:
        _save(session)


def _save(session):
    path = directory() / (session['id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'format': 'gentactix-session-v1', 'session': serializable(session)}
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate(session):
    """Validate durable state before import/restore can make it visible to the UI."""
    from gentactix import sim_clock
    if not isinstance(session, dict) or not re.fullmatch(r'[a-f0-9]{12}', str(session.get('id', ''))):
        raise ValueError('会话ID不合法')
    from gentactix.state import DEFAULT_SESSION_STATE
    required = set(DEFAULT_SESSION_STATE) | {'time_step', 'use_adjudicator', 'battle_choice', 'use_rag', 'use_live_intel'}
    if required - session.keys():
        raise ValueError('检查点缺少完整会话字段')
    if not isinstance(session.get('scenario_input'), str):
        raise ValueError('缺少有效想定正文')
    sim_clock.duration_seconds(session.get('time_step'))
    for field in ('turn_count', 'role_epoch'):
        value = session.get(field, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f'{field}必须是非负整数')
    for field in ('history', 'chat_messages', 'trajectory', 'counterfactuals', 'decision_log'):
        if not isinstance(session.get(field, []), list):
            raise ValueError(f'{field}必须是列表')
    if any(not isinstance(item, str) for item in session.get('history', [])):
        raise ValueError('战报必须是文本')
    for field in ('chat_messages', 'trajectory', 'counterfactuals', 'decision_log'):
        if any(not isinstance(item, dict) for item in session.get(field, [])):
            raise ValueError(f'{field}条目必须是对象')
    for field in ('war_state', 'last_war_state'):
        world = session.get(field)
        if world is None:
            continue
        if not isinstance(world, dict) or not isinstance(world.get('current_time'), str):
            raise ValueError('战局必须包含有效时间')
        for team in ('red_units', 'blue_units'):
            if not isinstance(world.get(team), list) or any(not isinstance(u, dict) for u in world[team]):
                raise ValueError(f'{team}必须是单位对象列表')
    # JSON NaN/Infinity is accepted by Python but breaks browser JSON parsing.
    json.dumps(session, allow_nan=False)


def load_all():
    sessions, errors = {}, []
    for path in directory().glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            session = value['session']
            if value['format'] != 'gentactix-session-v1' or not re.fullmatch(r'[a-f0-9]{12}', session['id']) or path.stem != session['id']:
                raise ValueError('invalid checkpoint identity')
            validate(session)
            session.update(adj_spec=None, active_observer=None, progress=None, cancel_requested=False)
            sessions[session['id']] = session
        except (ValueError, KeyError, TypeError, OSError):
            errors.append(path.name)
    return sessions, errors


def remove(session_id):
    (directory() / (session_id + '.json')).unlink(missing_ok=True)
