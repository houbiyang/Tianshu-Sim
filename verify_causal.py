"""Offline causal geometry regressions; no SDK, credentials or live models."""
import copy
import json
import unittest

from gentactix import causal


def fixture():
    before = {'current_time': '1952年10月14日04:00',
              'red_units': [{'name': '甲', 'type': '徒步步兵', 'hp': 100, 'lat': 38.325, 'lng': 127.466}],
              'blue_units': [{'name': '乙', 'type': '徒步步兵', 'hp': 100, 'lat': 38.320, 'lng': 127.466}]}
    after = copy.deepcopy(before)
    after['current_time'] = '1952年10月14日04:15'
    after['red_units'][0]['lat'] = 38.323
    return {'id': 'test:e0:t1', 'turn': 1, 'epoch': 0, 'time_step': '15分钟',
            'geometry_enabled': True, 'params': {'terrain_factor': 2.2, 'contact_km': 0.1},
            'before': before, 'after': after, 'changes': causal.state_changes(before, after),
            'decisions': [], 'runs': [], 'clock': {'status': 'verified'}}


class CausalTests(unittest.TestCase):
    def test_explanation_deduplicates_metadata_without_dropping_turns_or_effects(self):
        records=[]
        for turn in range(1,51):
            r=fixture();r.update(id=f'test:e0:t{turn}',turn=turn)
            r['decisions']=[{'id':f'd{turn}','team':'red','unit_name':'甲','action':'前进',
                             'rationale':'接近对方','expected_effect':'能够接触','basis':'model_declared'}]
            records.append(r)
        analysis=causal.analyze(records);original=copy.deepcopy(analysis)
        # These repeated branch descriptions must not multiply prompt size.
        for turn in analysis['attributions']:
            for effect in turn['effects']:effect['scope']='重复的控制变量说明'*1000
        compact=causal.explanation_evidence(analysis)
        self.assertEqual([r['trace_id'] for r in compact['evidence']],[r['id'] for r in records])
        self.assertEqual(compact['coverage']['decision_count'],50)
        for source,target in zip(analysis['attributions'],compact['attributions']):
            self.assertEqual(len(source['effects']),len(target['effects']))
            self.assertEqual(source['interactions'],target['interactions'])
            for full,small in zip(source['effects'],target['effects']):
                self.assertEqual(full['status_changes'],small['status_changes'])
                self.assertEqual([m for m in full['metrics'] if m['delta']],small['nonzero_metrics'])
        self.assertLess(len(json.dumps(compact)),len(json.dumps(analysis))/2)
        compact['evidence'][0]['changes'].clear()
        self.assertEqual(analysis['timeline'],original['timeline'])

    def test_ablation_changes_contact_without_mutation(self):
        record = fixture(); original = copy.deepcopy(record)
        result = causal.geometry_counterfactual(record, undo=[{'team': 'red', 'name': '甲'}])
        self.assertTrue(result['read_only'])
        self.assertTrue(any(c['field'] == 'can_contact' and c['factual'] and not c['alternative'] for c in result['status_changes']))
        self.assertEqual(record, original)
        self.assertIsNone(result['casualty_effect'])

    def test_actual_changes_and_turning_point_are_observations(self):
        record = fixture(); result = causal.analyze([record])
        self.assertTrue(result['turning_points'])
        self.assertTrue(result['attributions'][0]['effects'])
        self.assertEqual(record['decisions'], [])
        self.assertIsNone(result['explanation'])

    def test_no_movement_is_not_fabricated_decision(self):
        record = fixture(); record['after'] = copy.deepcopy(record['before']); record['changes'] = []
        result = causal.analyze([record])
        self.assertEqual(result['turning_points'], [])
        self.assertEqual(result['attributions'][0]['effects'], [])

    def test_cross_team_unknown_unit_rejected(self):
        for undo in ([{'team': 'blue', 'name': '甲'}], [{'team': 'red', 'name': '不存在'}]):
            with self.assertRaises(ValueError): causal.geometry_counterfactual(fixture(), undo=undo)

    def test_intervention_does_not_allow_outcome_overwrite(self):
        for changes in ([{'team': 'red', 'name': '甲', 'hp': 0}],
                        [{'team': 'red', 'name': '甲', 'lat': float('nan')}],
                        [{'team': 'red', 'name': '甲', 'supply': 101}]):
            with self.assertRaises(ValueError): causal.geometry_counterfactual(fixture(), changes=changes)

    def test_clock_verified_mismatch_unknown(self):
        record = fixture()
        self.assertEqual(causal.clock_check(record['before'], record['after'], '15分钟')['status'], 'verified')
        record['after']['current_time'] = '1952年10月14日05:45'
        self.assertEqual(causal.clock_check(record['before'], record['after'], '15分钟')['actual_minutes'], 105)
        self.assertEqual(causal.clock_check(record['before'], record['after'], '15分钟')['status'], 'mismatch')
        record['after']['current_time'] = '拂晓前一刻'
        self.assertEqual(causal.clock_check(record['before'], record['after'], '15分钟')['status'], 'unknown')

    def test_chinese_clock_and_dotted_date_are_unambiguous(self):
        self.assertEqual(causal.clock_check({'current_time': '1952年10月14日凌晨4时'},
            {'current_time': '1952年10月14日凌晨4时15分'}, '15分钟')['status'], 'verified')
        self.assertEqual(causal.clock_check({'current_time': '1952.10.14 04:00'},
            {'current_time': '1952-10-14 04:15'}, '15分钟')['status'], 'verified')

    def test_decisions_require_named_existing_actors(self):
        record = fixture()
        result = causal.decisions_from({'decisions': [{'team': 'red', 'unit_name': '甲', 'action': '前进'},
            {'team': 'blue', 'unit_name': '甲', 'action': '伪造命令'}]}, record['before'], record['after'], record['id'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['basis'], 'model_declared')

    def test_initialization_has_no_turning_point(self):
        record = fixture(); record.update(turn=0, before=None)
        self.assertEqual(causal.analyze([record])['turning_points'], [])


if __name__ == '__main__': unittest.main(verbosity=2)
