"""Cancel a real DSH run and verify atomic rollback; needs --live and a QA export."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--session-export', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT/'.cache/qa/live-cancel')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    for name, folder in [('SESSION_DIR', 'sessions'), ('TRACE_DIR', 'traces'), ('DSH_HOME', 'dsh'), ('DSH_WORKSPACE', 'workspace')]:
        os.environ['GENTACTIX_'+name] = str(out/folder)
    os.environ['GENTACTIX_ROLE_INDEX'] = str(out/'roles.json')
    from backend import main as api, harness_engine as h
    from fastapi.testclient import TestClient
    client = TestClient(api.app)
    data = json.loads(args.session_export.read_text())
    try:
        response = client.post('/api/sessions/import', json=data)
        assert response.status_code == 200
        sid = response.json()['session_id']
        before = client.get(f'/api/sessions/{sid}/export').json()
        h.get_harness()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f'/api/sessions/{sid}/review')
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                observer = api._SESSIONS[sid].get('active_observer')
                if observer and observer.active_sessions and observer._events:
                    # Let the underlying request actually enter generation.
                    time.sleep(.5)
                    break
                if pending.done(): raise AssertionError('Generation finished before cancellation could be tested')
                time.sleep(.05)
            else: raise AssertionError('No active runtime run')
            started = time.monotonic()
            cancelled = client.post(f'/api/sessions/{sid}/cancel').json()
            response = pending.result(timeout=30)
        after = client.get(f'/api/sessions/{sid}/export').json()
        for field in ('war_state', 'turn_count', 'history_logs', 'review'):
            assert before[field] == after[field], field
        assert response.status_code == 409, response.text
        assert any(row.get('ok') for row in cancelled['runtime']), 'Native cancel was not acknowledged'
        summary = {'live_model_calls': True, 'native_cancel_acknowledged': True,
                   'cancel_seconds': round(time.monotonic()-started, 3), 'http': response.status_code,
                   'state_preserved': True, 'progress_cleared': not client.get(f'/api/sessions/{sid}/progress').json()['active']}
        (out/'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    finally: h.close_harness()


if __name__ == '__main__': main()
