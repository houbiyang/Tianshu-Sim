"""Real DSH command/Agent acceptance. Explicit --live; never writes user games/config."""
import argparse
from pathlib import Path
import os, sys, json, time, copy, threading
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live', action='store_true', help='Allow real configured model calls')
parser.add_argument('--out', type=Path, default=Path('.cache/qa/commands-live'))
args = parser.parse_args()
if not args.live:
    parser.error('Real model calls require --live')
root = Path(__file__).resolve().parents[2]
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
if (out / 'result.json').exists():
    import shutil
    shutil.copy2(out / 'result.json', out / ('attempt-' + str(time.time_ns()) + '.json'))
for key, value in [('SESSION_DIR', 'sessions'), ('TRACE_DIR', 'traces'), ('DSH_HOME', 'home'), ('DSH_WORKSPACE', 'workspace'), ('ROLE_INDEX', 'roles.json'), ('PROJECT_ROOT', 'project')]:
    os.environ['GENTACTIX_' + key] = str(out / value)
os.environ['GENTACTIX_LLM_CONFIG'] = str(root / '.gentactix-llm.enc')
os.environ['GENTACTIX_VAULT_KEY_FILE'] = str(Path.home() / '.config/GenTactix-DSH/vault.key')
sys.path.insert(0, str(root))
from fastapi.testclient import TestClient
from backend import main, harness_engine as h, session_store
from gentactix import missions, battlefield
summary = {'passed': False, 'checks': [], 'errors': []}
start = time.monotonic()

