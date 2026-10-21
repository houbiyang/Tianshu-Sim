"""Real HTTP processes: checkpoint, terminate, restart, verify the same battle and source."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen

HAS_SDK = importlib.util.find_spec('deepseek_harness') is not None
ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(HAS_SDK, 'Real desktop process checks require DSH dependencies')
class DeploymentTests(unittest.TestCase):
    def test_checkpoint_survives_real_restart_and_source_matches(self):
        from backend.main import _new_session
        fixture = _new_session('restart fixture', '15分钟', False, None, False, False)
        fixture.update(war_state={'current_time': '1952-10-14 04:15', 'red_units': [], 'blue_units': []},
                       scenario_input='restart fixture', turn_count=1, history=['preserve-this-report'])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, GENTACTIX_SESSION_DIR=directory + '/sessions',
                GENTACTIX_TRACE_DIR=directory + '/traces', GENTACTIX_LLM_CONFIG=directory + '/llm.enc',
                GENTACTIX_VAULT_KEY_FILE=directory + '/private/vault.key',
                GENTACTIX_NATIVE_RUNTIME='0')
            process = None
            def request(path, payload=None, method=None):
                raw = json.dumps(payload).encode() if payload is not None else None
                with urlopen(Request(f'http://127.0.0.1:{port}' + path, data=raw, method=method,
                                     headers={'Content-Type': 'application/json'}), timeout=4) as response:
                    return json.load(response)
            def start():
                process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app',
                    '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(100):
                    if process.poll() is not None: self.fail('Isolated QA backend exited early')
                    try: return process, request('/api/health')
                    except OSError: time.sleep(.1)
                process.terminate(); process.wait(timeout=10)
                self.fail('Isolated QA backend failed to start')
            try:
                process, first = start()
                restored = request('/api/sessions/import', {'format': 'gentactix-session-v1', 'session': fixture})
                self.assertEqual(restored['turn_count'], 1)
                self.assertTrue(request('/api/sessions/checkpoint', {})['ok'])
                config = {'base_url': 'http://127.0.0.1:8000/v1', 'model': 'restart-model-fixture',
                    'api_key': 'restart-secret-fixture', 'max_tokens': 4096, 'context_window': 32768,
                    'timeout_seconds': 120, 'max_steps': 4, 'max_tool_calls': 3,
                    'role_policies': {'chat': {'max_tokens': 512, 'reasoning_effort': 'low'}}}
                self.assertTrue(request('/api/llm/config', config, 'PUT')['storage']['encrypted'])
                self.assertNotIn(b'restart-secret-fixture', Path(env['GENTACTIX_LLM_CONFIG']).read_bytes())
                process.terminate(); process.wait(timeout=15)
                process, second = start()
                self.assertNotEqual(first['build']['pid'], second['build']['pid'])
                self.assertFalse(second['build']['restart_required'])
                self.assertEqual(second['build']['loaded_revision'], second['build']['disk_revision'])
                saved = request('/api/sessions/' + fixture['id'])
                self.assertEqual(saved['war_state'], fixture['war_state'])
                self.assertEqual(saved['turn_count'], 1)
                self.assertEqual(saved['history'], ['preserve-this-report'])
                loaded = request('/api/llm/config')
                self.assertEqual(loaded['model'], config['model'])
                self.assertTrue(loaded['api_key_configured'])
                self.assertEqual(loaded['max_steps'], 4)
                self.assertEqual(loaded['role_policies']['chat'], config['role_policies']['chat'])
                self.assertNotIn('api_key', loaded)
            finally:
                if process and process.poll() is None:
                    process.terminate(); process.wait(timeout=15)


if __name__ == '__main__': unittest.main(verbosity=2)
