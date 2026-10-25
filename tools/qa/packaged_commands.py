"""Check a running isolated frozen backend through HTTP, never import its source.

The backend must use disposable game/runtime directories. --live additionally
allows real DSH parsing and one native unit turn with the configured provider.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18731')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.url.startswith('http://127.0.0.1:'):
        parser.error('Use an isolated loopback backend')
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {'passed': False, 'live': args.live, 'checks': []}
    started = time.monotonic()

    def request(method, path, body=None, expected=200):
        encoded = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(args.url + path, data=encoded, method=method,
                                     headers={'Content-Type': 'application/json', 'Origin': 'null'})
        try:
            with urllib.request.urlopen(req, timeout=900) as response:
                status, content = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, content = exc.code, exc.read()
        value = json.loads(content)
        if status != expected:
            raise RuntimeError(f'{method} {path}: HTTP {status}, expected {expected}: {str(value)[:1000]}')
        return value

    def record(name, **extra):
        summary['checks'].append({'name': name, **extra})
        (args.out / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(name, flush=True)

    def revision(view):
        return {'expected_turn': view['turn'], 'revision': view['revision'], 'world_version': view['world_version']}

    try:
        schema = request('GET', '/openapi.json')
        assert schema['info']['version'] == '3.3.0', schema['info']
        assert '/api/sessions/{sid}/commands' in schema['paths']
        assert '/api/sessions/import/preview' in schema['paths']
        assert len(request('GET', '/api/battles')['battles']) == 45
        assert len(request('GET', '/api/missions')['missions']) == 20
        record('frozen_version_routes_and_catalog')

        restored = request('GET', '/api/sessions')['sessions']
        for saved in restored:
            view = request('GET', f"/api/sessions/{saved['session_id']}/commands")
            assert view['turn'] == saved['turn_count'] and view['units']
        record('copied_existing_games_restore', games=len(restored))

        sid = request('POST', '/api/sessions', {'scenario': '安装包隔离验收，非历史战况',
            'mission_id': 'AJ15-local-v1', 'persist_roles': False, 'use_adjudicator': True})['session_id']
        path = '/api/sessions/' + sid
        request('POST', path + '/init')
        view = request('GET', path + '/commands')
        view = request('PUT', path + '/commands', {**revision(view), 'director': True,
            'orders': [{'unit_id': u['id'], 'action': 'hold'} for u in view['units']]})
        payload = {**revision(view), 'batch_id': uuid.uuid4().hex}
        first = request('POST', path + '/next', payload)
        repeated = request('POST', path + '/next', payload)
        assert first['turn_count'] == repeated['turn_count'] == 1
        assert repeated['command_result']['replayed']
        rows = request('GET', path + '/unit-decisions')
        assert rows['total'] == 6 and all(r['source'] == 'human' for r in rows['items'])
        request('PUT', path + '/commands', {**revision(view), 'orders': []}, expected=409)
        record('manual_commands_receipts_and_idempotency', units=rows['total'])

        exported = request('GET', path + '/export')
        assert request('POST', '/api/sessions/import/preview', exported)['conflict']
        request('DELETE', path)
        preview = request('POST', '/api/sessions/import/preview', exported)
        assert not preview['conflict'] and preview['turn_count'] == 1
        request('POST', '/api/sessions/import', exported)
        assert request('GET', path)['war_state'] == first['war_state']
        record('frozen_preview_confirmed_import_roundtrip')

        if args.live:
            view = request('GET', path + '/commands'); unit = view['units'][0]
            draft = request('POST', path + '/commands/resolve', {**revision(view),
                'text': '本单位仅下一回合原地待命，不改变其他单位命令。',
                'selected_units': [unit['id']], 'map_target': None})
            assert draft['status'] == 'draft' and draft['orders']
            assert draft['orders'][0]['unit_id'] == unit['id'] and draft['orders'][0]['action'] == 'hold'
            record('real_frozen_dsh_natural_language_draft')
            view = request('PUT', path + '/commands', {**revision(view), 'director': True,
                'orders': [{'unit_id': u['id'], 'action': 'hold'} for u in view['units'] if u['id'] != unit['id']],
                'unit_agents': [unit['id']],
                'intent': '本单位先调用原生get_observation核对当前粮食库存和战区时间，再选择hold待命。公开依据写出观察到的库存和时间。'})
            second = request('POST', path + '/next', {**revision(view), 'batch_id': uuid.uuid4().hex})
            assert second['turn_count'] == 2
            rows = request('GET', path + '/unit-decisions?start_turn=2&end_turn=2')['items']
            native = [r for r in rows if r['source'] == 'native_unit_agent']
            assert len(native) == 1 and native[0]['dsh_event_ref'] and native[0]['run_ids']
            exported = request('GET', path + '/export')
            runs = exported['trajectory'][-1]['runs']
            assert any(e.get('type') == 'tool/result' for run in runs for e in run['events'])
            record('real_frozen_native_unit_and_observation_tool', native_units=len(native))
        summary['passed'] = True
    except Exception as exc:
        summary['error'] = str(exc)
        raise
    finally:
        summary['seconds'] = round(time.monotonic() - started, 2)
        (args.out / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
