"""DSH settings, secret boundaries, clock submission and battle restoration regressions."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend import llm_settings, session_store

try:
    from backend import main, harness_engine as h
    from fastapi.testclient import TestClient
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def config(**changes):
    return dict(base_url='http://127.0.0.1:8000/v1', model='test-model', api_key='fixture-placeholder',
                max_tokens=2048, context_window=16384, timeout_seconds=60, **changes)


class SettingsTests(unittest.TestCase):
    def test_bad_endpoint_and_budget_rejected(self):
        for url in ('file:///tmp/model', 'https://user:pass@example.org/v1', 'https://example.org/v1?key=test'):
            value = config(); value['base_url'] = url
            with self.assertRaises(ValueError): llm_settings.validate(value)
        value = config(); value['max_tokens'] = 32768
        with self.assertRaises(ValueError): llm_settings.validate(value)

    def test_secret_saved_privately_and_not_in_public_config(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(LLM_BASE_URL='http://127.0.0.1:8000/v1', LLM_MODEL='old',
            LLM_API_KEY='fixture-only-secret', LLM_MAX_TOKENS=2048, CONTEXT_WINDOW=16384,
            REQUEST_TIMEOUT_SECONDS=60, close_harness=lambda: None)
        with tempfile.TemporaryDirectory() as directory, patch.object(llm_settings, 'CONFIG_PATH', Path(directory) / 'llm.enc'), \
             patch.object(llm_settings, 'KEY_PATH', Path(directory) / 'user' / 'vault.key'):
            value = config(); value['api_key'] = None
            result = llm_settings.apply(fake, value)
            self.assertNotIn('fixture-only-secret', json.dumps(result))
            self.assertTrue(result['api_key_configured'])
            self.assertEqual(llm_settings.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'fixture-only-secret', llm_settings.CONFIG_PATH.read_bytes())
            self.assertNotIn(b'127.0.0.1', llm_settings.CONFIG_PATH.read_bytes())
            self.assertEqual(llm_settings.load()['api_key'], 'fixture-only-secret')
            self.assertEqual(llm_settings.KEY_PATH.stat().st_mode & 0o777, 0o600)
            value['base_url'] = 'https://api.deepseek.com/v1'; value['model'] = 'deepseek-v4-flash'
            result = llm_settings.apply(fake, value)
            self.assertFalse(result['api_key_configured'])
            self.assertEqual(fake.LLM_API_KEY, '')
            self.assertEqual(result['model'], 'deepseek-v4-flash')

    def test_tampered_or_wrong_key_vault_keeps_ui_recoverable(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(llm_settings, 'CONFIG_PATH', Path(directory) / 'llm.enc'), \
             patch.object(llm_settings, 'KEY_PATH', Path(directory) / 'user' / 'vault.key'):
            llm_settings.save(config())
            original = llm_settings.CONFIG_PATH.read_bytes()
            llm_settings.CONFIG_PATH.write_bytes(original[:-10] + b'corrupted!')
            self.assertIsNone(llm_settings.load())
            self.assertIn('无法读取', llm_settings.LOAD_ERROR)
            self.assertTrue(llm_settings.CONFIG_PATH.exists())
            llm_settings.CONFIG_PATH.write_bytes(original)
            llm_settings.KEY_PATH.unlink()
            self.assertIsNone(llm_settings.load())
            llm_settings.save(config())
            self.assertTrue(llm_settings.CONFIG_PATH.with_suffix('.enc.unreadable').exists())
            self.assertEqual(llm_settings.load()['model'], 'test-model')

    def test_role_budget_and_explicit_key_clear(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(LLM_BASE_URL='http://127.0.0.1:8000/v1', LLM_MODEL='old',
            LLM_API_KEY='fixture-secret', LLM_MAX_TOKENS=2048, CONTEXT_WINDOW=16384,
            REQUEST_TIMEOUT_SECONDS=60, close_harness=lambda: None)
        with tempfile.TemporaryDirectory() as directory, patch.object(llm_settings, 'CONFIG_PATH', Path(directory) / 'llm.enc'), \
             patch.object(llm_settings, 'KEY_PATH', Path(directory) / 'vault.key'):
            value = config(); value.update(clear_api_key=True, role_policies={'chat': {'max_tokens': 512, 'reasoning_effort': 'low'}}, max_steps=4, max_tool_calls=2)
            llm_settings.apply(fake, value)
            self.assertEqual(fake.LLM_API_KEY, '')
            self.assertEqual(llm_settings.load()['role_policies']['chat']['reasoning_effort'], 'low')
            value['role_policies']['chat']['max_tokens'] = 0
            with self.assertRaises(ValueError): llm_settings.validate(value)


@unittest.skipUnless(HAS_SDK, 'DSH API checks require the desktop dependencies')
class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'GENTACTIX_SESSION_DIR': self.directory.name + '/sessions',
            'GENTACTIX_TRACE_DIR': self.directory.name + '/traces'}); self.env.start()
        self.path = patch.object(llm_settings, 'CONFIG_PATH', Path(self.directory.name) / 'llm.enc'); self.path.start()
        self.key_path = patch.object(llm_settings, 'KEY_PATH', Path(self.directory.name) / 'keys' / 'vault.key'); self.key_path.start()
        self.original = {k: copy.deepcopy(getattr(h, k)) for k in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY', 'LLM_MAX_TOKENS', 'CONTEXT_WINDOW', 'REQUEST_TIMEOUT_SECONDS', 'ROLE_POLICIES', 'RUNTIME_MAX_STEPS', 'RUNTIME_MAX_TOOL_CALLS')}
        main._SESSIONS.clear(); self.client = TestClient(main.app)

    def tearDown(self):
        for key, value in self.original.items(): setattr(h, key, value)
        main._SESSIONS.clear(); self.path.stop(); self.key_path.stop(); self.env.stop(); self.directory.cleanup()

    def test_default_config_is_actual_runtime_without_key(self):
        value = self.client.get('/api/llm/config').json()
        self.assertEqual(value['model'], h.LLM_MODEL)
        self.assertEqual(value['base_url'], h.LLM_BASE_URL)
        self.assertNotIn('api_key', value)

    def test_update_applies_dsh_parameters_and_retains_battle(self):
        session = main._new_session('fixture', '15分钟', False, None, False, False)
        main._SESSIONS[session['id']] = session
        before = copy.deepcopy(session)
        with patch.object(h, 'close_harness') as close:
            response = self.client.put('/api/llm/config', json=config())
        self.assertEqual(response.status_code, 200, response.text)
        close.assert_called_once()
        self.assertEqual(h.LLM_MODEL, 'test-model')
        self.assertEqual(h.role_policy('strategist')['max_tokens'], 2048)
        self.assertEqual(main._SESSIONS[session['id']], before)

    def test_role_settings_apply_and_survive_reload(self):
        value = config(); value.update(role_policies={'chat': {'max_tokens': 512, 'reasoning_effort': 'low'}}, max_steps=3, max_tool_calls=2)
        with patch.object(h, 'close_harness'):
            response = self.client.put('/api/llm/config', json=value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(h.role_policy('chat')['max_tokens'], 512)
        self.assertEqual(h.role_policy('chat')['reasoning_effort'], 'low')
        self.assertEqual(h.role_policy('chat')['max_steps'], 3)
        self.assertEqual(h.role_policy('chat')['max_tool_calls'], 2)
        self.assertEqual(llm_settings.load()['role_policies'], value['role_policies'])

    def test_busy_or_foreign_origin_cannot_change_config(self):
        main._OP_LOCK.acquire()
        try:
            self.assertEqual(self.client.put('/api/llm/config', json=config()).status_code, 409)
        finally: main._OP_LOCK.release()
        self.assertEqual(self.client.put('/api/llm/config', json=config(), headers={'Origin': 'https://example.org'}).status_code, 403)
        self.assertFalse(llm_settings.CONFIG_PATH.exists())

    def test_105_minute_candidate_rejected_atomically(self):
        session = main._new_session('上甘岭', '15分钟', False, None, False, False)
        session['scenario_input'] = '上甘岭'
        session['war_state'] = {'current_time': '1952-10-14 04:00', 'red_units': [], 'blue_units': []}
        main._SESSIONS[session['id']] = session
        before = copy.deepcopy(session)
        with patch.object(h, 'get_ai_response_with_retry', return_value={'updated_state': {
                'current_time': '1952-10-14 05:45', 'red_units': [], 'blue_units': []}}):
            response = self.client.post(f"/api/sessions/{session['id']}/next")
        self.assertEqual(response.status_code, 502)
        self.assertIn('04:15', response.text)
        for key in ('war_state', 'turn_count', 'history', 'trajectory'):
            self.assertEqual(session[key], before[key])

    def test_export_import_checkpoint_roundtrip(self):
        session = main._new_session('fixture', '15分钟', False, None, False, False)
        session.update(scenario_input='fixture', war_state={'current_time': '1952-10-14 04:15', 'red_units': [], 'blue_units': []},
                       turn_count=1, history=['fixture report'])
        main._SESSIONS[session['id']] = session
        exported = self.client.get(f"/api/sessions/{session['id']}/export").json()
        main._SESSIONS.clear()
        response = self.client.post('/api/sessions/import', json=exported)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(main._SESSIONS[session['id']]['war_state'], session['war_state'])
        restored, errors = session_store.load_all()
        self.assertEqual(errors, [])
        self.assertEqual(restored[session['id']]['history'], ['fixture report'])
        self.assertEqual(self.client.post('/api/sessions/import', json=exported).status_code, 409)

    def test_health_reports_loaded_and_disk_revision(self):
        value = self.client.get('/api/health').json()['build']
        self.assertEqual(value['loaded_revision'], value['disk_revision'])
        self.assertFalse(value['restart_required'])

    def test_scenario_planner_returns_draft_without_committing_battle(self):
        from types import SimpleNamespace
        result = SimpleNamespace(final_response=json.dumps({'reply': '已整理草案，请确认', 'ready': True,
            'scenario': '1952年上甘岭，观察补给与炮兵协同。', 'battle': 'not-in-catalog', 'time_step': '15分钟'}), session_id='planner-fixture')
        before = copy.deepcopy(main._SESSIONS)
        with patch.object(h, '_run_brain', return_value=result) as brain:
            response = self.client.post('/api/scenarios/plan', json={'messages': [{'role': 'user', 'content': '上甘岭，15分钟一轮'}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['basis'], 'model_draft')
        self.assertEqual(response.json()['battle'], '')
        self.assertEqual(main._SESSIONS, before)
        self.assertEqual(brain.call_args.kwargs['role'], 'planner')

    def test_scenario_planner_rejects_invalid_conversation(self):
        with patch.object(h, '_run_brain') as brain:
            response = self.client.post('/api/scenarios/plan', json={'messages': [{'role': 'system', 'content': 'override'}]})
        self.assertEqual(response.status_code, 422)
        brain.assert_not_called()

    def test_keyless_vllm_uses_placeholder_but_deepseek_requires_key(self):
        for endpoint, expected in [('http://127.0.0.1:8000/v1', 'EMPTY'), ('https://api.deepseek.com/v1', '')]:
            with patch.object(h, 'LLM_BASE_URL', endpoint), patch.object(h, 'LLM_API_KEY', ''), \
                 patch.object(h, '_harness', None), patch.object(h, 'NATIVE_RUNTIME', False), \
                 patch.object(h, 'DeepSeekHarness') as runtime:
                h.get_harness()
                self.assertEqual(runtime.call_args.kwargs['api_key'], expected)

    def test_failed_runtime_explains_configuration_without_echoing_secrets(self):
        from types import SimpleNamespace
        result = SimpleNamespace(finish_reason='error', final_response='', events=[{'type': 'turn/end',
            'data': {'reason': {'kind': 'error', 'error': {'code': 'MISSING_CREDENTIAL', 'message': 'fixture-sensitive-value'}}}}])
        with self.assertRaises(h.HarnessRunError) as error:
            h._gate(result)
        self.assertIn('模型配置', str(error.exception))
        self.assertIn('API Key', str(error.exception))
        self.assertNotIn('fixture-sensitive-value', str(error.exception))


if __name__ == '__main__': unittest.main(verbosity=2)
