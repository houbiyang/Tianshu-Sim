"""Offline integration of plugin commits, trace evidence and causal endpoints."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from backend import main, harness_engine as h, extensions
    from fastapi.testclient import TestClient
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def initial():
    return {'current_time': '1952年10月14日04:00', 'situation_report': '测试部署',
            'red_units': [{'name': '甲', 'type': '徒步步兵', 'hp': 100, 'lat': 38.325, 'lng': 127.466}],
            'blue_units': [{'name': '乙', 'type': '徒步步兵', 'hp': 100, 'lat': 38.320, 'lng': 127.466}]}


def advanced(time='1952年10月14日04:15'):
    state = initial(); state['current_time'] = time; state['red_units'][0]['lat'] = 38.323
    return {'updated_state': state, 'report': 'FUTURE-OUTCOME-ONLY',
            'decisions': [{'team': 'red', 'unit_name': '甲', 'action': '接近', 'rationale': '缩短距离'}]}


@unittest.skipUnless(HAS_SDK, 'Run in wargame: Harness SDK is not installed in the legacy venv')
class RuntimePluginTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'GENTACTIX_TRACE_DIR': self.directory.name, 'GENTACTIX_SESSION_DIR': self.directory.name + '/sessions'}); self.env.start()
        self.responses = []; self.prompts = []
        self.mock = patch.object(h, 'complete_ex', side_effect=self.complete); self.mock.start()
        self.persist = patch.object(h, 'PERSIST_ROLES', False); self.persist.start()
        h.RUNTIME_STATUS['connected'] = False
        main._SESSIONS.clear(); h.ENGINE_NOTICES.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.mock.stop(); self.persist.stop(); self.env.stop(); self.directory.cleanup(); main._SESSIONS.clear()

    def test_trace_waits_for_buffered_tail_and_checks_event_type(self):
        from backend.analysis_routes import verify_dsh_records
        path = Path(self.directory.name) / 'sessions/workspace/trace-test/session.v4.jsonl'
        path.parent.mkdir(parents=True)
        event = {'seq': 7, 'type': 'turn/end'}
        record = {'runs': [{'id': 'run', 'session_id': 'trace-test', 'last_seq': 7, 'events': [event]}]}
        with patch.object(h, 'DSH_HOME', self.directory.name):
            path.write_text('{"seq":7')
            with patch('backend.analysis_routes.time.sleep', side_effect=lambda _: path.write_text(json.dumps(event) + '\n')):
                self.assertEqual(verify_dsh_records(record)[0]['status'], 'verified')
            path.write_text(json.dumps({'seq': 7, 'type': 'user/message'}) + '\n')
            self.assertEqual(verify_dsh_records(record, flush_timeout=0)[0]['status'], 'partial')
            path.write_text('not-json\n')
            self.assertEqual(verify_dsh_records(record, flush_timeout=0)[0]['status'], 'corrupt')
            path.write_text('')
            self.assertEqual(verify_dsh_records(record, flush_timeout=0)[0]['found_events'], 0)

    def response(self, payload, sid):
        text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
        return SimpleNamespace(final_response=text, finish_reason='completed', session_id=sid,
            events=[{'type': 'turn/start', 'seq': 0, 'data': {'turn': 1}},
                    {'type': 'assistant/message', 'seq': 1, 'data': {'message': {'content': [{'type': 'text', 'text': text}]}}},
                    {'type': 'turn/end', 'seq': 2, 'data': {'reason': 'completed'}}], notifications=[])

    def complete(self, prompt, *, session_id=None, on_notification=None):
        self.prompts.append(prompt)
        value = self.responses.pop(0)
        if callable(value): return value()
        return self.response(value, session_id)

    def create(self):
        sid = self.client.post('/api/sessions', json={'scenario': '近距离测试', 'time_step': '15分钟', 'use_adjudicator': True, 'persist_roles': False}).json()['session_id']
        self.responses.append(initial())
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/init').status_code, 200)
        return sid

    def test_restored_snapshot_exposes_actual_session_controls(self):
        sid = self.create()
        main._SESSIONS[sid].update(use_agent_tools=True, persist_roles=True,
                                   agent_mode='native', use_live_intel=False)
        snap = self.client.get(f'/api/sessions/{sid}').json()
        self.assertEqual(snap['time_step'], '15分钟')
        self.assertTrue(snap['adjudication']['enabled'])
        self.assertEqual(snap['settings'], {'use_agent_tools': True, 'persist_roles': True,
                                          'agent_mode': 'native', 'use_live_intel': False})

    def advance(self, sid):
        self.responses.append(advanced())
        response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_committed_trace_has_decision_and_exact_event_reference(self):
        sid = self.create(); self.advance(sid)
        record = main._SESSIONS[sid]['trajectory'][1]
        self.assertEqual(record['turn'], 1)
        self.assertEqual(record['decisions'][0]['dsh_event_ref']['seq'], 1)
        self.assertEqual(record['runs'][0]['last_seq'], 2)
        self.responses.append('not json')
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/next').status_code, 502)
        self.assertEqual(len(main._SESSIONS[sid]['trajectory']), 2)

    def test_trace_storage_failure_prevents_battle_commit(self):
        sid = self.create(); before = copy.deepcopy(main._SESSIONS[sid]['war_state'])
        self.responses.append(advanced())
        with patch.dict(extensions.registry.plugins['trajectory'], {'before_commit': lambda c: (_ for _ in ()).throw(OSError('disk unavailable'))}):
            response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(main._SESSIONS[sid]['war_state'], before)
        self.assertEqual(main._SESSIONS[sid]['turn_count'], 0)

    def test_geometry_and_export_keep_original_world(self):
        sid = self.create(); snap = self.advance(sid); before = copy.deepcopy(snap['war_state'])
        result = self.client.post(f'/api/sessions/{sid}/counterfactual', json={'turn': 1, 'undo': [{'team': 'red', 'name': '甲'}]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['read_only'])
        self.assertEqual(main._SESSIONS[sid]['war_state'], before)
        exported = self.client.get(f'/api/sessions/{sid}/export').json()
        self.assertEqual(len(exported['trajectory']), 2)
        self.assertEqual(len(exported['counterfactuals']), 1)

    def test_fabricated_model_evidence_is_not_adopted(self):
        sid = self.create(); self.advance(sid)
        self.responses.append({'summary': '错误归因', 'hypotheses': [{'claim': '错误', 'evidence_ids': ['invented']}]})
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/analysis', json={'explain': True}).status_code, 502)
        self.assertIsNone(main._SESSIONS[sid]['causal_analysis'])

    def test_paired_native_forks_share_predecision_parent_and_no_future(self):
        sid = self.create(); snap = self.advance(sid); calls = []
        def fork(parent, prompt, observer, **kwargs):
            calls.append((copy.deepcopy(parent), prompt, kwargs))
            result = self.response(advanced(), f'gentactix-branch-{len(calls)}')
            observer.record_result(result, parent=parent)
            return result
        with patch.object(h, 'fork_run', side_effect=fork):
            response = self.client.post(f'/api/sessions/{sid}/counterfactual', json={'turn': 1, 'mode': 'model', 'instruction': '保持原位', 'samples': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0][0], calls[1][0])
        for _, prompt, kwargs in calls:
            self.assertNotIn('FUTURE-OUTCOME-ONLY', prompt)
            self.assertEqual(kwargs['context']['session']['war_state'], main._SESSIONS[sid]['trajectory'][0]['after'])
        self.assertEqual(main._SESSIONS[sid]['war_state'], snap['war_state'])

    def test_bad_clock_branch_not_counted_as_admissible(self):
        sid = self.create(); self.advance(sid)
        def fork(parent, prompt, observer, **kwargs):
            result = self.response(advanced('1952年10月14日05:45'), 'gentactix-bad-clock')
            observer.record_result(result); return result
        with patch.object(h, 'fork_run', side_effect=fork):
            response = self.client.post(f'/api/sessions/{sid}/counterfactual', json={'turn': 1, 'mode': 'model', 'instruction': '保持原位'})
        self.assertEqual(response.status_code, 422)
        self.assertIn('时间推进冲突', response.text)

    def test_failed_reinit_keeps_epoch_and_trace(self):
        sid = self.create(); self.responses.append('not json')
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/init').status_code, 502)
        self.assertEqual(main._SESSIONS[sid]['role_epoch'], 0)
        self.assertEqual(len(main._SESSIONS[sid]['trajectory']), 1)

    def test_native_tools_use_detached_context(self):
        session = {'war_state': initial(), 'use_rag': False, 'trajectory': [], 'history': []}
        before = copy.deepcopy(session)
        context = {'session': session, 'pre_adj': None}
        result = main._native_tool_dispatch(context, {'tool': 'evaluate_action', 'args': {'moves': [{'name': '甲', 'lat': 38.324, 'lng': 127.466}]}})
        self.assertTrue(result['ok'])
        self.assertEqual(session, before)

    def test_cancellation_rejects_returned_candidate(self):
        sid = self.create(); before = copy.deepcopy(main._SESSIONS[sid]['war_state'])
        def cancelled():
            self.client.post(f'/api/sessions/{sid}/cancel')
            return self.response(advanced(), 'gentactix-cancelled')
        self.responses.append(cancelled)
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/next').status_code, 409)
        self.assertEqual(main._SESSIONS[sid]['war_state'], before)

    def test_missing_turns_and_invalid_samples_rejected(self):
        sid = self.create()
        self.assertEqual(self.client.get(f'/api/sessions/{sid}/traces/99').status_code, 404)
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/counterfactual', json={'turn': 1, 'mode': 'model', 'samples': 10}).status_code, 422)

    def test_no_reasoning_or_credentials_in_public_execution_evidence(self):
        event = {'seq': 1, 'type': 'assistant/message', 'data': {'message': {'content': [{'type': 'reasoning', 'text': 'PRIVATE'}]}}}
        out = extensions.event_summary(event, 'gentactix-safe')
        self.assertNotIn('PRIVATE', str(out))
        self.assertNotIn('abc', str(extensions.safe({'api_key': 'abc', 'reasoning': 'abc'})))

    def test_progress_is_read_only_and_does_not_drain_notices(self):
        sid = self.create(); session = main._SESSIONS[sid]
        observer = h.RunObserver('test progress'); observer.progress.update(steps=4, tokens=1200)
        session.update(active_observer=observer, progress=observer.progress)
        h.ENGINE_NOTICES.append({'level': 'info', 'message': 'keep'})
        result = self.client.get(f'/api/sessions/{sid}/progress').json()
        self.assertTrue(result['active'])
        self.assertEqual(result['progress']['steps'], 4)
        self.assertEqual(h.ENGINE_NOTICES[-1]['message'], 'keep')

    def test_role_budget_controls_are_independent(self):
        # Test environment defaults without the user's encrypted role overrides.
        with patch.object(h, 'ROLE_POLICIES', {}), patch.object(h, 'LLM_MAX_TOKENS', 65536), \
             patch.dict(os.environ, {'GENTACTIX_CHAT_MAX_TOKENS': '1024', 'GENTACTIX_CHAT_REASONING_EFFORT': 'low'}):
            policy = h.role_policy('chat')
            self.assertEqual(policy['max_tokens'], 1024)
            self.assertEqual(policy['reasoning_effort'], 'low')
            self.assertNotEqual(h.role_policy('engine')['max_tokens'], 1024)


if __name__ == '__main__': unittest.main(verbosity=2)
