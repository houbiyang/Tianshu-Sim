"""Fixed teaching rules: conservation, intervention isolation, logistics and objectives."""
import copy
import json
import unittest
from pathlib import Path
from gentactix import missions as m, rag, prompts, sim_clock


class MissionTests(unittest.TestCase):
    def seed(self, profile='assault'):
        return m.initial(next(s['id'] for s in m.catalog() if s['profile'] == profile))

    def test_catalog_replay_and_final_verdict(self):
        self.assertEqual(len(m.catalog()), 20)
        for s in m.catalog():
            with self.subTest(s=s['id']):
                a=m.initial(s['id']); b=m.initial(s['id'])
                self.assertEqual(a,b)
                for _ in range(s['deadline_turn']):
                    if a['mission']['status']=='completed': break
                    a=m.advance(a,[],s['time_step']); b=m.advance(b,[],s['time_step'])
                    self.assertEqual(a,b); self.assertTrue(m.validate(a))
                self.assertEqual(a['mission']['status'],'completed')
                self.assertIn(a['mission']['winner'],('red','blue'))

    def test_generated_catalog_current(self):
        from tools.render_missions import render
        self.assertEqual(m.DATA.read_text(),render())

    def test_pinned_rules_all_twenty(self):
        for s in m.catalog():
            hits=rag.search(s['battle'],battle=s['battle'],phase='step',simulation_mode='open')
            included=rag.select_passages(hits,2200)
            self.assertTrue({'推演目标、关键决策与反事实','争议与待考','兵种、装备与补给'} <= {h['section'] for h in included})
            self.assertFalse({'作战过程与指挥时间线','伤亡、兵力快照与结果','资料与出处'} & {h['section'] for h in included})

    def test_other_families_rule_pinning(self):
        from gentactix import adjudication_lib
        count=0
        for spec in adjudication_lib.list_battles():
            name = spec if isinstance(spec,str) else spec.name
            if not name.startswith(('红军长征-', '抗美援朝-')): continue
            hits=rag.search(name,battle=name,phase='step',simulation_mode='open')
            sections={h['section'] for h in rag.select_passages(hits,2200)}
            self.assertTrue(sections & {'推演目标、关键决策与反事实','对推演建模的关键约束'}, name)
            self.assertTrue(sections & {'兵种、装备与补给','兵种与装备'}, name)
            self.assertFalse(sections & {'作战过程与指挥时间线','作战过程要点','伤亡与结果','伤亡、兵力快照与结果'})
            count+=1
        self.assertEqual(count,24)

    def test_mode_prompts(self):
        w=self.seed()
        opened=prompts.build_step_prompt(w,1,'30分钟',False,'',simulation_mode='open')
        replay=prompts.build_step_prompt(w,1,'30分钟',False,'',simulation_mode='replay')
        self.assertNotIn('必须让相关单位发生',opened)
        self.assertIn('开放推演',opened);self.assertIn('历史回放',replay)
        self.assertNotIn('12 到 24',prompts.build_init_prompt('湘江',{},''))

    def test_daily_clock_alias(self):
        self.assertEqual(sim_clock.duration_seconds('1日'),sim_clock.duration_seconds('1天'))
        src=Path('frontend/src/components/LaunchScreen.vue').read_text()
        self.assertIn('normalizeStep(battle.time_step',src)
        self.assertIn('stepOptions(draft.time_step)',src)

    def test_invalid_order_atomic_and_identity(self):
        w=self.seed(); old=m.digest(w)
        with self.assertRaises(ValueError): m.advance(w,[{'unit':'不存在','action':'hold'}],'30分钟')
        self.assertEqual(m.digest(w),old)
        with self.assertRaises(ValueError): m.advance(w,[],'1小时')
        damaged=copy.deepcopy(w);damaged['red_units'].pop()
        with self.assertRaises(ValueError): m.validate(damaged)
        damaged=copy.deepcopy(w);damaged['mission']['definition']['rules']['damage']=500
        damaged['mission']['definition_hash']=m.digest(damaged['mission']['definition'])
        with self.assertRaises(ValueError): m.validate(damaged)

    def test_depot_conservation_and_supply_action(self):
        w=self.seed(); w['supply_nodes'][0]['stock']=5
        orders=[{'unit':u['name'],'action':'resupply'} for u in w['red_units']]
        n=m.advance(w,orders,'30分钟'); logs=n['mission']['ledger'][:3]
        self.assertEqual(sum(r['delivery'] for r in logs),5)
        self.assertEqual(n['supply_nodes'][0]['stock'],0)
        for old,new,row in zip(w['red_units'],n['red_units'],logs):
            for field,cost in [('food','food_spent'),('ammo','ammo_spent')]:
                self.assertEqual(new['logistics'][field], old['logistics'][field]+row['delivery']-row[cost])

    def test_food_restricts_move_and_ammo_rejects_fire(self):
        w=self.seed();u=w['red_units'][0];u['logistics']['food']=0;u['logistics']['ammo']=0
        move={'unit':u['name'],'action':'move','lat':u['lat'], 'lng':u['lng']+.005}
        self.assertGreater(m.distance(u,move),.3)
        with self.assertRaises(ValueError):m.advance(w,[move],'30分钟')
        target=w['blue_units'][0];u.update(lat=target['lat'],lng=target['lng']-.001)
        with self.assertRaises(ValueError):m.advance(w,[{'unit':u['name'],'action':'fire','target':target['name']}],'30分钟')
        w['supply_nodes'][0]['stock']=0
        n=m.advance(w,[],'30分钟');self.assertEqual(n['red_units'][0]['personnel']['noncombat_losses'],2)

    def test_simultaneous_combat_and_counterfactual(self):
        w=self.seed();r=w['red_units'][0];b=w['blue_units'][0];r.update(lat=b['lat'],lng=b['lng']-.004)
        orders=[{'unit':r['name'],'action':'fire','target':b['name']},{'unit':b['name'],'action':'fire','target':r['name']}]
        a=m.advance(w,orders,'30分钟');reverse=m.advance(w,list(reversed(orders)),'30分钟')
        self.assertEqual(a['red_units'],reverse['red_units']);self.assertEqual(a['blue_units'],reverse['blue_units'])
        self.assertGreater(a['red_units'][0]['personnel']['casualties'],0)
        self.assertGreater(a['red_units'][0]['equipment'][0]['destroyed'],0)
        old=m.digest(w);branch=m.counterfactual(w,a,orders[1:],'30分钟')
        self.assertEqual(m.digest(w),old);self.assertTrue(branch['read_only'])
        self.assertGreater(next(r['delta'] for r in branch['metrics'] if r['metric']=='蓝方可用人员'),0)

    def test_seven_objectives_and_type_rules(self):
        for profile in ('assault','air','breakout','delay','rail','underground','reception'):
            w=self.seed(profile);spec=w['mission']['definition']
            if profile in ('assault','air'):
                for u in w['red_units']:u.update(spec['objective'])
                for u in w['blue_units']:u.update(lat=spec['objective']['lat']+.02)
            if profile=='breakout':
                for u in w['red_units']:u.update(spec['exit'])
            if profile=='rail':
                for u in w['red_units']:u.update(spec['objective'])
            if profile=='reception':
                for u in w['red_units']:u.update(spec['objective'])
            for i in range(4):
                orders=[]
                if profile=='rail':orders=[{'unit':u['name'],'action':'sabotage'} for u in w['red_units']]
                if profile=='underground':orders=[{'unit':u['name'],'action':'conceal'} for u in w['red_units']]
                if profile=='reception' and i>=2:orders=[{'unit':w['red_units'][0]['name'],'action':'accept'}]
                if w['mission']['status']=='completed':break
                w=m.advance(w,orders,'30分钟')
            self.assertEqual(w['mission']['winner'],'red',profile)

    def test_rail_underground_air_effects(self):
        w=self.seed('rail');w['mission']['network']=0
        n=m.advance(w,[],'30分钟');self.assertEqual(sum(r['delivery'] for r in n['mission']['ledger'][3:]),0)
        w=self.seed('underground');bare=w;concealed=w
        for _ in range(4):
            bare=m.advance(bare,[],'30分钟')
            concealed=m.advance(concealed,[{'unit':u['name'],'action':'conceal'} for u in concealed['red_units']],'30分钟')
        self.assertEqual(bare['mission']['winner'],'blue');self.assertEqual(concealed['mission']['winner'],'red')
        w=self.seed('air');supported=m.advance(w,[{'unit':w['red_units'][0]['name'],'action':'air_support'}],'30分钟')
        self.assertEqual(sum(x['delivery'] for x in supported['mission']['ledger'][3:]),3)
        self.assertEqual(sum(x['delivery'] for x in m.advance(w,[],'30分钟')['mission']['ledger'][3:]),6)

    def test_ceasefire_gate(self):
        w=self.seed('reception');r=w['red_units'][0];r.update(w['mission']['definition']['objective'])
        with self.assertRaises(ValueError):m.advance(w,[{'unit':r['name'],'action':'accept'}],'30分钟')
        w=m.advance(m.advance(w,[],'30分钟'),[],'30分钟')
        with self.assertRaises(ValueError):m.advance(w,[{'unit':r['name'],'action':'fire','target':w['blue_units'][0]['name']}],'30分钟')


if __name__=='__main__':unittest.main(verbosity=2)
