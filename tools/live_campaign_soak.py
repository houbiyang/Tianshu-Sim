"""Resumable real DSH campaign soak; private evidence, isolated from user battles."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--turns', type=int, default=20)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.turns <= 100:
        parser.error('--turns must be between 1 and 100')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name, directory in [('SESSION_DIR', 'sessions'), ('TRACE_DIR', 'traces'),
                            ('DSH_HOME', 'dsh'), ('DSH_WORKSPACE', 'workspace')]:
        os.environ['GENTACTIX_' + name] = str(out / directory)
    os.environ['GENTACTIX_ROLE_INDEX'] = str(out / 'roles.json')
    from backend import main as api, harness_engine as h, session_store
    from fastapi.testclient import TestClient

    summary_path = out / 'summary.json'
    summary = json.loads(summary_path.read_text()) if args.resume and summary_path.exists() else {
        'live_model_calls': True, 'stubbed': False, 'checks': [], 'turns': [], 'passed': False}
    summary.update(target_turns=args.turns, passed=False)

    def save():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    def call(client, name, method, path, payload=None):
        started = time.monotonic()
        response = client.request(method, path, json=payload)
        value = response.json()
        filename = f'{len(summary["checks"]):03d}-{name}.json'
        (out / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2))
        row = dict(name=name, http=response.status_code,
                   seconds=round(time.monotonic()-started, 2), evidence=filename)
        summary['checks'].append(row)
        save()
        print(json.dumps(row), flush=True)
        if response.status_code != 200:
            raise AssertionError(f'{name}: HTTP {response.status_code}; see private evidence')
        return value

    try:
        with TestClient(api.app) as client:
            if args.resume:
                assert len(api._SESSIONS) == 1, 'Resume needs exactly one isolated checkpoint'
                sid = next(iter(api._SESSIONS))
                initial = json.loads((out / 'initial.json').read_text())
            else:
                assert not api._SESSIONS, 'Use a fresh output directory or --resume'
                battle = next(b['name'] for b in api.battles()['battles'] if '上甘岭' in b['name'])
                sid = call(client, 'create', 'POST', '/api/sessions', {
                    'scenario': '上甘岭局部教学想定，1952年10月14日04:00。只设置红蓝双方各两个代表性连级单位（步兵与支援），控制在四个单位。围绕597.9高地持续观察阵地争夺、坑道、补给与损耗，计划连续20回合，每回合严格15分钟。不预设战果，不跳过时间。',
                    'battle': battle, 'time_step': '15分钟', 'use_adjudicator': True,
                    'use_rag': True, 'persist_roles': True, 'use_agent_tools': True,
                    'agent_mode': 'native'})['session_id']
                initial = call(client, 'init', 'POST', f'/api/sessions/{sid}/init')
                (out / 'initial.json').write_text(json.dumps(initial, ensure_ascii=False, indent=2))
            summary.update(session_id=sid, time_before=initial['war_state']['current_time'])
            previous = call(client, 'current', 'GET', f'/api/sessions/{sid}')
            for turn in range(previous['turn_count'] + 1, args.turns + 1):
                advanced = call(client, f'next-{turn}', 'POST', f'/api/sessions/{sid}/next')
                assert advanced['turn_count'] == turn
                world = advanced['war_state']
                assert world['simulation_clock']['elapsed_seconds'] == turn * 900
                json.dumps(world, allow_nan=False)
                trace = call(client, f'trace-{turn}', 'GET', f'/api/sessions/{sid}/traces/{turn}')
                assert trace['persistence'] and all(r['status'] == 'verified' for r in trace['persistence'])
                # Validate the actual durable checkpoint, not only the in-memory response.
                restored, errors = session_store.load_all()
                assert not errors and restored[sid]['war_state'] == world
                assert restored[sid]['turn_count'] == turn
                assert len(restored[sid]['trajectory']) == turn + 1
                row = dict(turn=turn, time=world['current_time'],
                           units={team: [u['name'] for u in world[team]] for team in ('red_units', 'blue_units')},
                           integrity=advanced.get('integrity'), checkpoint_verified=True,
                           trace_verified=True)
                summary['turns'].append(row)
                summary.update(completed_turns=turn, elapsed_seconds=turn*900, time_after=world['current_time'])
                save()
                previous = advanced
                if turn == 10:
                    # Reload persisted world and resume native role history for the next real call.
                    h.close_harness()
                    api._SESSIONS[sid] = restored[sid]
                    summary['midpoint_runtime_restart'] = True
                    save()
            exported = call(client, 'export', 'GET', f'/api/sessions/{sid}/export')
            (out / 'export.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2))
            summary['passed'] = exported['turn_count'] == args.turns
            save()
    finally:
        save()
        h.close_harness()


if __name__ == '__main__':
    main()
