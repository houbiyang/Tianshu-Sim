"""Supply/sector/narrative contract regressions from the 50-turn audit."""
import copy
import unittest
from gentactix import adjudicator, battlefield, fire_control, reporting, supply


def world():
    return {'current_time': '1952-10-14 11:15', 'red_units': [
        {'name': '红方工兵排', 'type': '工兵', 'lat': 38, 'lng': 127.01, 'hp': 90,
         'supply': 100, 'attack_range': 3000, 'supply_node_id': 'r',
         'personnel': {'current': 22, 'committed': 30, 'casualties': 8, 'noncombat_losses': 0}}],
        'blue_units': [], 'engagements': [],
        'supply_nodes': [{'id': 'r', 'team': 'red', 'lat': 38, 'lng': 127}],
        'supply_routes': [{'name': '道路', 'team': 'red', 'unit_name': '红方工兵排',
                           'waypoints': [[38, 127], [38.01, 127.005], [38, 127.01]],
                           'status': 'cut', 'source': '教学估计'}]}


class SupplyTests(unittest.TestCase):
    def test_map_uses_same_polyline_and_status_as_kernel_without_mutation(self):
        w = world(); frozen = copy.deepcopy(w)
        adj = adjudicator.adjudicate(w)
        line = battlefield.build_battlefield_view(w, adj)['supply_lines'][0]
        self.assertEqual(line, adj['units'][0]['supply_paths'][0])
        self.assertEqual(line['coordinates'], w['supply_routes'][0]['waypoints'])
        self.assertEqual((line['status'], line['declared_status']), ('open', 'cut'))
        self.assertTrue(line['estimated'])
        self.assertEqual(w, frozen)

    def test_all_polyline_segments_are_checked_not_the_source_recipient_chord(self):
        w = world(); w['blue_units'] = [{'name': '敌', 'type': '步兵', 'lat': 38.01,
                                        'lng': 127.005, 'hp': 100, 'attack_range': 100}]
        adj = adjudicator.adjudicate(w, params={'control_radius_km': .1})
        self.assertEqual(adj['units'][0]['supply'], '已切断')
        self.assertEqual(adj['units'][0]['cut_by'], '敌')
        self.assertGreater(adj['units'][0]['cut_at_km'], .5)

    def test_small_control_circle_between_old_sampling_points_is_intercepted(self):
        a = [38, 127]; b = adjudicator.offset_point(a, 90, 1)
        center = adjudicator.offset_point(a, 90, .25)
        hit, name, distance = adjudicator._blocked_along(a, b, [('敌', center, .05)])
        self.assertTrue(hit); self.assertEqual(name, '敌'); self.assertAlmostEqual(distance, .2, places=3)
        self.assertFalse(adjudicator._blocked_along(a, b, [('外侧', [38.01, 127], .05)])[0])

    def test_alternative_open_route_keeps_connectivity_and_both_routes_visible(self):
        w = world(); w['blue_units'] = [{'name': '敌', 'type': '步兵', 'lat': 38.01,
                                        'lng': 127.005, 'hp': 100, 'attack_range': 100}]
        w['supply_routes'].append({**w['supply_routes'][0], 'name': '直路', 'waypoints': [[38, 127], [38, 127.01]]})
        row = adjudicator.adjudicate(w, params={'control_radius_km': .1})['units'][0]
        self.assertEqual(row['supply'], '未切断')
        self.assertEqual([r['status'] for r in row['supply_paths']], ['cut', 'open'])
        self.assertEqual(row['supply_path'], w['supply_routes'][1]['waypoints'])

    def test_moving_friendly_truck_cannot_move_declared_depot(self):
        w = world(); w['red_units'].append({'name': '卡车', 'type': '运输', 'lat': 38.02, 'lng': 127, 'hp': 100})
        first = adjudicator.adjudicate(w)['units'][0]
        w['red_units'][1]['lng'] = 128
        second = adjudicator.adjudicate(w)['units'][0]
        self.assertEqual(first['supply_path'], second['supply_path'])

    def test_wrong_team_duplicate_and_missing_nodes_never_use_a_guessed_depot(self):
        for mode in ('wrong', 'duplicate', 'missing'):
            w = world()
            if mode == 'wrong': w['supply_nodes'][0]['team'] = 'blue'
            elif mode == 'duplicate': w['supply_nodes'] *= 2
            else: w['supply_nodes'] = []
            row = adjudicator.adjudicate(w)['units'][0]
            self.assertEqual(row['supply_basis'], 'unknown'); self.assertEqual(row['supply_path'], [])
            self.assertTrue(row['supply_warnings'])

    def test_invalid_polyline_falls_back_to_known_node_with_warning(self):
        w = world(); w['supply_routes'][0]['waypoints'][1] = [float('nan'), 127]
        row = adjudicator.adjudicate(w)['units'][0]
        self.assertEqual(row['supply_basis'], 'node_direct'); self.assertTrue(row['supply_warnings'])

    def test_moved_recipient_adds_visible_estimated_last_mile(self):
        w = world(); original = copy.deepcopy(w['supply_routes'][0]['waypoints'])
        w['red_units'][0]['lng'] = 127.02
        row = adjudicator.adjudicate(w)['units'][0]
        self.assertEqual(row['supply_path'][:-1], original)
        self.assertEqual(row['supply_path'][-1], [38, 127.02])
        self.assertIn('末段', row['supply_paths'][0]['source'])

    def test_stale_adjudication_status_cannot_be_applied_to_changed_route(self):
        w = world(); adj = adjudicator.adjudicate(w)
        w['supply_routes'][0]['waypoints'][1] = [38.02, 127]
        line = battlefield.build_battlefield_view(w, adj)['supply_lines'][0]
        self.assertEqual(line['status'], 'unknown')

    def test_rearmost_unit_does_not_pick_a_front_unit_as_its_rear(self):
        rear = {'pos': (38, 127), 'name': '后'}
        front = {'pos': (38.01, 127), 'name': '前'}
        result = adjudicator._rear_point([rear, front], (38.02, 127), rear)
        self.assertLess(result[0], rear['pos'][0])
        self.assertAlmostEqual(adjudicator.distance_km(result, rear['pos']), 8, places=3)


