"""Unit roles on DSH's native registry; only wargame observations and proposals."""
import copy
import json

from gentactix import commands
from . import harness_engine as h


def plan(session, prepared, observer):
    cs = prepared['state']
    controlled = {x['command']['unit_id'] for x in prepared['manual']} | {x['unit_id'] for x in prepared['blocked'] + prepared['expired']}
    units = commands.resolve_units(session, cs)
    orders, decisions = [], []
    bindings = cs.setdefault('agent_sessions', {})
    observation = commands.view(session)
    for identity in cs['unit_agents']:
        if identity in controlled:
            continue
        pair = units.get(identity)
        if not pair or pair[0].get('ambiguous') or not commands.available(pair[1]):
            continue
        entry, unit = pair
        if not cs['director'] and entry['team'] != cs['control_team']:
            raise ValueError('单位Agent越过控制方权限')
        if observer.cancel_requested or session.get('cancel_requested'):
            raise RuntimeError('用户取消，单位候选不提交')
        # All currently displayed units are known in this version. No fog/LOS claim.
        context = {'session': {k: copy.deepcopy(session.get(k)) for k in
            ('war_state', 'use_rag', 'scenario_input', 'battle_choice', 'history', 'trajectory', 'simulation_mode')}, 'pre_adj': None}
        prompt = ('你是以下唯一单位的原生DSH智能体。仅提出该单位本轮一项行动，不写权威状态。'
            '前次会话文字可能是未提交候选，以本次权威观察为准；禁止继承别的角色提示。'
            '只能使用当前capabilities.actions；全局硬限制优先，意图是软偏好。'
            'move必须在move_km内；fire需合法敌方target_id。不要输出内部推理。'
            '仅返回JSON {"order":{"unit_id":"指定id","action":"hold|move|fire|...",'
            '"duration":"next","destination":{"lat":0,"lng":0},"target_id":"敌方id"},'
            '"rationale":"本单位公开依据","expected_effect":"预期"}；不用的字段省略。\n'
            + json.dumps({'unit': next(x for x in observation['units'] if x['id'] == identity),
                'known_units': observation['units'], 'mission': session['war_state'].get('mission'),
                'current_time': session['war_state'].get('current_time'), 'time_step': session['time_step'],
                'no_fire': cs['no_fire'] and entry['team'] == cs['control_team'],
                'intent': cs['intent'], 'observation_version': prepared['world_version']}, ensure_ascii=False))
        start = len(observer.runs)
        run, binding = h.unit_run(bindings.get(identity), prompt, observer, context=context,
            namespace=f"{session['id']}-e{session.get('role_epoch', 0)}-{identity}")
        parsed = h.parse_json_object(run.final_response)
        draft = parsed.get('order')
        if not isinstance(draft, dict) or draft.get('unit_id') != identity:
            raise ValueError('原生Agent输出越过了绑定单位身份')
        validated = commands.replace_queue(session, [draft], control_team=cs['control_team'],
            director=cs['director'], no_fire=cs['no_fire'], intent=cs['intent'])
        raw = commands.step_order(session, validated['orders'][0], cs)
        orders.append(raw)
        decisions.append({'team': entry['team'], 'unit_name': entry['name'], 'unit_id': identity,
            'action': raw['action'], 'rationale': str(parsed.get('rationale') or ''),
            'expected_effect': str(parsed.get('expected_effect') or ''), 'source': 'native_unit_agent',
            'run_ids': [r['id'] for r in observer.runs[start:]]})
        bindings[identity] = binding
    return orders, decisions
