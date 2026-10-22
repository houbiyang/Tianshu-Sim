"""API contract tests; model orders are fixtures, not real-model acceptance."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from backend import main, harness_engine as h, session_store
    from gentactix import missions as m
    BACKEND=True
except ImportError:
    BACKEND=False


@unittest.skipUnless(BACKEND,'needs wargame environment')
class MissionAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'GENTACTIX_SESSION_DIR':self.temp.name+'/sessions', 'GENTACTIX_TRACE_DIR':self.temp.name+'/traces'})
        self.env.start();self.client=TestClient(main.app)
        self.spec=m.definition('AJ15-local-v1')
        self.payload={'scenario':self.spec['title'],'battle':self.spec['battle'],'mission_id':self.spec['id'],
            'simulation_mode':'open','use_rag':True,'use_adjudicator':True,'persist_roles':False,'agent_mode':'json'}
        self.sid=None
    def tearDown(self):
        if self.sid:main._SESSIONS.pop(self.sid,None)
        self.env.stop();self.temp.cleanup()
    def start(self):
        r=self.client.post('/api/sessions',json=self.payload);self.assertEqual(r.status_code,200,r.text)
        self.sid=r.json()['session_id']
        with patch.object(h,'get_ai_response_with_retry',side_effect=AssertionError('init must be deterministic')):
            r=self.client.post('/api/sessions/'+self.sid+'/init')
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_catalog_and_modes(self):
        r=self.client.get('/api/missions');self.assertEqual(len(r.json()['missions']),20)
        self.assertNotIn('initial_state',r.json()['missions'][0])
        for changed in ({'simulation_mode':'replay'},{'simulation_mode':'bad'},{'mission_id':'bad'}, {'use_agent_tools':True}, {'use_live_intel':True}):
            self.assertEqual(self.client.post('/api/sessions',json={**self.payload,**changed}).status_code,422)
    def test_checkpoint_and_terminal_goal(self):
        first=self.start();self.assertEqual(first['time_step'],'30分钟')
        self.assertEqual(first['settings']['simulation_mode'],'open')
        self.assertEqual(first['war_state']['mission'],m.initial(self.spec['id'])['mission'])
        orders=[{'unit':u['name'],'action':'conceal'} for u in first['war_state']['red_units']]
        with patch.object(h,'get_ai_response',return_value={'orders':orders,'reason':'保持组织'}) as model:
            for _ in range(4):
                r=self.client.post(f'/api/sessions/{self.sid}/next',json={'instruction':'隐蔽保存组织'})
                self.assertEqual(r.status_code,200,r.text)
            self.assertIn('隐蔽保存组织',model.call_args.args[0])
        final=r.json();self.assertEqual(final['war_state']['mission']['winner'],'red')
        self.assertEqual(self.client.post(f'/api/sessions/{self.sid}/next').status_code,409)
        saved=main._SESSIONS[self.sid];session_store.validate(session_store.serializable(saved))
        loaded,errors=session_store.load_all();self.assertFalse(errors)
        self.assertEqual(loaded[self.sid]['war_state'],final['war_state'])
        main._SESSIONS[self.sid] = loaded[self.sid]
        resumed=self.client.get(f'/api/sessions/{self.sid}').json()
        self.assertEqual(resumed['adjudication']['spec']['source_kind'],'local_teaching')
        self.assertEqual(resumed['adjudication']['last']['control_radius_km'],.25)
        for trace in saved['trajectory'][1:]:self.assertEqual(len(trace['decisions']),3)
    def test_invalid_orders_atomic_retry(self):
        first=self.start()
        with patch.object(h,'get_ai_response',return_value={'orders':[{'unit':'bad','action':'hold'}]}) as model:
            r=self.client.post(f'/api/sessions/{self.sid}/next')
            self.assertEqual(model.call_count,2);self.assertEqual(r.status_code,502,r.text)
        current=self.client.get(f'/api/sessions/{self.sid}').json()
        self.assertEqual(current['war_state'],first['war_state']);self.assertEqual(current['turn_count'],0)
    def test_command_counterfactual_read_only(self):
        first=self.start();orders=[{'unit':u['name'],'action':'conceal'} for u in first['war_state']['red_units']]
        with patch.object(h,'get_ai_response',return_value={'orders':orders}):
            r=self.client.post(f'/api/sessions/{self.sid}/next');self.assertEqual(r.status_code,200,r.text)
        factual=r.json()['war_state']
        r=self.client.post(f'/api/sessions/{self.sid}/counterfactual',json={'turn':1,'mode':'mission','orders':[]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['alternative']['mission']['organization'],85)
        self.assertEqual(self.client.get(f'/api/sessions/{self.sid}').json()['war_state'],factual)
        r=self.client.post(f'/api/sessions/{self.sid}/counterfactual',json={'turn':1,'mode':'model','instruction':'改变'})
        self.assertEqual(r.status_code,422)


if __name__=='__main__':unittest.main(verbosity=2)