class FireTests(unittest.TestCase):
    def shot(self, bearing, sector=0, arc=60):
        w = world(); u = w['red_units'][0]
        u['fire_sector'] = {'bearing_deg': sector, 'arc_deg': arc}
        p = [u['lat'], u['lng']]; target = adjudicator.offset_point(p, bearing, .3)
        w['engagements'] = [{'attacker': u['name'], 'attacker_pos': p, 'lat': target[0], 'lng': target[1]}]
        return w

    def test_wrap_boundary_and_actual_outside_sector(self):
        for bearing in (350, 0, 30): self.assertEqual(fire_control.check(self.shot(bearing)), [])
        self.assertEqual(fire_control.check(self.shot(31))[0]['code'], 'outside_sector')

    def test_unknown_direction_is_not_guessed(self):
        w = self.shot(180); del w['red_units'][0]['fire_sector']
        self.assertEqual(fire_control.check(w), [])

    def test_inherited_sector_cannot_be_bypassed_by_omission(self):
        old = self.shot(0); new = self.shot(180); del new['red_units'][0]['fire_sector']
        self.assertIn('outside_sector', [i['code'] for i in fire_control.check(new, old)])

    def test_rotation_requires_reason_and_fixed_sector_cannot_rotate(self):
        old = self.shot(0); new = self.shot(180, 180)
        self.assertIn('undeclared_reorientation', [i['code'] for i in fire_control.check(new, old)])
        new['red_units'][0]['fire_sector_change'] = {'reason': '完成炮架转向，重新校射'}
        self.assertEqual(fire_control.check(new, old), [])
        old['red_units'][0]['fire_sector']['fixed'] = True
        self.assertIn('undeclared_reorientation', [i['code'] for i in fire_control.check(new, old)])

    def test_unknown_origin_range_and_sector_are_separate_checks(self):
        w = self.shot(180); w['red_units'][0]['attack_range'] = 50
        codes = {i['code'] for i in fire_control.check(w)}
        self.assertTrue({'outside_sector', 'range_exceeded'} <= codes)
        w['engagements'][0]['attacker_pos'] = [float('nan'), 0]
        self.assertIn('invalid_position', {i['code'] for i in fire_control.check(w)})
        w['engagements'][0]['attacker'] = '不存在'
        self.assertIn('unknown_attacker', {i['code'] for i in fire_control.check(w)})


