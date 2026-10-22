"""Read-only causal evidence and controlled geometry interventions (stdlib only).

An ablation measures a single-step controlled effect under the existing geometry
kernel. It is not a fitted casualty model, historical causal proof or a percentage
allocation of victory. Stochastic model branches are reported separately.
"""
import copy
import math
import re
from datetime import datetime

from . import adjudicator
from .state import extract_lat_lng, get_unit_field, normalize_state_units

VERSION = 'causal-1'
VERDICT_FIELDS = ('can_contact', 'supply', 'encirclement', 'retreat')
SCOPE = ('归因试算仅重算机动接触、几何补给截断、包围与退路。固定其他单位终态和环境；'
         '不重算伤亡、弹药、道路运量或胜负。单因素效应不能直接相加，模型解读属于待验证假说。')


def unit_index(state):
    return {(team, str(get_unit_field(u, 'name', '名称'))): u
            for key, team in (('red_units', 'red'), ('blue_units', 'blue'))
            for u in (state or {}).get(key, []) if isinstance(u, dict)}


def distance(a, b):
    if None in (*a, *b):
        return None
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b[1]-a[1])/2)**2
    return round(6371 * 2 * math.asin(min(1, math.sqrt(h))), 3)


def state_changes(before, after):
    old, new = unit_index(before), unit_index(after)
    rows = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        fields = {}
        for field in ('lat', 'lng', 'hp', 'supply', 'morale', 'personnel', 'equipment'):
            av, bv = (a or {}).get(field), (b or {}).get(field)
            if av != bv:
                fields[field] = {'before': av, 'after': bv}
        moved = distance(extract_lat_lng(a), extract_lat_lng(b)) if a and b else None
        if fields or a is None or b is None:
            rows.append({'team': key[0], 'name': key[1], 'fields': fields,
                         'moved_km': moved, 'before_position': list(extract_lat_lng(a)) if a else None,
                         'after_position': list(extract_lat_lng(b)) if b else None,
                         'status': 'added' if a is None else 'removed' if b is None else 'changed'})
    return rows


def verdict_changes(factual, alternative):
    a = {(u['team'], u['name']): u for u in (factual or {}).get('units', [])}
    b = {(u['team'], u['name']): u for u in (alternative or {}).get('units', [])}
    return [{'team': key[0], 'name': key[1], 'field': field,
             'factual': a[key].get(field), 'alternative': b[key].get(field)}
            for key in sorted(a.keys() & b.keys()) for field in VERDICT_FIELDS
            if a[key].get(field) != b[key].get(field)]


def metrics(verdict):
    rows = (verdict or {}).get('units', [])
    out = {}
    for team in ('red', 'blue'):
        units = [r for r in rows if r['team'] == team]
        out[team + '.contact'] = sum(bool(u.get('can_contact')) for u in units)
        out[team + '.cut'] = sum(u.get('supply') == '已切断' for u in units)
        out[team + '.encircled'] = sum(u.get('encirclement') == '被围' for u in units)
        out[team + '.retreat_blocked'] = sum(u.get('retreat') == '已断' for u in units)
    return out


def compare_metrics(factual, alternative):
    a, b = metrics(factual), metrics(alternative)
    return [{'metric': k, 'factual': a[k], 'alternative': b[k], 'delta': b[k]-a[k]} for k in a]


def geometry(state, record):
    return adjudicator.adjudicate(copy.deepcopy(state), record['time_step'],
                                  turn=record['turn'], params=record.get('params') or {})


