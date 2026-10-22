"""Read-only, per-field checks for a teaching campaign; not historical validation.

Run during or after live_campaign_soak.py. Evidence contains reports and state
summaries, never native reasoning text or model credentials.
"""
import argparse
from collections import Counter
import json
import math
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gentactix import adjudicator, battlefield, causal


def finite(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def position(v):
    if isinstance(v, dict):
        v = [v.get('lat'), v.get('lng')]
    return (list(v) if isinstance(v, (list, tuple)) and len(v) == 2
            and all(finite(x) for x in v) and abs(v[0]) <= 90 and abs(v[1]) <= 180 else None)


def distance_m(a, b):
    return causal.distance(a, b) * 1000 if a and b else None


def units(world):
    return {(team, u.get('name', '')): u for team in ('red', 'blue')
            for u in world.get(team + '_units', []) if isinstance(u, dict)}


def audit_record(record, previous=None, initial=None, raw=None):
    after = record['after']; before = record.get('before') or {}
    now, old, baseline = units(after), units(before), units(initial or after)
    issues = []
    def flag(category, code, detail, severity='warning'):
        issues.append(dict(category=category, code=code, detail=detail, severity=severity))
    turn = record['turn']
    if after.get('simulation_clock', {}).get('elapsed_seconds') != turn * 900:
        flag('clock', 'wrong_elapsed', after.get('simulation_clock'), 'error')
    if previous is not None and before != previous:
        flag('continuity', 'snapshot_gap', 'before与上轮after不一致', 'error')
    if turn:
        check = causal.clock_check(before, after, record['time_step'])
        if check.get('status') != 'verified': flag('clock', 'step_mismatch', check, 'error')
    for team in ('red', 'blue'):
        names = [u.get('name') for u in after.get(team+'_units', [])]
        if not all(names) or len(names) != len(set(names)):
            flag('roster', 'invalid_names', team, 'error')
    unit_rows = []
    for key, u in now.items():
        name = key[1]; prev = old.get(key, {}); base = baseline.get(key, {})
        for field in ('name','type','lat','lng','hp','morale','supply','combat_power','speed','attack_range'):
            if field not in u or u[field] is None: flag('units','missing_field',f'{name}.{field}', 'error')
        if not position(u): flag('units','invalid_position',name,'error')
        for field in ('hp','morale','supply'):
            if not finite(u.get(field)) or not 0 <= u[field] <= 100:
                flag('units','out_of_scale',f'{name}.{field}={u.get(field)}','error')
        for field in ('combat_power','speed','attack_range'):
            if not finite(u.get(field)) or u[field] < 0:
                flag('units','negative_or_unknown',f'{name}.{field}={u.get(field)}','error')
        p = u.get('personnel') or {}; prevp = prev.get('personnel') or {}
        fields = battlefield.PERSONNEL_FIELDS
        if not all(type(p.get(k)) is int and p[k] >= 0 for k in fields):
            flag('personnel','missing_or_invalid_counts',name,'error')
        else:
            if p['committed'] != p['current'] + p['casualties'] + p['noncombat_losses']:
                flag('personnel','accounting_difference',{'unit':name,'counts':p},'error')
            if base and p['committed'] != (base.get('personnel') or {}).get('committed'):
                flag('personnel','unplanned_reinforcements',name,'error')
            for field in ('casualties','noncombat_losses'):
                if type(prevp.get(field)) is int and p[field] < prevp[field]:
                    flag('personnel','cumulative_loss_reversed',name+'.'+field,'error')
            if any(p.get(f,0) > prevp.get(f,0) for f in ('casualties','noncombat_losses')) and not any(w in str(p.get('source')) for w in ('模型','推演','估计','估算')):
                flag('provenance','estimated_loss_source_not_labeled',name+'.personnel')
        equipment = u.get('equipment') or []
        if not equipment: flag('equipment','missing_ledger',name,'error')
        old_eq = {(e.get('name'),e.get('unit')): e for e in prev.get('equipment', [])}
        base_eq = {(e.get('name'),e.get('unit')): e for e in base.get('equipment', [])}
        identities = [(e.get('name'),e.get('unit')) for e in equipment]
        if len(identities) != len(set(identities)): flag('equipment','duplicate_category',name,'error')
        for e in equipment:
            identity=(e.get('name'),e.get('unit')); title=name+'.'+str(identity[0]);old_e=old_eq.get(identity,{})
            if not all(type(e.get(k)) is int and e[k]>=0 for k in battlefield.EQUIPMENT_FIELDS):
                flag('equipment','missing_or_invalid_counts',title,'error');continue
            if e['committed'] != e['operational']+e['damaged']+e['destroyed']:
                flag('equipment','accounting_difference',{'unit':title,'counts':e},'error')
            if identity not in base_eq or e['committed'] != base_eq[identity]['committed']:
                flag('equipment','unplanned_inventory_change',title,'error')
            if old_e.get('destroyed',0)>e['destroyed']:flag('equipment','permanent_loss_reversed',title,'error')
        unit_rows.append({'team':key[0],'name':name,'hp':u.get('hp'),'hp_change':u.get('hp',0)-prev.get('hp',u.get('hp',0)),
                          'personnel':p,'equipment':equipment,'morale':u.get('morale'),'supply':u.get('supply'),
                          'position':position(u),'combat_power':u.get('combat_power'),
                          'speed':u.get('speed'),'attack_range':u.get('attack_range'),'fire_sector':u.get('fire_sector'),
                          'moved_m':distance_m(position(prev),position(u))})
    by_name={u['name']:u for u in now.values()}
    old_by_name={u['name']:u for u in old.values()}
    engagements=after.get('engagements')
    if not isinstance(engagements,list):
        flag('engagements','missing_list','engagements','error' if turn else 'warning');engagements=[]
    for index,e in enumerate(engagements):
        attacker=by_name.get(e.get('attacker')) or old_by_name.get(e.get('attacker'))
        if not attacker:flag('engagements','unknown_attacker',e.get('attacker'),'error');continue
        prev=old_by_name.get(e.get('attacker'),attacker)
        if prev.get('hp',1)<=0:flag('engagements','disabled_unit_attacks',e['attacker'],'error')
        if prev.get('supply') == 0:
            flag('engagements','zero_supply_at_start_still_fires',e['attacker'])
        a=position(e.get('attacker_pos'));target=position(e)
        if not a or not target:flag('engagements','invalid_geometry',index,'error');continue
        actual=distance_m(a,target);rng=attacker.get('attack_range')
        if finite(rng) and actual > rng+30:
            flag('engagements','range_exceeded',{'attacker':e['attacker'],'distance_m':actual,'range_m':rng},'error')
        sector=attacker.get('fire_sector') or {}
        bearing,arc=sector.get('bearing_deg'),sector.get('arc_deg')
        if finite(bearing) and finite(arc):
            direction=adjudicator.bearing_deg(a,target)
            offset=abs((direction-bearing+180)%360-180)
            if offset>arc/2+1:
                flag('sectors','shot_outside_displayed_sector',
                     {'attacker':e['attacker'],'bearing':direction,'sector':sector})
        known=[distance_m(a,position(x)) for x in (attacker,prev) if position(x)]
        if known and min(known)>50:flag('engagements','attacker_position_unmatched',e['attacker'])
    guards=record.get('guards') or {};roster=guards.get('roster') or {}
    for field in ('recovered','merged','suspected','duplicate','structure_missing'):
        if roster.get(field):flag('roster','guard_'+field,roster[field])
    for item in guards.get('numeric_flags',[]):flag('units','numeric_guard',item)
    for item in guards.get('movement_violations',[]):flag('movement','movement_limit',item,'error')
    nodes={n.get('id'):n for n in after.get('supply_nodes',[]) if isinstance(n,dict)}
    geometry = causal.geometry(after, record) if 'time_step' in record else {}
    geometry_units={(u['team'],u['name']):u for u in geometry.get('units',[])}
    before_geometry = causal.geometry(before, record) if before and 'time_step' in record else {}
    before_units={(u['team'],u['name']):u for u in before_geometry.get('units',[])}
    supply_comparisons=[]
    for key,u in now.items():
        prior=old.get(key,{})
        if finite(prior.get('supply')) and prior['supply']<20 and key in before_units:
            moved=distance_m(position(prior),position(u))
            half_limit=before_units[key]['reach_km']*500
            if moved is not None and moved>half_limit+10:
                flag('movement','low_supply_half_speed_not_enforced',
                     {'unit':key[1],'supply_before':prior['supply'],
                      'moved_m':moved,'half_limit_m':half_limit})
        node_id=u.get('supply_node_id')
        if node_id and node_id not in nodes:flag('supply','unknown_node',key[1], 'error')
        elif node_id and nodes[node_id].get('team')!=key[0]:flag('supply','opposing_node',key[1], 'error')
        node=nodes.get(node_id); verdict=geometry_units.get(key,{})
        path=verdict.get('supply_path') or []
        if node and position(node) and path and position(path[0]):
            gap=distance_m(position(node),position(path[0]))
            supply_comparisons.append({'unit':key[1],'declared_node':node_id,
                'declared_position':position(node),'kernel_rear':path[0],
                'source_separation_m':gap,'kernel_supply':verdict.get('supply')})
            if gap > 100:
                flag('supply','declared_and_inferred_source_differ',
                     {'unit':key[1],'separation_m':gap})
        sector=u.get('fire_sector')
        if sector:
            bearing,arc=sector.get('bearing_deg'),sector.get('arc_deg')
            if not finite(bearing) or not 0<=bearing<360 or not finite(arc) or not 0<arc<=360:
                flag('sectors','invalid_angles',key[1], 'error')
    for route in after.get('supply_routes',[]):
        path=route.get('waypoints',[])
        if len(path)<2 or not all(position(p) for p in path):flag('supply','invalid_route',route.get('name'), 'error')
        if route.get('unit_name') and route['unit_name'] not in by_name:
            flag('supply','route_to_inactive_unit',route.get('unit_name'))
        if not route.get('source'):flag('provenance','route_source_missing',route.get('name'))
        verdict=geometry_units.get((route.get('team'),route.get('unit_name')), {})
        declared=route.get('status'); calculated=verdict.get('supply')
        if (declared=='open' and calculated=='已切断') or (declared=='cut' and calculated=='未切断'):
            flag('supply','displayed_route_and_kernel_status_differ',
                 {'unit':route.get('unit_name'),'displayed':declared,'kernel':calculated})
    previous_features={f.get('name'):f for f in before.get('terrain_features',[])}
    for feature in after.get('terrain_features',[]):
        if not feature.get('source'):flag('provenance','terrain_source_missing',feature.get('name'))
        points=feature.get('coordinates',[])
        if not points or not all(position(p) for p in points):
            flag('terrain','invalid_geometry',feature.get('name'),'error')
        prior=previous_features.get(feature.get('name'))
        if prior and prior.get('coordinates') != points:
            flag('terrain','same_named_geometry_changed',feature.get('name'))
        if feature.get('kind')=='ridge' and '射界' in str(feature.get('name')):
            flag('terrain','fire_annotation_classified_as_ridge',feature.get('name'))
    view=battlefield.build_battlefield_view(after,previous=before,trajectory=[record])
    for warning in view.get('warnings',[]):flag('display','layer_warning',warning)
    for row in view.get('data_quality',[]):
        if row['status']!='available':flag('display','data_missing',row['label'])
    if not record.get('report'):flag('report','missing_report',turn,'error')
    label=re.search(r'T\+(\d+)',record.get('report',''))
    if label and int(label[1])!=turn:flag('report','turn_label_mismatch',label[0],'error')
    clock=re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}',record.get('report',''))
    if clock and clock[0]!=after.get('current_time'):
        flag('report','clock_label_mismatch',clock[0],'error')
    decisions=record.get('decisions') or []
    if turn and not decisions:flag('decisions','missing_decisions',turn)
    for decision in decisions:
        name=decision.get('unit_name')
        if name and name not in by_name and name not in old_by_name:
            flag('decisions','unknown_unit',name,'error')
        if not decision.get('action') or not decision.get('rationale'):
            flag('decisions','missing_action_or_basis',name)
        if record.get('id') not in decision.get('evidence_ids',[]):
            flag('decisions','missing_trace_reference',name, 'error')
    for run in record.get('runs',[]):
        if run.get('finish_reason')!='completed':flag('trace','unfinished_run',run.get('finish_reason'),'error')
    raw_missing=[]
    if raw is not None:
        candidate=raw.get('updated_state',raw)
        raw_index=units(candidate)
        for key,u in now.items():
            src=raw_index.get(key,{})
            missing=[k for k in ('name','type','lat','lng','hp','morale','supply','personnel','equipment') if k not in src or src[k] is None]
            if missing:raw_missing.append({'unit':key[1],'fields':missing})
        if raw_missing:flag('raw_output','missing_fields_before_guards',raw_missing)
    return {'turn':turn,'time':after.get('current_time'),'unit_count':len(now),
            'units':unit_rows,'engagement_count':len(engagements),'decision_count':len(decisions),
            'report':record.get('report',''),'source_note':(raw or {}).get('source_note', (raw or {}).get('updated_state',{}).get('source_note')),
            'field_inventory':sorted(after),'display_quality':view.get('data_quality'),
            'supply_comparisons':supply_comparisons,
            'issues':issues,'raw_missing':raw_missing,
            'structural_pass':not any(i['severity']=='error' for i in issues)}