class NarrativeTests(unittest.TestCase):
    def test_body_clock_is_not_hidden_by_correct_header(self):
        w = world(); old = {**w, 'current_time': '1952-10-14 11:00'}
        text = '【T+29 | 1952-10-14 11:15】七点一刻，高地安静。'
        result, audit = reporting.render(text, w, old, turn=29)
        self.assertIn('narrative_clock', {x['code'] for x in audit['issues']})
        self.assertNotIn('七点一刻', result); self.assertEqual(audit['original_text'], text)
        self.assertIn('11:15', result)

    def test_full_strength_and_supply_are_replaced_by_actual_ledger(self):
        w = world(); frozen = copy.deepcopy(w)
        result, audit = reporting.render('红方炮兵、工兵、运输三单位满编满供。', w, turn=50)
        self.assertNotIn('满编满供', result); self.assertIn('22/30', result)
        self.assertEqual(w, frozen); self.assertEqual(audit['status'], 'fallback')

    def test_no_fabricated_ammunition_from_zero_supply_or_hp(self):
        w = world(); w['red_units'][0].update(supply=0, hp=1)
        result, audit = reporting.render('弹药归零，红方快没人了。', w, turn=1)
        self.assertIn('22/30', result); self.assertNotIn('快没人', result)
        self.assertTrue({'inventory_claim', 'strength_claim'} <= {x['code'] for x in audit['issues']})

    def test_history_and_direction_claims_require_evidence(self):
        _, audit = reporting.render('今天第一次受阻，是唯一变化。七个方向全部被封，退路只剩一条。', world(), turn=1)
        self.assertTrue({'history_superlative', 'direction_count'} <= {x['code'] for x in audit['issues']})

    def test_review_keeps_recommendations_but_not_conflicting_sentence(self):
        result, audit = reporting.render('工兵满编。建议检查补给线路。', world(), review=True)
        self.assertNotIn('工兵满编', result); self.assertIn('建议检查补给线路', result)
        self.assertIn('22/30', result); self.assertTrue(audit['issues'])

    def test_unknown_numbers_stay_unknown_and_a_little_is_not_a_clock(self):
        w = world(); w['red_units'][0].pop('personnel')
        result, audit = reporting.render('炮位补上一点补给。', w, turn=1)
        self.assertEqual(audit['issues'], []); self.assertIn('未记录/未记录', result)

    def test_wrong_date_header_and_multiday_explicit_times_are_detected(self):
        w = world()
        text, audit = reporting.render('【T+99 | 1952-10-15 11:15】正文。', w, turn=29)
        self.assertIn('report_header', {i['code'] for i in audit['issues']})
        self.assertTrue(text.startswith('【T+29 | 1952-10-14 11:15】'))
        _, audit = reporting.render('1952-10-15 11:15发生交火。', w, turn=29)
        self.assertIn('narrative_clock', {i['code'] for i in audit['issues']})

    def test_live_review_regression_does_not_grant_firing_rights_from_connectivity(self):
        text, audit = reporting.render('几何内核给出的补给通断已把结论写死：谁先转open谁就获得开火权。'
                                       '蓝方无弹可射。建议继续观察。', world(), review=True)
        self.assertNotIn('开火权', text); self.assertNotIn('无弹可射', text)
        self.assertIn('建议继续观察', text)
        self.assertTrue({'inventory_claim', 'geometry_claim'} <= {x['code'] for x in audit['issues']})


if __name__ == '__main__':
    unittest.main(verbosity=2)
