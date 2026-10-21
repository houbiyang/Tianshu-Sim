"""Explicit live DSH acceptance; isolated battles, real model calls, no production writes.

Run with desktop dependencies: python tools/live_release_smoke.py --live
Evidence is private under .cache/qa/live-release/. Reads the existing encrypted
LLM configuration. Never prints credentials or modifies the provider settings.
"""
import argparse
import copy
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
    parser.add_argument('--out', type=Path, default=ROOT / '.cache/qa/live-release')
    parser.add_argument('--resume', action='store_true', help='Resume the isolated two-turn checkpoint after a failed later check')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    for name, directory in [('SESSION_DIR', 'sessions'), ('TRACE_DIR', 'traces'), ('DSH_HOME', 'dsh'), ('DSH_WORKSPACE', 'workspace')]:
        os.environ['GENTACTIX_' + name] = str(out / directory)
    os.environ['GENTACTIX_ROLE_INDEX'] = str(out / 'roles.json')
    from backend import main as api, harness_engine as h
    from fastapi.testclient import TestClient
    checks = []
    summary = {'live_model_calls': True, 'stubbed': False, 'checks': checks, 'passed': False}
    client = TestClient(api.app)

    def call(name, method, path, payload=None):
        started = time.monotonic()
        response = client.request(method, path, json=payload)
        value = response.json()
        (out / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2))
        row = {'name': name, 'http': response.status_code, 'seconds': round(time.monotonic()-started, 2)}
        checks.append(row); print(json.dumps(row), flush=True)
        if response.status_code != 200: raise AssertionError(name + ' failed; inspect private evidence')
        return value

    try:
        call('connection', 'POST', '/api/llm/test')
        if args.resume:
            from backend import session_store
            sessions, errors = session_store.load_all()
            assert not errors and len(sessions) == 1
            api._SESSIONS.update(sessions)
            sid = next(iter(sessions))
            advanced = call('resumed', 'GET', f'/api/sessions/{sid}')
            assert advanced['turn_count'] == 2
            initial = json.loads((out/'init.json').read_text())
        else:
            sid, initial, advanced = setup_battle(call, api)
        world = copy.deepcopy(advanced['war_state'])
        chat = call('chat', 'POST', f'/api/sessions/{sid}/chat', {'message': '请简要说明当前补给、火力和地形限制，区分观测数据与假设。'})
        assert len(chat['chat_messages']) >= 2 and chat['chat_messages'][-1]['content']
        review = call('review', 'POST', f'/api/sessions/{sid}/review')
        assert review['review']['text']
        call('analysis', 'POST', f'/api/sessions/{sid}/analysis', {'explain': True})
        trace = call('trace', 'GET', f'/api/sessions/{sid}/traces/1')
        assert trace['persistence'] and all(r['status'] == 'verified' for r in trace['persistence'])
        unit = world['red_units'][0]['name']
        call('geometry', 'POST', f'/api/sessions/{sid}/counterfactual', {'turn': 1, 'mode': 'geometry', 'undo': [{'team': 'red', 'name': unit}]})
        call('paired-model', 'POST', f'/api/sessions/{sid}/counterfactual', {'turn': 1, 'mode': 'model', 'samples': 1, 'instruction': '红方本回合原地固守，保持坐标不变；其他条件与15分钟时长保持一致。'})
        final = call('export', 'GET', f'/api/sessions/{sid}/export')
        assert final['war_state'] == world and final['turn_count'] == 2
        call('checkpoint', 'POST', '/api/sessions/checkpoint')
        summary.update(passed=True, resumed=args.resume, time_before=initial['war_state']['current_time'], time_after=world['current_time'],
                       elapsed_seconds=1800, runtime=api.health()['runtime'], analysis_preserves_world=True)
    finally:
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        h.close_harness()
    return 0


def setup_battle(call, api):
    call('planner', 'POST', '/api/scenarios/plan', {'messages': [{'role': 'user', 'content': '请直接整理上甘岭1952年10月14日04:00的局部教学推演草案，15分钟一轮，观察坑道与补给，无需追问。'}]})
    battle = next(b['name'] for b in api.battles()['battles'] if '上甘岭' in b['name'])
    sid = call('create', 'POST', '/api/sessions', {
        'scenario': '上甘岭局部教学想定，1952年10月14日04:00。只设置红蓝双方各两个代表性连级单位（步兵与支援），控制在四个单位。观察597.9高地附近阵地对峙，15分钟内有限机动与补给，不预设战果。',
        'battle': battle, 'time_step': '15分钟', 'use_adjudicator': True,
        'use_rag': True, 'persist_roles': True, 'use_agent_tools': True, 'agent_mode': 'native'})['session_id']
    initial = call('init', 'POST', f'/api/sessions/{sid}/init')
    for turn in (1, 2):
        advanced = call(f'next-{turn}', 'POST', f'/api/sessions/{sid}/next')
        assert advanced['turn_count'] == turn
        assert advanced['war_state']['simulation_clock']['elapsed_seconds'] == turn*900
    return sid, initial, advanced

if __name__ == '__main__': sys.exit(main())
