"""Release regressions: untrusted inputs, concurrency, durable state and a 20-turn soak.

Only the model transport is scripted. Use the desktop Python environment; this
suite intentionally fails rather than silently skipping when dependencies break.
"""
import copy
import importlib.util
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from unittest.mock import Mock

HAS_SDK = importlib.util.find_spec('deepseek_harness') is not None
if HAS_SDK:
    import verify_desktop_backend as fixtures
    from backend import main, harness_engine as h, session_store


@unittest.skipUnless(HAS_SDK, 'Requires the desktop Python dependencies')
class ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixtures.DesktopBackendTest.setUp(self)
        self.trace_dir = tempfile.TemporaryDirectory()
        self.trace_env = patch.dict('os.environ', {'GENTACTIX_TRACE_DIR': self.trace_dir.name})
        self.trace_env.start()

    def tearDown(self):
        self.trace_env.stop()
        self.trace_dir.cleanup()
        fixtures.DesktopBackendTest.tearDown(self)

    def _fake_complete_ex(self, *args, **kwargs):
        return fixtures.DesktopBackendTest._fake_complete_ex(self, *args, **kwargs)

    def _create(self):
        return fixtures.DesktopBackendTest._create(self)

    def _init(self, sid):
        return fixtures.DesktopBackendTest._init(self, sid)

    def test_remote_websites_cannot_read_or_mutate_local_sessions(self):
        sid = self._create()
        for origin in ('https://untrusted.example', 'http://localhost.evil.example', 'file://evil', 'https://127.0.0.1@evil.example'):
            for method, path in (('GET', '/api/sessions'), ('POST', f'/api/sessions/{sid}/cancel'), ('DELETE', f'/api/sessions/{sid}')):
                response = self.client.request(method, path, headers={'Origin': origin})
                self.assertEqual(response.status_code, 403, (method, origin))
                self.assertNotEqual(response.headers.get('access-control-allow-origin'), '*')
        self.assertIn(sid, main._SESSIONS)
        self.assertFalse(main._SESSIONS[sid]['cancel_requested'])

    def test_desktop_and_loopback_origins_still_work_and_rebinding_is_rejected(self):
        for origin in ('null', 'http://127.0.0.1:5173', 'http://localhost:8784', 'http://[::1]:5173'):
            response = self.client.get('/api/health', headers={'Origin': origin})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get('access-control-allow-origin'), origin)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'evil.example:8700'}).status_code, 403)

    def test_bad_imports_are_422_and_never_create_a_session(self):
        sid = self._create()
        self._init(sid)
        exported = self.client.get(f'/api/sessions/{sid}/export').json()
        base = exported['session']; base['id'] = 'f' * 12
        invalid = [{'export': []}, {'export': None}, {'snapshot': []}, {'snapshot': {'session_id': 'f'*12, 'scenario': 'x'}}]
        for key in ('adjudication', 'rag'):
            invalid.append({'snapshot': {'session_id': 'f'*12, 'scenario': 'x', 'time_step': '15分钟', key: []}})
        for field, value in (('turn_count', 'bad'), ('turn_count', -1), ('turn_count', True), ('history', [3]), ('chat_messages', {}), ('trajectory', [3]), ('war_state', {}), ('war_state', {'current_time': 'now', 'red_units': [3], 'blue_units': []})):
            invalid.append({'format': 'gentactix-session-v1', 'session': {**base, field: value}})
        for payload in invalid:
            response = self.client.post('/api/sessions/import', json=payload)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(set(main._SESSIONS), {sid})

    def test_checkpoint_restore_quarantines_invalid_state_without_deleting_file(self):
        sid = self._create(); self._init(sid)
        session_store.save(main._SESSIONS[sid])
        path = session_store.directory() / ('f'*12 + '.json')
        broken = copy.deepcopy(main._SESSIONS[sid]); broken.update(id='f'*12, turn_count='bad')
        path.write_text(json.dumps({'format': 'gentactix-session-v1', 'session': session_store.serializable(broken)}))
        sessions, errors = session_store.load_all()
        self.assertEqual(set(sessions), {sid})
        self.assertEqual(errors, [path.name]); self.assertTrue(path.exists())
        broken.pop('review_text')
        path.write_text(json.dumps({'format': 'gentactix-session-v1', 'session': session_store.serializable(broken)}))
        self.assertEqual(session_store.load_all()[1], [path.name])

    def test_parallel_checkpoint_writes_remain_parseable(self):
        sid = self._create(); self._init(sid)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: session_store.save(main._SESSIONS[sid]), range(30)))
        sessions, errors = session_store.load_all()
        self.assertEqual(errors, [])
        self.assertEqual(sessions[sid]['war_state'], main._SESSIONS[sid]['war_state'])

    def test_reasoning_chat_defaults_fit_budget_and_explicit_policy_wins(self):
        with patch.object(h, 'ROLE_POLICIES', {}), patch.object(h, 'LLM_MAX_TOKENS', 16384), patch.dict('os.environ', {}, clear=True):
            self.assertEqual(h.role_policy('chat')['max_tokens'], 8192)
            self.assertEqual(h.role_policy('chat')['reasoning_effort'], 'low')
            with patch.object(h, 'ROLE_POLICIES', {'chat': {'max_tokens': 1024, 'reasoning_effort': None}}):
                self.assertEqual(h.role_policy('chat')['max_tokens'], 1024)
                self.assertIsNone(h.role_policy('chat')['reasoning_effort'])

    def test_missing_fork_parent_is_never_recreated_after_restart(self):
        harness = Mock()
        harness.resume_session.side_effect = RuntimeError('missing exact parent history')
        with patch.object(h, 'get_harness', return_value=harness), patch.object(h, 'NATIVE_RUNTIME', True), patch.dict(h.RUNTIME_STATUS, {'connected': True}):
            with self.assertRaisesRegex(RuntimeError, 'missing exact parent'):
                h.fork_run({'session_id': 'gentactix-missing-engine', 'last_seq': 13}, 'branch', h.RunObserver('test'))
        harness.start_session.assert_not_called()
        self.assertNotIn('gentactix-missing-engine', h._role_sessions)

    def test_busy_operations_fail_immediately_while_health_and_cancel_remain_available(self):
        sid = self._create(); self._init(sid)
        main._OP_LOCK.acquire()
        try:
            for path in (f'/api/sessions/{sid}/next', f'/api/sessions/{sid}/review', '/api/sessions/checkpoint'):
                self.assertEqual(self.client.post(path).status_code, 409)
            self.assertEqual(self.client.get('/api/health').status_code, 200)
            self.assertEqual(self.client.get(f'/api/sessions/{sid}/progress').status_code, 200)
            self.assertEqual(self.client.post(f'/api/sessions/{sid}/cancel').status_code, 200)
        finally:
            main._OP_LOCK.release()

    def test_inflight_cancel_keeps_world_and_records_failure(self):
        sid = self._create(); self._init(sid)
        before = copy.deepcopy(main._SESSIONS[sid]['war_state'])
        entered, release = threading.Event(), threading.Event()
        original = h.complete_ex
        self.responses.append(json.dumps({'updated_state': fixtures._init_state(30), 'report': 'cancel fixture'}))
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5): raise AssertionError('cancel test timed out')
            return original(*args, **kwargs)
        with patch.object(h, 'complete_ex', slow), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, f'/api/sessions/{sid}/next')
            self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(self.client.get(f'/api/sessions/{sid}/progress').json()['active'])
                self.assertEqual(self.client.post(f'/api/sessions/{sid}/cancel').status_code, 200)
            finally: release.set()
            self.assertEqual(future.result(timeout=5).status_code, 409)
        self.assertEqual(main._SESSIONS[sid]['war_state'], before)
        self.assertEqual(main._SESSIONS[sid]['turn_count'], 0)
        self.assertFalse(self.client.get(f'/api/sessions/{sid}/progress').json()['active'])

    def test_20_turn_soak_preserves_roster_clock_trace_and_export(self):
        sid = self._create(); initial = self._init(sid)
        for turn in range(1, 21):
            self.responses.append(json.dumps({'updated_state': fixtures._init_state(turn*30), 'report': f'fixture turn {turn}'}))
            result = self.client.post(f'/api/sessions/{sid}/next')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['turn_count'], turn)
        exported = self.client.get(f'/api/sessions/{sid}/export').json()
        self.assertEqual(exported['war_state']['simulation_clock']['elapsed_seconds'], 36000)
        self.assertEqual(len(exported['trajectory']), 21)
        for team in ('red_units', 'blue_units'):
            self.assertEqual([u['name'] for u in exported['war_state'][team]], [u['name'] for u in initial['war_state'][team]])
        self.client.delete(f'/api/sessions/{sid}')
        self.assertEqual(self.client.post('/api/sessions/import', json=exported).status_code, 200)
        self.assertEqual(self.client.get(f'/api/sessions/{sid}/export').json()['war_state'], exported['war_state'])


if __name__ == '__main__': unittest.main(verbosity=2)