def clock_check(before, after, time_step):
    from . import sim_clock
    if (before or {}).get('simulation_clock'):
        try:
            return sim_clock.enforce_step(before, copy.deepcopy(after), time_step)
        except ValueError as exc:
            return {'status': 'mismatch', 'source': 'program_clock', 'note': str(exc)}
    def parse(value):
        text = str(value)
        date = re.search(r'(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?', text)
        if not date:
            return None
        tail = text[date.end():]
        if '前' in tail or '后' in tail:
            return None
        clock = re.search(r'(\d{1,2})(?:[:：](\d{2})|时(?:(\d{1,2})分?)?)', tail)
        if not clock:
            return None
        try:
            hour = int(clock[1]); minute = int(clock[2] or clock[3] or 0)
            if any(word in tail for word in ('下午', '晚上', '傍晚')) and hour < 12:
                hour += 12
            return datetime(*map(int, date.groups()), hour, minute)
        except ValueError:
            return None
    a, b = parse((before or {}).get('current_time')), parse((after or {}).get('current_time'))
    expected = adjudicator.parse_time_step(time_step) * 60
    if a is None or b is None:
        return {'status': 'unknown', 'note': '时间文字不能可靠换算为钟点，不能验证时间跨度'}
    minutes = (b-a).total_seconds()/60
    return {'status': 'verified' if abs(minutes-expected) < .01 else 'mismatch',
            'expected_minutes': expected, 'actual_minutes': minutes}


def decisions_from(result, before, after, trace_id):
    known = unit_index(before) | unit_index(after)
    decisions = []
    raw = result.get('decisions') or []
    if not isinstance(raw, list):
        return []
    for item in raw[:16]:
        if not isinstance(item, dict) or item.get('team') not in ('red', 'blue'):
            continue
        unit = str(item.get('unit_name') or '')
        if unit and (item['team'], unit) not in known:
            continue
        if not isinstance(item.get('action'), str) or not item['action'].strip():
            continue
        decisions.append({'id': f'{trace_id}:d{len(decisions)+1}', 'team': item['team'],
                          'unit_name': unit, 'action': item['action'][:500],
                          'rationale': str(item.get('rationale') or '')[:500],
                          'expected_effect': str(item.get('expected_effect') or '')[:500],
                          'basis': 'model_declared', 'evidence_ids': [trace_id]})
    return decisions


def geometry_counterfactual(record, undo=(), changes=()):
    if not record.get('before') or record['turn'] < 1:
        raise ValueError('需要有决策前快照的已提交回合')
    if not undo and not changes:
        raise ValueError('至少选择一项位移撤销或状态干预')
    candidate = copy.deepcopy(record['after'])
    index, previous = unit_index(candidate), unit_index(record['before'])
    applied = []
    for item in undo:
        key = (item.get('team'), item.get('name'))
        if key not in index or key not in previous:
            raise ValueError('干预单位必须同时存在于决策前后快照，且方别匹配')
        lat, lng = extract_lat_lng(previous[key])
        if lat is None or lng is None:
            raise ValueError('决策前坐标不完整')
        index[key].update(lat=lat, lng=lng)
        applied.append({'team': key[0], 'name': key[1], 'lat': lat, 'lng': lng, 'kind': 'undo_move'})
    for item in changes:
        key = (item.get('team'), item.get('name'))
        if key not in index:
            raise ValueError('未找到指定方别的干预单位')
        fields = set(item) - {'team', 'name'}
        if not fields or not fields <= {'lat', 'lng', 'supply', 'morale'}:
            raise ValueError('仅允许干预位置、补给和士气，不直接改写伤亡或兵种')
        for field in fields:
            value = item[field]
            low, high = (-90, 90) if field == 'lat' else (-180, 180) if field == 'lng' else (0, 100)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'非法干预值：{field}')
            index[key][field] = value
        applied.append(copy.deepcopy(item))
    normalize_state_units(candidate)
    factual = geometry(record['after'], record)
    alternative = geometry(candidate, record)
    return {'mode': 'controlled_geometry', 'read_only': True, 'source_trace_id': record['id'],
            'turn': record['turn'], 'interventions': applied,
            'metrics': compare_metrics(factual, alternative),
            'status_changes': verdict_changes(factual, alternative),
            'scope': SCOPE, 'casualty_effect': None,
            'source_clock': record.get('clock'), 'source_guards': record.get('guards'),
            'held_fixed': '其他单位终态、人员装备计数、环境、参数和回合跨度'}


def record_summary(record):
    result = {k: copy.deepcopy(record.get(k)) for k in ('id', 'turn', 'epoch', 'time', 'time_step',
        'report', 'changes', 'decisions', 'runs', 'tool_executions', 'clock', 'geometry_enabled', 'guards')}
    if record.get('before'):
        result['clock'] = clock_check(record['before'], record['after'], record['time_step'])
    return result