def note(name, **kw):
    summary['checks'].append({'name': name, **kw})
    (out / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(name, flush=True)

def ok(response):
    if response.status_code != 200:
        raise RuntimeError(str(response.status_code) + ':' + response.text[:1500])
    return response.json()
try:
    client = TestClient(main.app)
    spec = missions.definition('AJ15-local-v1')
    sid = ok(client.post('/api/sessions', json={'scenario': spec['title'], 'mission_id': spec['id'], 'battle': spec['battle'], 'simulation_mode': 'open', 'persist_roles': False, 'agent_mode': 'json', 'use_rag': True, 'use_adjudicator': True}))['session_id']
    path = '/api/sessions/' + sid
    ok(client.post(path + '/init'))

    def view():
        return ok(client.get(path + '/commands'))

    def rev(v):
        return {'expected_turn': v['turn'], 'revision': v['revision'], 'world_version': v['world_version']}
    v = view()
    u = v['units'][0]
    autonomous_ids = {u['id'], v['units'][1]['id']}
    parsed = ok(client.post(path + '/commands/resolve', json={**rev(v), 'text': '本单位前往选定位置，仅下一回合；保持其他已下达命令。', 'selected_units': [u['id']], 'map_target': u['position']}))
    assert parsed['status'] == 'draft' and parsed['orders'][0]['destination'] == u['position']
    assert not view()['enabled']
    note('real_natural_language_map_draft', calls=len(main._SESSIONS[sid]['command_proposals'][parsed['id']]['runs']))

    def queue(v):
        return ok(client.put(path + '/commands', json={**rev(v), 'orders': [{'unit_id': x['id'], 'action': 'hold'} for x in v['units'] if x['id'] not in autonomous_ids], 'director': True, 'unit_agents': list(autonomous_ids), 'intent': '本轮本单位先调用原生get_observation核对当前粮食库存和战区时间，再选择hold待命。公开依据写出观察到的当前粮食余额和战区时间。'}))
    v = queue(view())
    request = {**rev(v), 'batch_id': 'live-first'}
    first = ok(client.post(path + '/next', json=request))
    s = main._SESSIONS[sid]
    binding = s['command_state']['agent_sessions'][u['id']]
    trace = s['trajectory'][-1]
    native_rows = [d for d in trace['decisions'] if d['source'] == 'native_unit_agent']
    assert len(native_rows) == 2
    assert len({d['dsh_event_ref']['session_id'] for d in native_rows}) == 2
    native = native_rows[0]
    assert native['dsh_event_ref']
    assert all((d['dsh_event_ref'] is None for d in trace['decisions'] if d['source'] == 'human'))
    assert any((e.get('type') == 'tool/result' for r in trace['runs'] for e in r['events'])), trace['runs']
    note('real_native_unit_with_tool', unit_sessions=[d['dsh_event_ref']['session_id'] for d in native_rows], session_id=binding['session_id'], unit_agents=len(native_rows), event_count=sum((r['event_count'] for r in trace['runs'])), rationale=native['rationale'], policy=h.role_policy('engine', tools=True))
    retry = ok(client.post(path + '/next', json=request))
    assert retry['turn_count'] == 1 and retry['command_result']['replayed']
    note('real_batch_retry_no_duplicate')
    h.close_harness()
    restored, errors = session_store.load_all()
    assert not errors
    main._SESSIONS[sid] = restored[sid]
    v = queue(view())
    second = ok(client.post(path + '/next', json={**rev(v), 'batch_id': 'live-second'}))
    assert main._SESSIONS[sid]['command_state']['agent_sessions'][u['id']]['session_id'] == binding['session_id']
    note('real_restart_resume_same_native_unit', rationale=next((d['rationale'] for d in main._SESSIONS[sid]['trajectory'][-1]['decisions'] if d['source'] == 'native_unit_agent')))
    v = queue(view())
    before = copy.deepcopy(main._SESSIONS[sid]['war_state'])
    queue_before = copy.deepcopy(main._SESSIONS[sid]['command_state'])
    response = []
    worker = threading.Thread(target=lambda: response.append(client.post(path + '/next', json={**rev(v), 'batch_id': 'live-cancel'})))
    worker.start()
    deadline = time.monotonic() + 30
    cancelled = None
    while worker.is_alive() and time.monotonic() < deadline:
        observer = main._SESSIONS[sid].get('active_observer')
        if observer and observer.active_sessions:
            cancelled = ok(client.post(path + '/cancel'))
            break
        time.sleep(0.03)
    worker.join(timeout=60)
    assert not worker.is_alive(), 'cancel did not stop native run in60s'
    assert cancelled and response[0].status_code == 409, response[0].text if response else None
    assert main._SESSIONS[sid]['war_state'] == before and main._SESSIONS[sid]['command_state'] == queue_before
    note('real_inflight_cancel', runtime=cancelled['runtime'], status=response[0].status_code)
    retried = ok(client.post(path + '/next', json={**rev(v), 'batch_id': 'live-cancel'}))
    assert retried['turn_count'] == 3
    note('real_retry_after_native_cancel', turn=retried['turn_count'])
    v = queue(view())
    v = ok(client.put(path + '/commands', json={**rev(v), 'orders': [
        {k: order[k] for k in ('id', 'unit_id', 'action', 'duration') if k in order} for order in v['orders']],
        'director': True, 'unit_agents': list(autonomous_ids), 'reset_agents': [u['id']],
        'intent': '本轮先调用get_observation核对实际库存，再待命；使用当前观察，不继承已取消的候选。'}))
    final = ok(client.post(path + '/next', json={**rev(v), 'batch_id': 'live-reset'}))
    assert final['turn_count'] == 4 and final['war_state']['mission']['status'] == 'completed'
    assert final['war_state']['current_time'] == '1942-05-01 10:00'
    new_binding = main._SESSIONS[sid]['command_state']['agent_sessions'][u['id']]
    assert new_binding['session_id'] != binding['session_id']
    assert client.post(path + '/next', json={**rev(view()), 'batch_id': 'terminal-reject'}).status_code == 422
    for trace in main._SESSIONS[sid]['trajectory'][1:]:
        replayed = missions.advance(trace['before'], trace['after']['mission']['last_orders'], trace['time_step'])
        battlefield.carry_battlefield_state(trace['before'], replayed)
        assert replayed['mission'] == trace['after']['mission']
        for team in ('red', 'blue'):
            assert replayed[team + '_units'] == trace['after'][team + '_units']
    note('real_explicit_memory_reset_and_terminal', turn=4, current_time=final['war_state']['current_time'],
         replayed_turns=4, old_session=binding['session_id'], new_session=new_binding['session_id'])
    open_id = ok(client.post('/api/sessions', json={'scenario':
        '合成教学测试，无历史对应战役：2026-10-24 08:00，山西高平35.81N112.84E附近陆地，'
        '红方一个步兵连100人，蓝方一个步兵连100人，双方相距2公里，无其他编组。'
        '双方各2件教学装备；粮食弹药充足。本次只测试队列的位置履行，不发生交战，不产生损失。',
        'time_step': '15分钟', 'simulation_mode': 'open', 'persist_roles': False, 'use_adjudicator': True}))['session_id']
    open_path = '/api/sessions/' + open_id
    open_initial = ok(client.post(open_path + '/init'))
    open_view = ok(client.get(open_path + '/commands'))
    chosen = next(unit for unit in open_view['units'] if unit['team'] == 'red' and unit['position'] and unit['controllable'])
    destination = {**chosen['position'], 'lat': chosen['position']['lat'] + .0002}
    orders = [{'unit_id': unit['id'], 'action': 'move' if unit['id'] == chosen['id'] else 'hold',
               **({'destination': destination} if unit['id'] == chosen['id'] else {})}
              for unit in open_view['units'] if unit['available'] and not unit['ambiguous']]
    open_view = ok(client.put(open_path + '/commands', json={**rev(open_view), 'orders': orders, 'director': True, 'no_fire': True}))
    opened = ok(client.post(open_path + '/next', json={**rev(open_view), 'batch_id': 'ordinary-move',
        'instruction': '严格履行已确认队列：移动单位采用给定精确落点，其余待命；不改变人数、装备或库存，无交战。'}))
    actual = next(unit for unit in opened['war_state']['red_units'] if unit['name'] == chosen['name'])
    from gentactix import commands
    assert commands.distance(commands.unit_position(actual), destination) <= .01
    assert opened['turn_count'] == 1
    note('real_ordinary_open_move', turn=1, unit=chosen['name'],
         distance_km=commands.distance(chosen['position'], commands.unit_position(actual)),
         outcome_basis='movement_verified_other_effects_model_estimated')
    summary['passed'] = True
except Exception as exc:
    summary['errors'].append(str(exc)[:2500])
    raise
finally:
    h.close_harness()
    summary['seconds'] = round(time.monotonic() - start, 2)
    (out / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
