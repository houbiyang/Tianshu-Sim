"""Regressions for authoritative time, Korean-context gating and contact-band semantics."""
import copy
import unittest
from gentactix import sim_clock, adjudicator, prompts
from gentactix.unit_types import modern_unit_instruction


class ClockTests(unittest.TestCase):
    def test_105_minutes_cannot_be_committed_as_15(self):
        before = {'current_time': '1952年10月14日04:00'}
        sim_clock.initialize(before)
        candidate = {'current_time': '1952年10月14日05:45'}
        original = copy.deepcopy(before)
        with self.assertRaises(sim_clock.ClockViolation):
            sim_clock.enforce_step(before, candidate, '15分钟')
        self.assertEqual(before, original)
        self.assertNotIn('simulation_clock', candidate)

    def test_relative_dawn_does_not_guess_calendar(self):
        before = {'current_time': '1952年10月14日拂晓前两小时'}
        sim_clock.initialize(before)
        self.assertIsNone(before['simulation_clock']['origin_iso'])
        candidate = {'current_time': '1952年10月14日拂晓前一刻'}
        with self.assertRaises(sim_clock.ClockViolation):
            sim_clock.enforce_step(before, candidate, '15分钟')
        candidate['current_time'] = '1952年10月14日拂晓前两小时 · T+15分钟'
        sim_clock.enforce_step(before, candidate, '15分钟')
        self.assertEqual(candidate['simulation_clock']['elapsed_seconds'], 900)

    def test_date_rollover_and_forged_counter(self):
        before = {'current_time': '1952-12-31 23:55'}
        sim_clock.initialize(before)
        candidate = {'current_time': '1953-01-01 00:10', 'simulation_clock': {'elapsed_seconds': 999999}}
        sim_clock.enforce_step(before, candidate, '15分钟')
        self.assertEqual(candidate['simulation_clock']['elapsed_seconds'], 900)

    def test_ancient_time_has_exact_elapsed_counter(self):
        state = {'current_time': '前262年四月'}
        sim_clock.initialize(state)
        for minutes in (30, 60, 90):
            candidate = {'current_time': f'前262年四月 · T+{minutes}分钟'}
            sim_clock.enforce_step(state, candidate, '30分钟')
            state = candidate
        self.assertEqual(state['simulation_clock']['elapsed_seconds'], 5400)

    def test_parse_exact_and_ambiguous_times(self):
        for value in ('1952年10月14日凌晨4时15分', '1952.10.14 04:15', '1952-10-14T04:15'):
            self.assertEqual(sim_clock.parse_timestamp(value).minute, 15)
        for value in ('1952年10月14日拂晓前一刻', '1952-02-30 04:00', '公元前260年04:00'):
            self.assertIsNone(sim_clock.parse_timestamp(value))

    def test_invalid_step_rejected_and_month_is_30_days(self):
        for step in ('0分钟', '-15分钟', '随意', 'nan小时'):
            with self.assertRaises(ValueError): sim_clock.duration_seconds(step)
        self.assertEqual(sim_clock.duration_seconds('1个月'), 30 * 86400)

    def test_prompt_contains_computed_target(self):
        prompt = prompts.build_step_prompt({'current_time': '1952-10-14 04:00'}, 1, '15分钟', False)
        self.assertIn('1952-10-14 04:15', prompt)
        self.assertIn('程序时钟硬约束', prompt)

    def test_bare_years_do_not_inject_korean_equipment(self):
        for text in ('1950年非朝鲜地区假想战争', '1953年伊朗', '编号19501', '前260年长平'):
            self.assertEqual(modern_unit_instruction(text), '')
        for text in ('抗美援朝第五次战役', '朝鲜战争', '上甘岭', '长津湖'):
            self.assertIn('近现代兵种', modern_unit_instruction(text))


class ContactTests(unittest.TestCase):
    def battle(self, km):
        return {'red_units': [{'name': '甲', 'type': '步兵', 'lat': 38, 'lng': 127, 'hp': 100}],
                'blue_units': [{'name': '乙', 'type': '步兵', 'lat': 38 + km / 110.57, 'lng': 127, 'hp': 100}]}

    def test_band_changes_verdict_not_only_fingerprint(self):
        battle = self.battle(2)
        tight = adjudicator.adjudicate(battle, '15分钟', params={'contact_km': .1})
        loose = adjudicator.adjudicate(battle, '15分钟', params={'contact_km': 2})
        self.assertEqual(tight['summary']['can_contact'], 0)
        self.assertEqual(loose['summary']['can_contact'], 2)
        self.assertEqual(loose['units'][0]['hours_to_contact'], 0)
        self.assertEqual(tight['units'][0]['reach_km'], loose['units'][0]['reach_km'])

    def test_boundary_and_unreachable_enemy(self):
        result = adjudicator.adjudicate(self.battle(1.7), '15分钟', params={'contact_km': 1, 'terrain_factor': 1})
        self.assertTrue(result['units'][0]['can_contact'])
        result = adjudicator.adjudicate(self.battle(1.8), '15分钟', params={'contact_km': 1, 'terrain_factor': 1})
        self.assertFalse(result['units'][0]['can_contact'])
        self.assertGreater(result['units'][0]['hours_to_contact'], .25)


if __name__ == '__main__': unittest.main(verbosity=2)