def explanation_evidence(analysis):
    """Keep every turn/decision/intervention without repeating branch metadata.

    Each branch repeats scope, guards, clock and held-fixed descriptions. Sending
    those copies to the model made a real 50-turn prompt exceed 375k tokens.
    Full evidence remains in the analysis object and persisted trajectory.
    """
    decisions = ('id', 'team', 'unit_name', 'action', 'rationale', 'expected_effect', 'basis')
    evidence = [{
        'trace_id': t['id'], 'turn': t['turn'], 'clock': t.get('clock'),
        'geometry_enabled': t.get('geometry_enabled'), 'guards': t.get('guards'),
        'changes': t['changes'],
        'decisions': [{k: d[k] for k in decisions if k in d} for d in t['decisions']],
    } for t in analysis['timeline']]
    attributions = [{
        'trace_id': t['trace_id'], 'turn': t['turn'],
        'evaluated_movers': t['evaluated_movers'], 'total_movers': t['total_movers'],
        'effects': [{
            'team': e['team'], 'name': e['name'], 'moved_km': e['moved_km'],
            'status_changes': e['status_changes'],
            'nonzero_metrics': [m for m in e['metrics'] if m['delta'] != 0],
        } for e in t['effects']],
        'interactions': t['interactions'],
    } for t in analysis['attributions']]
    return copy.deepcopy({
        'coverage': {'trace_count': len(evidence), 'all_turns_included': True,
                     'decision_count': sum(len(t['decisions']) for t in evidence)},
        'evidence': evidence, 'attributions': attributions, 'scope': analysis['scope'],
        'held_fixed': '其他单位终态、人员装备计数、环境、参数和回合跨度',
        'metric_note': '仅省略delta=0的汇总指标；净变化为0仍可能存在单位状态互换，请核对status_changes。',
        'limits': analysis['limits'],
    })


def analyze(records):
    turning, attributions = [], []
    for record in records:
        if not record.get('before') or record['turn'] < 1:
            continue
        pre, post = geometry(record['before'], record), geometry(record['after'], record)
        transitions = verdict_changes(pre, post)
        if transitions:
            turning.append({'trace_id': record['id'], 'turn': record['turn'],
                            'status_changes': transitions, 'rank_basis': len(transitions),
                            'label': '状态转折候选，尚非历史因果结论',
                            'geometry_enabled_in_battle': record.get('geometry_enabled', False)})
        movers = [c for c in record.get('changes', []) if c['status'] == 'changed' and (c.get('moved_km') or 0) > .001]
        effects = []
        for change in movers[:12]:
            effect = geometry_counterfactual(record, undo=[{'team': change['team'], 'name': change['name']}])
            effects.append({'team': change['team'], 'name': change['name'], 'moved_km': change['moved_km'],
                            'status_change_count': len(effect['status_changes']), **effect})
        # Pair effects expose interaction: single-factor effects are not additive.
        active = [e for e in effects if e['status_change_count']][:4]
        interactions = []
        for i, a in enumerate(active):
            for b in active[i+1:]:
                joint = geometry_counterfactual(record, undo=[{'team': a['team'], 'name': a['name']}, {'team': b['team'], 'name': b['name']}])
                interactions.append({'units': [a['name'], b['name']], 'joint_status_changes': len(joint['status_changes']),
                                     'single_counts_sum': a['status_change_count']+b['status_change_count'],
                                     'nonadditive': len(joint['status_changes']) != a['status_change_count']+b['status_change_count']})
        attributions.append({'trace_id': record['id'], 'turn': record['turn'], 'effects': effects,
                             'interactions': interactions, 'evaluated_movers': len(effects), 'total_movers': len(movers)})
    return {'version': VERSION, 'scope': SCOPE, 'timeline': [record_summary(r) for r in records],
            'turning_points': sorted(turning, key=lambda r: (-r['rank_basis'], r['turn'])),
            'attributions': attributions, 'explanation': None,
            'limits': ['几何重算不等于胜负因果识别', '单因素归因最多覆盖每回合12个位移；交互检查覆盖前4个有影响位移',
                       '没有外生随机种子控制，不输出胜率、因果百分比或统计置信区间']}
