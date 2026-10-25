"""Order-domain regressions. Synthetic mission fixtures, no SDK or model calls."""
import copy
import unittest

from gentactix import commands as c, missions as m


def session():
    return {'id': '123456abcdef', 'role_epoch': 1, 'turn_count': 0, 'time_step': '30分钟',
            'simulation_mode': 'open', 'war_state': m.initial('AJ15-local-v1')}


def save(s, orders, **options):
    return c.replace_queue(s, orders, **{'control_team': 'red', 'director': False,
                                       'no_fire': False, 'intent': '', **options})


class OrderDomainTests(unittest.TestCase):
    def setUp(self):
        self.s = session()
        self.rows = c.view(self.s)['units']
        self.red = next(u for u in self.rows if u['team'] == 'red')
        self.blue = next(u for u in self.rows if u['team'] == 'blue')

    def order(self, action='hold', **extra):
        return {'unit_id': self.red['id'], 'action': action, **extra}

    def test_legacy_ids_are_stable_and_dont_modify_initial_hash(self):
        before = copy.deepcopy(self.s)
        self.assertEqual(c.view(self.s), c.view(copy.deepcopy(self.s)))
        self.assertEqual(self.s, before)
        self.s['command_state'] = c.state(self.s)
        self.assertEqual(c.view(self.s)['units'][0]['id'], self.rows[0]['id'])
        self.s['role_epoch'] += 1
        self.s.pop('command_state')
        self.assertNotEqual(c.view(self.s)['units'][0]['id'], self.rows[0]['id'])

    def test_permission_requires_explicit_director(self):
        order = {'unit_id': self.blue['id'], 'action': 'hold'}
        with self.assertRaisesRegex(ValueError, '权限'):
            save(self.s, [order])
        self.assertEqual(len(save(self.s, [order], director=True)['orders']), 1)

    def test_queue_rejects_duplicate_unknown_and_injected_state(self):
        for orders in ([self.order(), self.order()], [self.order(unit_id='missing')],
                       [self.order(war_state={'hp': 999})]):
            with self.assertRaises(ValueError):
                save(self.s, orders)
        self.assertNotIn('command_state', self.s)

    def test_revision_checks_clock_queue_and_world(self):
        token = c.world_version(self.s)
        c.check_revision(self.s, 0, 0, token)
        for turn, rev, digest in [(1, 0, token), (0, 1, token), (0, 0, 'stale'), (False, 0, token)]:
            with self.assertRaises(c.Conflict):
                c.check_revision(self.s, turn, rev, digest)
        self.s['war_state']['red_units'][0]['logistics']['food'] -= 1
        with self.assertRaises(c.Conflict):
            c.check_revision(self.s, 0, 0, token)

    def test_replay_and_terminal_are_readonly(self):
        self.s['simulation_mode'] = 'replay'
        with self.assertRaisesRegex(ValueError, '回放'):
            save(self.s, [self.order()])
        self.s['simulation_mode'] = 'open'
        self.s['war_state']['mission']['status'] = 'completed'
        with self.assertRaisesRegex(ValueError, '结算'):
            save(self.s, [self.order()])

    def test_target_validation_unknown_is_not_zero(self):
        for destination in ({'lat': None, 'lng': 1}, {'lat': True, 'lng': 1},
                            {'lat': 91, 'lng': 1}, {'lat': 1, 'lng': float('nan')},
                            {'lat': 1, 'lng': 1, 'hp': 100}):
            with self.assertRaises(ValueError):
                save(self.s, [self.order('move', destination=destination)])

    def test_map_boundaries_and_single_turn_budget(self):
        origin = self.red['position']
        distant = c.towards(origin, {'lat': origin['lat']+.05, 'lng': origin['lng']}, 1.6)
        with self.assertRaisesRegex(ValueError, '预算'):
            save(self.s, [self.order('move', destination=distant)])
        with self.assertRaisesRegex(ValueError, '边界'):
            save(self.s, [self.order('move', destination={'lat': origin['lat']+1, 'lng': origin['lng']}, duration='until_arrived')])

    def test_continuous_movement_is_bounded_and_survives_checkpoint(self):
        origin = self.red['position']
        target = c.towards(origin, {'lat': origin['lat']+.05, 'lng': origin['lng']}, 1.5)
        self.s['command_state'] = save(self.s, [self.order('move', destination=target, duration='until_arrived')])
        for turn in range(1, 4):
            prepared = c.prepare(self.s)
            orders = c.merge_proposal(self.s, prepared, [])
            after = m.advance(self.s['war_state'], orders, self.s['time_step'])
            cs, decisions = c.finish(self.s, prepared, after, executed=orders)
            moved = c.distance(c.unit_position(self.s['war_state']['red_units'][0]), c.unit_position(after['red_units'][0]))
            self.assertLessEqual(moved, .601)
            self.assertEqual(len(decisions), 6)
            self.assertEqual(cs['receipts'][-6]['source'], 'human')
            self.assertEqual(cs['receipts'][-6]['run_ids'], [])
            self.s.update(war_state=after, command_state=copy.deepcopy(cs), turn_count=turn)
            c.validate_saved(self.s)
        self.assertFalse(self.s['command_state']['orders'])
        self.assertLessEqual(c.distance(c.unit_position(after['red_units'][0]), target), .01)

    def test_low_food_halves_movement(self):
        self.s['war_state']['red_units'][0]['logistics']['food'] = 3
        self.assertAlmostEqual(c.view(self.s)['units'][0]['capabilities']['move_km'], .3)

    def test_manual_commands_cannot_be_overridden_by_model(self):
        self.s['command_state'] = save(self.s, [self.order()])
        prepared = c.prepare(self.s)
        result = c.merge_proposal(self.s, prepared, [{'unit': self.red['name'], 'action': 'move', 'lat': 0, 'lng': 0}])
        self.assertEqual(result, [{'unit': self.red['name'], 'action': 'hold'}])

    def test_no_fire_is_enforced_for_controlled_side(self):
        self.s['command_state'] = save(self.s, [], no_fire=True)
        prepared = c.prepare(self.s)
        with self.assertRaisesRegex(ValueError, '禁止'):
            c.merge_proposal(self.s, prepared, [{'unit': self.red['name'], 'action': 'fire'}])
        self.assertEqual(c.merge_proposal(self.s, prepared, [{'unit': self.blue['name'], 'action': 'fire'}])[0]['action'], 'fire')

    def test_impossible_orders_fail_before_state_change(self):
        with self.assertRaisesRegex(ValueError, '未实现'):
            save(self.s, [self.order('recon')])
        self.s['war_state']['red_units'][0]['logistics']['ammo'] = 0
        with self.assertRaises(ValueError):
            save(self.s, [self.order('fire', target_id=self.blue['id'])])

    def test_unavailable_and_ambiguous_units_cannot_be_commanded(self):
        self.s['war_state']['red_units'][0]['personnel'].update(current=0, casualties=100)
        with self.assertRaisesRegex(ValueError, '行动能力'):
            save(self.s, [self.order()])
        self.s = session()
        self.s['war_state']['red_units'].append(copy.deepcopy(self.s['war_state']['red_units'][0]))
        with self.assertRaisesRegex(ValueError, '歧义'):
            save(self.s, [self.order()])

    def test_cancel_is_copy_on_write_and_archived(self):
        self.s['command_state'] = save(self.s, [self.order()])
        before = copy.deepcopy(self.s)
        cs = c.cancel(self.s, self.s['command_state']['orders'][0]['id'])
        self.assertFalse(cs['orders'])
        self.assertEqual(cs['archive'][-1]['status'], 'cancelled')
        self.assertEqual(self.s, before)

    def test_terminal_expires_continuous_tasks(self):
        origin = self.red['position']
        target = c.towards(origin, {'lat': origin['lat']+.05, 'lng': origin['lng']}, 2)
        self.s['command_state'] = save(self.s, [self.order('move', destination=target, duration='until_arrived')])
        prepared = c.prepare(self.s)
        after = m.advance(self.s['war_state'], c.merge_proposal(self.s, prepared, []), self.s['time_step'])
        after['mission'].update(status='completed', winner='blue')
        cs, _ = c.finish(self.s, prepared, after)
        self.assertFalse(cs['orders'])
        self.assertEqual(cs['archive'][-1]['status'], 'expired')

    def test_all_twenty_missions_keep_command_ledger_and_checkpoint_continuity(self):
        for spec in m.catalog():
            with self.subTest(mission=spec['id']):
                s = {**session(), 'war_state': m.initial(spec['id'])}
                initial = copy.deepcopy(s['war_state'])
                for turn in range(spec['deadline_turn']):
                    rows = c.view(s)['units']
                    orders = [{'unit_id': u['id'], 'action': 'hold'} for u in rows if u['available']]
                    s['command_state'] = save(s, orders, director=True)
                    prepared = c.prepare(s)
                    executed = c.merge_proposal(s, prepared, [])
                    after = m.advance(s['war_state'], executed, s['time_step'])
                    cs, decisions = c.finish(s, prepared, after, executed=executed)
                    self.assertEqual(len(decisions), 6)
                    self.assertEqual(len(after['mission']['ledger']), 6)
                    self.assertEqual(after['mission']['initial_hash'], initial['mission']['initial_hash'])
                    s.update(command_state=cs, war_state=after, turn_count=turn + 1)
                    c.validate_saved(s)
                    s = copy.deepcopy(s)
                    if after['mission']['status'] == 'completed':
                        self.assertFalse(cs['orders'])
                        break

    def test_cross_side_same_names_are_not_commanded_by_name_adapter(self):
        self.s['war_state']['blue_units'][0]['name'] = self.red['name']
        with self.assertRaisesRegex(ValueError, '歧义'):
            save(self.s, [self.order()])

    def test_retired_or_disabled_command_has_explicit_terminal_receipt(self):
        for retired in (True, False):
            s = copy.deepcopy(self.s)
            s['command_state'] = save(s, [self.order()])
            if retired:
                s['war_state']['red_units'].pop(0)
            else:
                s['war_state']['red_units'][0]['personnel']['current'] = 0
            prepared = c.prepare(s)
            cs, _ = c.finish(s, prepared, s['war_state'])
            self.assertFalse(cs['orders'])
            self.assertEqual(cs['archive'][-1]['status'], 'expired')
            receipt = next(r for r in cs['receipts'] if r['unit_id'] == self.red['id'])
            self.assertEqual(receipt['source'], 'unavailable')
            if retired:
                s['command_state'] = cs
                s['war_state']['red_units'].append(copy.deepcopy(self.s['war_state']['red_units'][0]))
                new_id = next(u['id'] for u in c.view(s)['units'] if u['name'] == self.red['name'])
                self.assertNotEqual(new_id, self.red['id'])
                self.assertEqual(new_id, next(u['id'] for u in c.view(s)['units'] if u['name'] == self.red['name']))

    def test_changed_rule_version_pauses_existing_order(self):
        s = copy.deepcopy(self.s)
        s['war_state'].pop('mission')
        s['command_state'] = save(s, [self.order()])
        s['time_step'] = '15分钟'
        prepared = c.prepare(s)
        self.assertFalse(prepared['manual'])
        self.assertIn('规则已变更', prepared['blocked'][0]['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
