"""Independent fault injection for the 50-turn teaching-scenario auditor."""
import copy
import unittest
from tools.qa.audit_outputs import audit_record


def fixture():
    u={'name':'测试步兵','type':'徒步步兵','lat':38.3,'lng':127.4,'hp':100,
       'morale':80,'supply':80,'combat_power':100,'speed':3,'attack_range':500,
       'personnel':{'committed':10,'current':10,'casualties':0,'noncombat_losses':0,'source':'教学想定'},
       'equipment':[{'name':'步枪','unit':'支','committed':10,'operational':10,'damaged':0,'destroyed':0}]}
    world={'current_time':'1952-10-14 04:00','simulation_clock':{'elapsed_seconds':0},
           'red_units':[u],'blue_units':[],'engagements':[]}
    return {'turn':0,'after':world,'report':'初始部署','runs':[],'decisions':[]}


class CampaignOutputAuditTests(unittest.TestCase):
    def codes(self, record):
        return {i['code'] for i in audit_record(record)['issues']}

    def test_balanced_ledger_passes_without_claiming_missing_layers_available(self):
        row=audit_record(fixture())
        self.assertTrue(row['structural_pass'])
        self.assertIn('data_missing',{i['code'] for i in row['issues']})

    def test_personnel_and_equipment_imbalance_detected(self):
        r=fixture();u=r['after']['red_units'][0]
        u['personnel']['casualties']=3;u['equipment'][0]['destroyed']=2
        issues=audit_record(r)['issues']
        self.assertEqual({i['category'] for i in issues if i['code']=='accounting_difference'}, {'personnel','equipment'})
        self.assertFalse(audit_record(r)['structural_pass'])

    def test_unknown_and_out_of_range_attackers_detected(self):
        r=fixture();r['after']['engagements']=[{'attacker':'测试步兵','attacker_pos':[38.3,127.4],'lat':38.4,'lng':127.4},
                                             {'attacker':'不存在','lat':38.3,'lng':127.4}]
        self.assertTrue({'range_exceeded','unknown_attacker'}<=self.codes(r))

    def test_inactive_unit_cannot_attack(self):
        r=fixture();r['after']['red_units'][0]['hp']=0
        r['after']['engagements']=[{'attacker':'测试步兵','attacker_pos':[38.3,127.4],'lat':38.3001,'lng':127.4}]
        self.assertIn('disabled_unit_attacks',self.codes(r))

    def test_raw_omissions_remain_visible_after_state_recovery(self):
        r=fixture();raw=copy.deepcopy(r['after']);del raw['red_units'][0]['personnel']
        row=audit_record(r,raw={'updated_state':raw})
        self.assertIn('missing_fields_before_guards',{i['code'] for i in row['issues']})

    def test_narrative_clock_and_turn_cannot_hide_behind_correct_state_clock(self):
        r=fixture();r['report']='【T+1 | 1952-10-14 05:45】'
        self.assertTrue({'turn_label_mismatch','clock_label_mismatch'}<=self.codes(r))

    def test_fire_outside_visible_sector_is_reported_separately_from_range(self):
        r=fixture();r['after']['red_units'][0]['fire_sector']={'bearing_deg':0,'arc_deg':60}
        r['after']['engagements']=[{'attacker':'测试步兵','attacker_pos':[38.3,127.4],'lat':38.299,'lng':127.4}]
        self.assertIn('shot_outside_displayed_sector',self.codes(r))
        self.assertNotIn('range_exceeded',self.codes(r))


if __name__=='__main__':unittest.main(verbosity=2)