def raw_outputs(home, session_ids=None):
    results={};policies=[];event_counts=Counter();tool_results=[];messages=[];compactions=[];started={}
    for path in home.glob('sessions/*/*/session.v4.jsonl'):
        if session_ids is not None and path.parent.name not in session_ids:
            continue
        for line in path.open():
            if not line.endswith('\n'):continue
            try:e=json.loads(line)
            except ValueError:continue
            d=e.get('data') or {}
            event_counts[e.get('type','unknown')]+=1
            if e.get('type')=='compaction/start':
                started[(path.parent.name,d.get('compactionId'))]=e
            if e.get('type')=='compaction/end':
                start=started.get((path.parent.name,d.get('compactionId')))
                if start:
                    compactions.append({'session_id':path.parent.name,'first_seq':start['seq'],
                        'last_seq':e['seq'],'seconds':(e['time']-start['time'])/1000})
            if e.get('type')=='request/header':
                c=d.get('header',{}).get('config',{})
                policies.append({'session_id':path.parent.name,'seq':e.get('seq'),'max_tokens':c.get('maxTokens'),'reasoning_effort':c.get('reasoningEffort')})
            if e.get('type')=='tool/result':
                m=d.get('message',{})
                for block in m.get('content',[]):
                    if block.get('type')!='text':continue
                    try:result=json.loads(block.get('text',''))
                    except ValueError:continue
                    if isinstance(result,dict):
                        tool_results.append({'session_id':path.parent.name,'seq':e.get('seq'),
                            'tool':result.get('tool'),'ok':result.get('ok'),
                            'unknown_count':len(result.get('unknown',[])),
                            'applied_count':len(result.get('applied',[])) if 'applied' in result else None,
                            'native_error':bool(m.get('isError'))})
            if e.get('type')!='assistant/message':continue
            messages.append({'session_id':path.parent.name,'seq':e.get('seq'),'usage':d.get('usage'),
                             'reasoning_chars':sum(len(b.get('text','')) for b in d.get('message',{}).get('content',[]) if b.get('type')=='reasoning')})
            text='\n'.join(b.get('text','') for b in d.get('message',{}).get('content',[]) if b.get('type')=='text')
            if '{' not in text:continue
            try:value=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
            except ValueError:continue
            if isinstance(value,dict):results[(path.parent.name,e.get('seq'))]=value
    return results,policies,{'event_counts':dict(event_counts),'tool_results':tool_results,
                            'messages':messages,'compactions':compactions}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-dir',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    checkpoints=list((args.live_dir/'sessions').glob('*.json'))
    assert len(checkpoints)==1,'Expected one isolated battle'
    session=json.loads(checkpoints[0].read_text())['session'];records=session.get('trajectory',[])
    session_ids={run['session_id'] for record in records for run in record.get('runs',[])}
    raw,policies,native=raw_outputs(args.live_dir/'dsh',session_ids);rows=[];previous=None
    for record in records:
        candidate=None
        for run in reversed(record.get('runs',[])):
            matching=[(seq,value) for (sid,seq),value in raw.items() if sid==run['session_id'] and run.get('first_seq',0)<=seq<=run.get('last_seq',0)]
            if matching:candidate=max(matching,key=lambda x:x[0])[1];break
        row=audit_record(record,previous,records[0]['after'],candidate);rows.append(row);previous=record['after']
    counts=Counter(i['code'] for r in rows for i in r['issues'])
    report={'completed_turns':session['turn_count'],'structural_pass':all(r['structural_pass'] for r in rows),
            'historical_accuracy_verified':False,'narrative_requires_review':True,
            'scope':'8单位、无增援、HP/士气/补给0–100的指定教学想定；不泛用于任意战役计数口径。',
            'issue_counts':dict(counts),'policies':policies,'native':native,'failed_operations':len(session.get('failed_operations',[])),
            'rows':rows}
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','policies','native')},ensure_ascii=False))
    if not report['structural_pass']:
        raise SystemExit(1)


if __name__=='__main__':main()
