"""Command API/commit regression. Model transport fixtures are not live evidence."""
import copy
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

try:
    import verify_mission_backend as fixtures
    from backend import main, harness_engine as h, session_store
    from gentactix import commands, missions
    BACKEND = True
except ImportError:
    BACKEND = False


@unittest.skipUnless(BACKEND, 'Requires desktop Python dependencies')
class CommandAPITests(unittest.TestCase):
    def setUp(self):
        fixtures.MissionAPITests.setUp(self)
        self.start = lambda: fixtures.MissionAPITests.start(self)
        self.start()
        self.path = f'/api/sessions/{self.sid}'

    def tearDown(self):
        for sid in list(main._SESSIONS):
            main._SESSIONS.pop(sid)
        fixtures.MissionAPITests.tearDown(self)

    def view(self):
        response = self.client.get(self.path + '/commands')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def revision(self, data=None):
        data = data or self.view()
        return {k: data[v] for k, v in [('expected_turn', 'turn'), ('revision', 'revision'), ('world_version', 'world_version')]}

    def put(self, orders=None, **extra):
        data = self.view()
        response = self.client.put(self.path + '/commands', json={**self.revision(data),
            'orders': data['orders'] if orders is None else orders, 'control_team': 'red', 'director': True,
            **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def all_hold(self):
        return self.put([{'unit_id': u['id'], 'action': 'hold'} for u in self.view()['units']])

    def request(self):
        return {**self.revision(), 'batch_id': uuid.uuid4().hex}

    def test_manual_only_turn_uses_no_model_and_is_idempotent(self):
        data = self.all_hold(); payload = self.request()
        with patch.object(h, 'get_ai_response', side_effect=AssertionError('manual turn needs no model')), \
                patch.object(h, 'get_role_session', side_effect=AssertionError('no engine needed')):
            first = self.client.post(self.path + '/next', json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            repeated = self.client.post(self.path + '/next', json=payload)
        self.assertEqual(repeated.json()['turn_count'], 1)
        self.assertTrue(repeated.json()['command_result']['replayed'])
        receipts = self.client.get(self.path + '/unit-decisions').json()
        self.assertEqual(receipts['total'], len(data['units']))
        self.assertTrue(all(r['source'] == 'human' and r['run_ids'] == [] for r in receipts['items']))
        reused = self.client.post(self.path + '/next', json={**payload, 'instruction': 'changed'})
        self.assertEqual(reused.status_code, 409)
        restored, errors = session_store.load_all()
        self.assertFalse(errors)
        self.assertEqual(restored[self.sid]['command_state'], main._SESSIONS[self.sid]['command_state'])

    def test_stale_version_and_legacy_next_do_not_consume_orders(self):
        stale = self.request(); self.all_hold()
        before = copy.deepcopy(main._SESSIONS[self.sid]['command_state'])
        for payload in (stale, {}, {**self.request(), 'expected_turn': True}):
            response = self.client.post(self.path + '/next', json=payload)
            self.assertIn(response.status_code, (409, 422), response.text)
        self.assertEqual(main._SESSIONS[self.sid]['command_state'], before)

    def test_durable_failure_and_retry_keep_queue_world_and_receipts_atomic(self):
        self.all_hold(); payload = self.request()
        before = session_store.serializable(main._SESSIONS[self.sid])
        with patch.object(session_store, 'save', side_effect=OSError('disk-full')):
            response = self.client.post(self.path + '/next', json=payload)
        self.assertEqual(response.status_code, 502, response.text)
        live = main._SESSIONS[self.sid]
        for key in ('war_state', 'command_state', 'turn_count', 'trajectory', 'history'):
            self.assertEqual(live[key], before[key], key)
        retry = self.client.post(self.path + '/next', json=payload)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['turn_count'], 1)

    def test_partial_manual_overrides_model_order(self):
        units = self.view()['units']; chosen = units[0]
        self.put([{'unit_id': chosen['id'], 'action': 'hold'}])
        malicious = [{'unit': chosen['name'], 'action': 'move', 'lat': 80, 'lng': 0}]
        with patch.object(h, 'get_ai_response', return_value={'orders': malicious}):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 200, response.text)
        actual = next(u for u in response.json()['war_state']['red_units'] if u['name'] == chosen['name'])
        self.assertEqual(commands.unit_position(actual), chosen['position'])

    def test_cancel_mid_candidate_preserves_queue(self):
        chosen = self.view()['units'][0]
        self.put([{'unit_id': chosen['id'], 'action': 'hold'}])
        before = copy.deepcopy(main._SESSIONS[self.sid]['command_state'])
        def cancel(*a, **k):
            main._SESSIONS[self.sid]['cancel_requested'] = True
            return {'orders': []}
        with patch.object(h, 'get_ai_response', side_effect=cancel):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(main._SESSIONS[self.sid]['command_state'], before)
        self.assertEqual(main._SESSIONS[self.sid]['turn_count'], 0)

    def test_model_global_restriction_violation_rejects_entire_turn(self):
        self.put([], no_fire=True)
        red = self.view()['units'][0]['name']; blue = next(u['name'] for u in self.view()['units'] if u['team'] == 'blue')
        with patch.object(h, 'get_ai_response', return_value={'orders': [{'unit': red, 'action': 'fire', 'target': blue}]}):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.view()['turn'], 0)

    def test_resolve_is_draft_scoped_and_map_bound(self):
        data = self.view(); chosen = data['units'][0]; target = chosen['position']
        payload = {**self.revision(data), 'text': '前往选定位置', 'selected_units': [chosen['id']], 'map_target': target}
        output = {'orders': [{'unit_id': chosen['id'], 'action': 'move', 'destination': target}], 'intent': '保存兵力', 'no_fire': True}
        with patch.object(h, 'get_ai_response', return_value=output):
            response = self.client.post(self.path + '/commands/resolve', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'draft')
        self.assertFalse(self.view()['enabled']); self.assertEqual(self.view()['orders'], [])
        with patch.object(h, 'get_ai_response', return_value={'orders': [{**output['orders'][0], 'destination': {'lat': 0, 'lng': 0}}]}):
            self.assertEqual(self.client.post(self.path + '/commands/resolve', json=payload).status_code, 502)
        with patch.object(h, 'get_ai_response', return_value={'orders': [], 'clarifications': ['这是咨询，未下令']}):
            r = self.client.post(self.path + '/commands/resolve', json={**payload, 'text': '能不能撤退？'})
        self.assertEqual(r.json()['status'], 'needs_clarification')

    def test_no_enemy_agent_without_director(self):
        data = self.view(); enemy = next(u for u in data['units'] if u['team'] == 'blue')
        response = self.client.put(self.path + '/commands', json={**self.revision(), 'orders': [], 'unit_agents': [enemy['id']]})
        self.assertEqual(response.status_code, 422)

    def test_native_unit_identity_and_run_refs_do_not_leak_to_manual_units(self):
        data = self.view(); autonomous = data['units'][0]
        others = [{'unit_id': u['id'], 'action': 'hold'} for u in data['units'][1:]]
        self.put(others, unit_agents=[autonomous['id']])
        def native(binding, prompt, observer, **kwargs):
            self.assertIsNone(binding)
            self.assertIn('observation_version', prompt)
            native_id = f"gentactix-unit-{kwargs['namespace']}-fixture"
            run = SimpleNamespace(session_id=native_id, finish_reason='completed', events=[
                {'type': 'assistant/message', 'seq': 3}], final_response=json.dumps({'order': {
                    'unit_id': autonomous['id'], 'action': 'hold'}, 'rationale': '本单位守备目标'}))
            observer.record_result(run)
            return run, {'session_id': run.session_id, 'factory': 'DSH.AgentRegistry'}
        with patch.object(h, 'unit_run', side_effect=native), patch.object(h, 'get_ai_response', side_effect=AssertionError('all units covered')):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 200, response.text)
        trace = main._SESSIONS[self.sid]['trajectory'][-1]
        native_row = next(d for d in trace['decisions'] if d['source'] == 'native_unit_agent')
        self.assertEqual(native_row['dsh_event_ref']['seq'], 3)
        self.assertIn(self.sid, native_row['dsh_event_ref']['session_id'])
        self.assertTrue(all(d['dsh_event_ref'] is None for d in trace['decisions'] if d['source'] == 'human'))
        self.assertIn(autonomous['id'], main._SESSIONS[self.sid]['command_state']['agent_sessions'])
        session_store.validate(session_store.serializable(main._SESSIONS[self.sid]))

    def test_native_failure_never_falls_back_to_batch(self):
        u = self.view()['units'][0]
        self.put([], unit_agents=[u['id']])
        with patch.object(h, 'unit_run', side_effect=RuntimeError('journal missing')), patch.object(h, 'get_ai_response') as batch:
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 502)
        batch.assert_not_called()
        self.assertEqual(self.view()['turn'], 0)

    def test_history_branch_is_separate_and_original_readonly(self):
        main._SESSIONS[self.sid]['simulation_mode'] = 'replay'
        data = self.view(); original = copy.deepcopy(main._SESSIONS[self.sid]['war_state'])
        self.assertTrue(data['read_only'])
        self.assertEqual(self.client.put(self.path + '/commands', json={**self.revision(), 'orders': []}).status_code, 422)
        response = self.client.post(self.path + '/commands/open-branch', json=self.revision())
        self.assertEqual(response.status_code, 200, response.text)
        new_id = response.json()['session_id']; self.assertNotEqual(new_id, self.sid)
        self.assertEqual(main._SESSIONS[new_id]['war_state'], original)
        self.assertEqual(main._SESSIONS[self.sid]['simulation_mode'], 'replay')
        self.assertEqual(main._SESSIONS[new_id]['simulation_mode'], 'open')

    def test_decision_coverage_above_previous_cap_and_pagination(self):
        base = main._SESSIONS[self.sid]['war_state']
        before = copy.deepcopy(base)
        before['red_units'] = [{**before['red_units'][0], 'name': f'unit-{i}'} for i in range(27)]
        decisions = commands.complete_decisions(before, before, [], [])
        from gentactix import causal
        parsed = causal.decisions_from({'decisions': decisions}, before, before, 'trace')
        self.assertEqual(len(parsed), 30)
        self.assertTrue(all(d['source'] == 'missing' for d in parsed))
        main._SESSIONS[self.sid]['trajectory'] = [{'turn': 1, 'id': 'trace', 'decisions': parsed}]
        page = self.client.get(self.path + '/unit-decisions?limit=10&offset=10').json()
        self.assertEqual(page['total'], 30); self.assertEqual(len(page['items']), 10)
        self.assertEqual(self.client.get(self.path + '/unit-decisions?end_turn=0').json()['total'], 0)
        self.assertEqual(self.client.get(self.path + '/unit-decisions?start_turn=2&end_turn=1').status_code, 422)

    def test_ordinary_open_verifies_human_destination_after_model_and_geo(self):
        s = main._SESSIONS[self.sid]
        s['mission_id'] = None; s['war_state'].pop('mission')
        chosen = self.view()['units'][0]
        target = {**chosen['position'], 'lat': chosen['position']['lat'] + .001}
        self.put([{'unit_id': chosen['id'], 'action': 'move', 'destination': target}])
        request = self.request(); before = copy.deepcopy(s['war_state'])
        rejected_state = copy.deepcopy(before); rejected_state['current_time'] = '1942-05-01 08:30'
        with patch.object(h, 'get_ai_response_with_retry', return_value={'updated_state': rejected_state, 'report': '未移动'}):
            rejected = self.client.post(self.path + '/next', json=request)
        self.assertEqual(rejected.status_code, 502, rejected.text)
        self.assertIn('未履行', rejected.text)
        self.assertEqual(s['war_state'], before)
        candidate = copy.deepcopy(rejected_state); candidate['red_units'][0].update(target)
        with patch.object(h, 'get_ai_response_with_retry', return_value={'updated_state': candidate, 'report': '移动完成'}):
            accepted = self.client.post(self.path + '/next', json=request)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        receipt = self.client.get(self.path + '/unit-decisions?unit_id=' + chosen['id']).json()['items'][0]
        self.assertEqual(receipt['source'], 'human')
        self.assertEqual(receipt['outcome_basis'], 'movement_verified_other_effects_model_estimated')

    def test_unit_command_comparison_keeps_original_world_and_queue(self):
        self.all_hold(); response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 200, response.text)
        before = session_store.serializable(main._SESSIONS[self.sid])
        chosen = self.view()['units'][0]
        response = self.client.post(self.path + '/commands/compare', json={**self.revision(), 'turn': 1,
            'orders': [{'unit_id': chosen['id'], 'action': 'conceal'}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['read_only'])
        self.assertTrue(any(m['delta'] != 0 for m in response.json()['metrics']))
        self.assertEqual(session_store.serializable(main._SESSIONS[self.sid]), before)

    def test_crash_orphan_trace_is_retained_and_retry_uses_authoritative_checkpoint(self):
        self.all_hold(); payload = self.request()
        before = copy.deepcopy(main._SESSIONS[self.sid])
        completed = self.client.post(self.path + '/next', json=payload)
        self.assertEqual(completed.status_code, 200, completed.text)
        # Simulate a crash after trace fsync but before its world checkpoint.
        main._SESSIONS[self.sid] = before
        session_store.save(before)
        retry = self.client.post(self.path + '/next', json=payload)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['turn_count'], 1)
        from pathlib import Path
        archived = list(Path(self.temp.name).rglob('*.uncommitted-*.json'))
        self.assertEqual(len(archived), 1)

    def test_natural_draft_cannot_overwrite_newer_queue_or_fake_interpretation_source(self):
        data = self.view(); u = data['units'][0]
        with patch.object(h, 'get_ai_response', return_value={'orders': [{'unit_id': u['id'], 'action': 'hold'}]}):
            parsed = self.client.post(self.path + '/commands/resolve', json={**self.revision(), 'text': '待命', 'selected_units': [u['id']]})
        self.assertEqual(parsed.status_code, 200, parsed.text)
        proposal = parsed.json()
        forged = self.client.put(self.path + '/commands', json={**self.revision(), 'proposal_id': proposal['id'],
            'orders': [{'unit_id': u['id'], 'action': 'conceal'}]})
        self.assertEqual(forged.status_code, 422)
        self.put([])
        stale = self.client.put(self.path + '/commands', json={**self.revision(), 'proposal_id': proposal['id'], 'orders': proposal['orders']})
        self.assertEqual(stale.status_code, 422)

    def test_explicit_agent_reset_is_audited_and_does_not_change_world(self):
        u = self.view()['units'][0]
        self.put([], unit_agents=[u['id']])
        s = main._SESSIONS[self.sid]
        s['command_state']['agent_sessions'] = {u['id']: {'session_id': f"gentactix-unit-{self.sid}-e0-{u['id']}-old"}}
        before = copy.deepcopy(s['war_state'])
        with patch.object(h, 'dispose_unit_sessions') as dispose:
            self.put([], unit_agents=[u['id']], reset_agents=[u['id']])
        self.assertEqual(s['war_state'], before)
        self.assertFalse(s['command_state']['agent_sessions'])
        self.assertEqual(s['command_state']['agent_resets'][-1]['unit_id'], u['id'])
        dispose.assert_called_once()

    def test_batch_model_cannot_forge_native_agent_provenance(self):
        u = self.view()['units'][0]
        self.put([])
        reply = {'orders': [{'unit': u['name'], 'action': 'hold'}], 'decisions': [
            {'team': u['team'], 'unit_name': u['name'], 'action': 'hold', 'rationale': '测试模型候选',
             'source': 'native_unit_agent', 'run_ids': ['forged-run']}]}
        with patch.object(h, 'get_ai_response', return_value=reply):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 200, response.text)
        receipt = self.client.get(self.path + '/unit-decisions?unit_id=' + u['id']).json()['items'][0]
        self.assertEqual(receipt['source'], 'model_batch')
        self.assertNotIn('forged-run', receipt['run_ids'])
        self.assertIsNone(receipt['dsh_event_ref'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
