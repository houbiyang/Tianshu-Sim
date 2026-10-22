"""Real DSH acceptance of the 20 synthetic local exercises (no response fixtures).
Use --workers for independent runtimes. Resumable result files live under --out.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def comparable(value):
    # The common roster guard annotates completeness without changing counters.
    if isinstance(value, dict): return {k: comparable(v) for k,v in value.items() if k != 'carried_forward'}
    if isinstance(value, list): return [comparable(v) for v in value]
    return value


def run_one(args):
    identity, output = args
    directory=Path(output)/identity; directory.mkdir(parents=True, exist_ok=True)
    result_path=directory/'acceptance.json'
    if result_path.exists():
        prior=json.loads(result_path.read_text())
        if prior.get('passed'): return prior
        (directory / ('failed-attempt-' + str(time.time_ns()) + '.json')).write_text(json.dumps(prior,ensure_ascii=False,indent=2))
    for key,suffix in [('SESSION_DIR','sessions'),('TRACE_DIR','traces'),('DSH_HOME','dsh-home'),('DSH_WORKSPACE','workspace'),('ROLE_INDEX','roles.json')]:
        os.environ['GENTACTIX_'+key]=str(directory/suffix)
    # Imports happen in fresh spawned workers, after isolation settings.
    from fastapi.testclient import TestClient
    from backend import main, harness_engine as h, session_store
    from gentactix import missions as m, sim_clock
    spec=m.definition(identity)
    result={'id':identity,'battle':spec['battle'],'profile':spec['profile'],'kind':'real_dsh_synthetic_local',
            'definition_hash':m.digest(spec),'model':h.LLM_MODEL,'policy':h.role_policy('engine'),
            'passed':False,'turns':[],'failures':[], 'limitations':['教学参数未史实标定；不是整场历史战役验收']}
    client=TestClient(main.app)
    start=time.monotonic()
    try:
        restored, errors = session_store.load_all()
        if errors: raise AssertionError('invalid checkpoints: ' + str(errors))
        candidates = [v for v in restored.values() if v.get('mission_id') == identity and v.get('war_state')]
        if candidates:
            saved = max(candidates, key=lambda v: v['turn_count'])
            sid = saved['id']; main._SESSIONS[sid] = saved
            initial = copy.deepcopy(saved['trajectory'][0]['after'])
            committed = {r['turn']: r for r in saved['trajectory'] if r['turn'] > 0}
        else:
            r=client.post('/api/sessions',json={'scenario':spec['title'],'battle':spec['battle'],
                'mission_id':identity,'time_step':spec['time_step'],'use_rag':True,'use_adjudicator':True,
                'persist_roles':True,'use_agent_tools':False,'agent_mode':'json'})
            assert r.status_code==200,r.text
            sid=r.json()['session_id']
            r=client.post(f'/api/sessions/{sid}/init');assert r.status_code==200,r.text
            initial=r.json()['war_state']; committed={}
        result['session_id']=sid;previous=initial
        for turn in range(1,spec['deadline_turn']+1):
            if previous['mission']['status']=='completed':break
            tick=time.monotonic(); reused=turn in committed
            if reused:
                world=copy.deepcopy(committed[turn]['after'])
                _, rag_meta=main._retrieve_briefing(main._SESSIONS[sid],spec['battle'],'step')
            else:
                r=client.post(f'/api/sessions/{sid}/next',json={'instruction':
                    '红方积极争取本局程序目标；蓝方采取合理对抗，不主动放弃。根据当前距离、库存、目标类型选择合法动作。未射程内先移动，不强行射击。'})
                if r.status_code!=200: raise AssertionError('next '+str(r.status_code)+': '+r.text[:1200])
                snap=r.json();world=snap['war_state'];rag_meta=snap['rag']['last']
            m.validate(world)
            replay=m.advance(previous,world['mission']['last_orders'],spec['time_step'])
            # Common backend guards add observation fields; compare authoritative counters/state only.
            for field in ('red_units','blue_units','mission','supply_nodes','simulation_clock'):
                assert comparable(world[field])==comparable(replay[field]), 'replay mismatch '+field
            assert len(world['red_units'])==len(previous['red_units']) and len(world['blue_units'])==len(previous['blue_units'])
            assert world['current_time']==sim_clock.target(previous,spec['time_step'])[0]
            assert {'推演目标、关键决策与反事实','争议与待考','兵种、装备与补给'} <= {x['section'] for x in rag_meta['hits']}
            alternative=[o for o in world['mission']['last_orders'] if o['unit'].startswith('蓝方')]
            live_before=client.get(f'/api/sessions/{sid}').json()['war_state']
            cf=client.post(f'/api/sessions/{sid}/counterfactual',json={'turn':turn,'mode':'mission','orders':alternative})
            assert cf.status_code==200,cf.text
            assert client.get(f'/api/sessions/{sid}').json()['war_state']==live_before,'branch mutated live state'
            differences=[x for x in cf.json()['metrics'] if x['delta']]
            moved=any((u['lat'],u['lng'])!=(v['lat'],v['lng']) for u,v in zip(world['red_units'],cf.json()['alternative']['red_units']))
            record=next(r for r in main._SESSIONS[sid]['trajectory'] if r['turn']==turn)
            assert record['runs'] and record['decisions'],'missing real trace or decision'
            audit=client.get(f'/api/sessions/{sid}/traces/{turn}'); assert audit.status_code==200,audit.text
            evidence=audit.json()['persistence']
            assert evidence and all(e['status']=='verified' for e in evidence), 'DSH persistence not fully verified'
            result['turns'].append({'turn':turn,'reused_trace':reused,'seconds':round(time.monotonic()-tick,2),
                'orders':world['mission']['last_orders'],'metrics':world['mission']['metrics'],
                'decision_differences':differences, 'movement_difference':moved,
                'clock':world['current_time'],'replayed':True,'balanced':True,'dsh':audit.json()['persistence']})
            previous=world
            result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        assert previous['mission']['status']=='completed','objective not settled'
        # Supply intervention on the identical saved initial state, no live-state modification.
        depleted=copy.deepcopy(initial)
        for node in depleted['supply_nodes']: node['stock']=0
        for _,u in m.units(depleted):u['logistics'].update(food=0,ammo=0)
        dry=m.advance(depleted,[],spec['time_step'])
        assert all(u['personnel']['noncombat_losses']==2 for _,u in m.units(dry))
        session_store.validate(session_store.serializable(main._SESSIONS[sid]));session_store.save(main._SESSIONS[sid])
        restored,errors=session_store.load_all();assert not errors,errors
        assert restored[sid]['war_state']==previous
        assert any(x['decision_differences'] or x['movement_difference'] for x in result['turns']), 'no tested decision difference'
        result.update(passed=True,objective=previous['mission']['reason'],winner=previous['mission']['winner'],
                      supply_constraint=True,checkpoint_replay=True)
    except Exception as exc:
        result['failures'].append(str(exc)[:2000])
    finally:
        h.close_harness()
        result['seconds']=round(time.monotonic()-start,2)
        result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    import multiprocessing
    from gentactix import missions
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(ROOT/'.cache/mission-acceptance'))
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--ids',nargs='*')
    args=parser.parse_args(); output=Path(args.out).resolve();output.mkdir(parents=True,exist_ok=True)
    ids=args.ids or [s['id'] for s in missions.catalog()]
    results=[]
    # One process per scenario prevents SDK global state or shared server locks crossing scenes.
    with multiprocessing.get_context('spawn').Pool(processes=args.workers,maxtasksperchild=1) as pool:
        for row in pool.imap_unordered(run_one,[(i,str(output)) for i in ids]):
            results.append(row)
            print(row['id'], 'PASS' if row['passed'] else 'FAIL',len(row['turns']),'turns',row.get('failures'),flush=True)
            (output/'summary.json').write_text(json.dumps(sorted(results,key=lambda r:r['id']),ensure_ascii=False,indent=2))
    return 0 if all(r['passed'] for r in results) else 1


if __name__=='__main__':sys.exit(main())
