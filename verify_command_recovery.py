"""Regression for interrupted commands and import recovery; all model calls are fixtures."""
import copy
import unittest
from unittest.mock import patch

import verify_command_backend as fixture
from backend import main, harness_engine as h, session_store
from gentactix import commands


class RecoveryTests(unittest.TestCase):
    setUp = fixture.CommandAPITests.setUp
    tearDown = fixture.CommandAPITests.tearDown
    view = fixture.CommandAPITests.view
    revision = fixture.CommandAPITests.revision
    put = fixture.CommandAPITests.put
    all_hold = fixture.CommandAPITests.all_hold
    request = fixture.CommandAPITests.request

    def ordinary(self):
        session = main._SESSIONS[self.sid]
        session['mission_id'] = None
        session['war_state'].pop('mission')
        return session

    def advance_open(self, session):
        candidate = copy.deepcopy(session['war_state'])
        candidate['current_time'] = '1942-05-01 08:30'
        with patch.object(h, 'get_ai_response_with_retry', return_value={
                'updated_state': candidate, 'report': '现存单位保持原状待命'}):
            return self.client.post(self.path + '/next', json=self.request())

    def test_unavailable_saved_order_expires_without_blocking_fixed_turn(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                if blocked:
                    self.start()
                    self.path = f'/api/sessions/{self.sid}'
                data = self.all_hold()
                session = main._SESSIONS[self.sid]
                dead = session['war_state']['red_units'][0]
                unit = next(u for u in data['units'] if u['name'] == dead['name'])
                order = next(o for o in session['command_state']['orders'] if o['unit_id'] == unit['id'])
                if blocked:
                    order.update(status='blocked', reason='上轮暂停')
                dead['personnel'].update(current=0, casualties=100)
                dead['hp'] = 0
                session_store.validate(session_store.serializable(session))
                response = self.client.post(self.path + '/next', json=self.request())
                self.assertEqual(response.status_code, 200, response.text)
                cs = session['command_state']
                self.assertFalse(any(o['id'] == order['id'] for o in cs['orders']))
                archived = [o for o in cs['archive'] if o['id'] == order['id']]
                self.assertEqual(len(archived), 1)
                self.assertEqual(archived[0]['status'], 'expired')
                receipt = next(r for r in cs['receipts'] if r['command_id'] == order['id'])
                self.assertEqual((receipt['source'], receipt['status']), ('unavailable', 'expired'))

    def test_retired_order_expires_during_complete_open_pipeline(self):
        session = self.ordinary()
        self.all_hold()
        retired = session['war_state']['red_units'].pop(0)
        identity = next(e['id'] for e in session['command_state']['registry'] if e['name'] == retired['name'])
        session_store.validate(session_store.serializable(session))
        response = self.advance_open(session)
        self.assertEqual(response.status_code, 200, response.text)
        rows = [r for r in session['command_state']['receipts'] if r['unit_id'] == identity]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'expired')
        self.assertFalse(session['command_state']['orders'])

    def test_expiration_is_not_committed_on_checkpoint_failure(self):
        self.all_hold()
        session = main._SESSIONS[self.sid]
        dead = session['war_state']['red_units'][0]
        dead['personnel'].update(current=0, casualties=100)
        dead['hp'] = 0
        original = copy.deepcopy(session['command_state'])
        with patch.object(session_store, 'save', side_effect=OSError('disk full')):
            response = self.client.post(self.path + '/next', json=self.request())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(session['command_state'], original)
        self.assertEqual(session['turn_count'], 0)

    def test_unknown_position_advertises_only_hold_and_can_finish(self):
        session = self.ordinary()
        unit = session['war_state']['red_units'][0]
        unit.pop('lat'); unit.pop('lng')
        chosen = next(u for u in self.view()['units'] if u['name'] == unit['name'])
        self.assertEqual(chosen['capabilities']['actions'], ['hold'])
        self.put([{'unit_id': chosen['id'], 'action': 'hold'}])
        response = self.advance_open(session)
        self.assertEqual(response.status_code, 200, response.text)
        row = next(r for r in session['command_state']['receipts'] if r['unit_id'] == chosen['id'])
        self.assertIsNone(row['before_position']); self.assertIsNone(row['after_position'])
        self.assertEqual(row['outcome_basis'], 'position_unknown_other_effects_model_estimated')

    def test_unknown_position_hold_does_not_invent_coordinates(self):
        session = self.ordinary()
        unit = session['war_state']['red_units'][0]
        unit.pop('lat'); unit.pop('lng')
        chosen = next(u for u in self.view()['units'] if u['name'] == unit['name'])
        self.put([{'unit_id': chosen['id'], 'action': 'hold'}])
        prepared = commands.prepare(session)
        candidate = copy.deepcopy(session['war_state'])
        candidate['red_units'][0].update(lat=25, lng=110)
        with self.assertRaisesRegex(ValueError, '位置未知'):
            commands.verify_open(session, prepared, candidate)

    def exported(self):
        response = self.client.get(self.path + '/export')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_import_preview_is_readonly_and_reports_collision(self):
        document = self.exported()
        before = {p.name: p.read_bytes() for p in session_store.directory().glob('*')}
        preview = self.client.post('/api/sessions/import/preview', json=document)
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertTrue(data['conflict'])
        self.assertEqual(data['session_id'], self.sid)
        self.assertEqual(data['unit_count'], 6)
        self.assertEqual(before, {p.name: p.read_bytes() for p in session_store.directory().glob('*')})

    def test_preview_then_import_preserves_commands_and_rejects_repeat(self):
        self.all_hold()
        document = self.exported()
        main._SESSIONS.pop(self.sid)
        session_store.remove(self.sid)
        preview = self.client.post('/api/sessions/import/preview', json=document)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()['conflict'])
        self.assertEqual(preview.json()['order_count'], 6)
        self.assertNotIn(self.sid, main._SESSIONS)
        self.assertFalse((session_store.directory() / (self.sid + '.json')).exists())
        imported = self.client.post('/api/sessions/import', json=document)
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(len(self.view()['orders']), 6)
        self.assertEqual(self.client.post('/api/sessions/import', json=document).status_code, 409)

    def test_disk_only_checkpoint_is_never_overwritten_by_import(self):
        document = self.exported()
        path = session_store.directory() / (self.sid + '.json')
        path.write_text('unreadable existing checkpoint', encoding='utf-8')
        original = path.read_bytes()
        main._SESSIONS.pop(self.sid)
        response = self.client.post('/api/sessions/import', json=document)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn(self.sid, main._SESSIONS)

    def test_invalid_preview_and_legacy_without_snapshot_are_rejected(self):
        for document in ({'format': 'gentactix-session-v1', 'session': {}}, {'war_state': {}}):
            with self.subTest(document=document):
                r = self.client.post('/api/sessions/import/preview', json=document)
                self.assertEqual(r.status_code, 422, r.text)

    def test_scope_transition_keeps_permission_gate_and_accepts_explicit_cleanup(self):
        data = self.view()
        red = [u['id'] for u in data['units'] if u['team'] == 'red'][:2]
        blue = next(u['id'] for u in data['units'] if u['team'] == 'blue')
        self.put([{'unit_id': i, 'action': 'hold'} for i in [*red, blue]], unit_agents=[*red, blue])
        invalid = {**self.revision(), 'orders': [], 'control_team': 'blue', 'director': False, 'unit_agents': [*red, blue]}
        self.assertEqual(self.client.put(self.path + '/commands', json=invalid).status_code, 422)
        saved = next(o for o in self.view()['orders'] if o['unit_id'] == blue)
        response = self.client.put(self.path + '/commands', json={**self.revision(), 'control_team': 'blue',
            'director': False, 'orders': [{k: saved[k] for k in ('id', 'unit_id', 'action', 'duration')}], 'unit_agents': [blue]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.view()['unit_agents'], [blue])
        self.assertEqual(self.view()['turn'], 0)
        cancelled = [o for o in main._SESSIONS[self.sid]['command_state']['archive'] if o['status'] == 'cancelled']
        self.assertEqual(len(cancelled), 2)


if __name__ == '__main__':
    unittest.main()
