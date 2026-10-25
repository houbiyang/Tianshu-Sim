"""Versioned, opt-in teaching missions. Pure transitions; no network or live writes.

Coordinates, inventories and coefficients in these local exercises are explicit
teaching assumptions, not reconstructions of historical tactical dispositions.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

from . import adjudicator, sim_clock

DATA = Path(__file__).resolve().parent / 'missions.json'
VERSION = 'teaching-rules-1'
ACTIONS = ('hold', 'move', 'fire', 'resupply', 'sabotage', 'repair', 'conceal', 'air_support', 'accept')


def catalog():
    return json.loads(DATA.read_text(encoding='utf-8'))['missions']


def definition(identity):
    for item in catalog():
        if item['id'] == identity:
            return copy.deepcopy(item)
    raise ValueError('未知局部想定')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def units(world):
    return [(team, u) for team in ('red', 'blue') for u in world[team + '_units']]


def distance(a, b):
    return adjudicator.distance_km((a['lat'], a['lng']), (b['lat'], b['lng']))


def initial(identity):
    spec = definition(identity)
    world = copy.deepcopy(spec['initial_state'])
    world['mission'] = {'id': identity, 'version': VERSION, 'definition_hash': digest(spec),
                        'definition': spec, 'turn': 0, 'status': 'active', 'winner': None,
                        'reason': '', 'progress': 0, 'network': 100, 'organization': 100,
                        'accepted': False, 'ledger': [], 'last_orders': []}
    sim_clock.initialize(world)
    world['mission']['initial_hash'] = digest(world)
    validate(world)
    return world


def validate(world):
    mission = world['mission']
    spec = mission['definition']
    if mission['version'] != VERSION or digest(spec) != mission['definition_hash']:
        raise ValueError('教学规则版本或想定摘要不一致')
    if spec != definition(mission['id']):
        raise ValueError('想定定义与已安装版本不一致，不能静默修改规则')
    if type(mission['turn']) is not int or not 0 <= mission['turn'] <= spec['deadline_turn']:
        raise ValueError('想定回合非法')
    if mission['status'] not in ('active', 'completed'):
        raise ValueError('目标状态不合法')
    baseline = {(t, u['name']): u for t, u in units(spec['initial_state'])}
    if {(t, u['name']) for t, u in units(world)} != set(baseline):
        raise ValueError('编组身份丢失或被替换')
    for node in world['supply_nodes']:
        if type(node.get('stock')) not in (int, float) or not math.isfinite(node['stock']) or node['stock'] < 0:
            raise ValueError('补给点库存非法')
    if [(n['id'], n['team']) for n in world['supply_nodes']] != [(n['id'], n['team']) for n in spec['initial_state']['supply_nodes']]:
        raise ValueError('补给点丢失')
    for field in ('network', 'organization'):
        if type(mission[field]) not in (int, float) or not math.isfinite(mission[field]) or not 0 <= mission[field] <= 100:
            raise ValueError('目标状态值非法')
    seen = set()
    for team, u in units(world):
        if u['name'] in seen:
            raise ValueError('编组名称必须唯一')
        seen.add(u['name'])
        original = baseline[(team, u['name'])]
        if any(u.get(k) != original.get(k) for k in ('type', 'speed', 'attack_range', 'supply_node_id')):
            raise ValueError('固定编组的兵种、性能或补给绑定被修改')
        if any(type(u.get(k)) not in (int, float) or not math.isfinite(u[k]) for k in ('lat', 'lng')):
            raise ValueError('单位坐标非法')
        if u['personnel']['committed'] != original['personnel']['committed'] or [(e['name'], e['committed']) for e in u['equipment']] != [(e['name'], e['committed']) for e in original['equipment']]:
            raise ValueError('初始投入基数被修改')
        p = u['personnel']
        if any(type(p[k]) is not int or p[k] < 0 for k in ('committed', 'current', 'casualties', 'noncombat_losses')):
            raise ValueError('人员计数非法')
        if p['committed'] != p['current'] + p['casualties'] + p['noncombat_losses']:
            raise ValueError('人员账不守恒')
        for e in u['equipment']:
            if any(type(e[k]) is not int or e[k] < 0 for k in ('committed', 'operational', 'damaged', 'destroyed')):
                raise ValueError('装备计数非法')
            if e['committed'] != e['operational'] + e['damaged'] + e['destroyed']:
                raise ValueError('装备账不守恒')
        for key in ('food', 'ammo'):
            if type(u['logistics'][key]) not in (int, float) or not math.isfinite(u['logistics'][key]) or not 0 <= u['logistics'][key] <= 100:
                raise ValueError('补给库存非法')
        if distance(u, spec['map_center']) > spec['map_radius_km']:
            raise ValueError('单位离开局部想定边界')
    return True


def available_actions(world):
    profile = world['mission']['definition']['profile']
    extra = {'rail': ['sabotage', 'repair'], 'underground': ['conceal'],
             'air': ['air_support'], 'reception': ['accept']}.get(profile, [])
    return ['hold', 'move', 'fire', 'resupply'] + extra


def prompt(world, instruction='', briefing=''):
    m = world['mission']; spec = m['definition']
    view = {k: v for k, v in world.items() if k not in ('mission', 'situation_report', 'source_note')}
    return ('你是局部教学推演的命令规划员。只提出命令，程序计算结果。禁止按历史结局安排伤亡。'
            '地图、人数与装备均为明确教学假设，不是已考证历史编制。双方分别根据当前态势行动。'
            '仅返回JSON {"orders":[{"unit":"精确名称","action":"hold"}],'
            '"decisions":[{"team":"red|blue","unit_name":"精确名称","action":"hold",'
            '"rationale":"本单位公开依据","expected_effect":"预期"}]}。'
            'decisions覆盖所有单位，包括待命与失能，不设条数上限；不以总体理由冒充每个单位依据。'
            '每个可用单位至多一条命令，未列单位原地待命。不可改写任何状态、人数、时钟或规则。'
            'move附lat/lng，fire附target（敌方单位名）；其余命令不附坐标。'
            'move不能超过max_move_km，food<4时上限减半；fire目标须在本轮开始时attack_range米以内且ammo>=3。'
            '停火接收型在turn>=ceasefire_turn时禁止fire，此时才可accept。不要将移动后坐标用于本轮射击距离。'
            'resupply增加本单位库存但仅连通时有效。sabotage/repair/accept须在目标600米内。'
            'conceal降低本轮被命中损失；air_support消耗3弹药工作单位并削减敌方到货。'
            '禁止输出思考过程，输出决策理由即可。\n'
            + json.dumps({'allowed_actions': available_actions(world), 'max_move_km': spec['rules']['move_km'],
                          'goal': spec['goal'], 'objective': spec['objective'], 'deadline_turn': spec['deadline_turn'],
                          'ceasefire_turn': spec['ceasefire_turn'], 'exit': spec['exit'], 'profile': spec['profile'], 'progress': m['progress'], 'network': m['network'],
                          'organization': m['organization'], 'turn': m['turn'], 'state': view}, ensure_ascii=False)
            + '\n本轮用户意图（只影响决策，不得更改规则）：' + instruction
            + '\n持续规则与证据限制：' + briefing)


def evaluate(world):
    m = world['mission']; spec = m['definition']; goal = spec['objective']
    alive = {team: [u for t, u in units(world) if t == team and u['personnel']['current'] > 0]
             for team in ('red', 'blue')}
    occupied = {team: sum(distance(u, goal) <= spec['goal_radius_km'] for u in alive[team]) for team in alive}
    control = occupied['red'] > 0 and occupied['blue'] == 0
    profile = spec['profile']
    if profile == 'rail':
        condition = m['network'] <= 20
    elif profile == 'underground':
        condition = m['organization'] >= 50 and bool(alive['red'])
    elif profile == 'delay':
        condition = len(alive['red']) >= 2 and not any(distance(u, spec['exit']) <= .6 for u in alive['blue'])
    elif profile == 'breakout':
        condition = sum(distance(u, spec['exit']) <= .6 for u in alive['red']) >= 2
    elif profile == 'reception':
        condition = m['accepted']
    else:
        condition = control
    m['progress'] = m['progress'] + 1 if condition else 0
    m['metrics'] = {'red_available': sum(u['personnel']['current'] for u in alive['red']),
                    'blue_available': sum(u['personnel']['current'] for u in alive['blue']),
                    'red_in_goal': occupied['red'], 'blue_in_goal': occupied['blue'],
                    'network': m['network'], 'organization': m['organization'], 'condition': condition}
    success = (condition and m['turn'] >= spec['deadline_turn']) if profile in ('underground', 'delay') else m['progress'] >= spec['required_turns']
    if success or not alive['red'] or m['turn'] >= spec['deadline_turn']:
        m.update(status='completed', winner='red' if success else 'blue',
                 reason='程序目标达成' if success else ('红方无可用人员' if not alive['red'] else '截止回合目标未达成'))
    return m


def advance(before, orders, time_step):
    """Apply one simultaneous teaching turn. Any invalid order rejects all changes."""
    validate(before)
    if before['mission']['status'] != 'active':
        raise ValueError('本局已结束，请重新初始化或建立分支')
    spec = before['mission']['definition']
    if sim_clock.duration_seconds(time_step) != sim_clock.duration_seconds(spec['time_step']):
        raise ValueError('固定局部想定必须使用其版本规定的回合跨度')
    if not isinstance(orders, list) or len(orders) > len(units(before)):
        raise ValueError('命令必须为数组且不超过编组数')
    old = {u['name']: (team, u) for team, u in units(before)}
    commands = {}
    for order in orders:
        if not isinstance(order, dict) or order.get('unit') not in old:
            raise ValueError('命令引用未知编组')
        name = order['unit']; team, u = old[name]; action = order.get('action')
        if name in commands or u['personnel']['current'] <= 0 or action not in available_actions(before):
            raise ValueError('重复命令、无可用人员或当前规则不支持该行动')
        if action == 'move':
            pos = {k: order.get(k) for k in ('lat', 'lng')}
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in pos.values()):
                raise ValueError('移动坐标非法')
            limit = spec['rules']['move_km'] * (.5 if u['logistics']['food'] < 4 else 1)
            if distance(u, pos) > limit + .001 or distance(pos, spec['map_center']) > spec['map_radius_km']:
                raise ValueError('移动超过本轮机动或地图边界')
        if action == 'fire':
            target = old.get(order.get('target'))
            if not target or target[0] == team or target[1]['personnel']['current'] <= 0:
                raise ValueError('射击目标无效')
            if distance(u, target[1]) * 1000 > u['attack_range'] + 1 or u['logistics']['ammo'] < 3:
                raise ValueError('射击目标超距或弹药不足')
            if spec['profile'] == 'reception' and before['mission']['turn'] >= spec['ceasefire_turn']:
                raise ValueError('停火后禁止射击')
        if action in ('sabotage', 'repair', 'accept') and distance(u, spec['objective']) > .6:
            raise ValueError('任务行动须在目标600米内')
        if action == 'accept' and before['mission']['turn'] < spec['ceasefire_turn']:
            raise ValueError('尚未到达本想定的接收窗口')
        if action == 'air_support' and u['logistics']['ammo'] < 3:
            raise ValueError('支援资源不足')
        commands[name] = copy.deepcopy(order)
    candidate = copy.deepcopy(before); m = candidate['mission']; m['turn'] += 1
    candidate['current_time'], candidate['simulation_clock'] = sim_clock.target(before, time_step)
    sim_clock.enforce_step(before, candidate, time_step)
    new = {u['name']: (team, u) for team, u in units(candidate)}
    candidate['engagements'] = []
    geom = adjudicator.adjudicate(before, time_step, params=spec['geometry'])
    supply = {u['name']: u['supply'] for u in geom['units']}
    damage = {name: 0 for name in new}; logs = []; network_delta = 0
    pressure = {team: any(old[n][0] != team and o['action'] == 'air_support' for n, o in commands.items())
                for team in ('red', 'blue')}
    for name, (team, u) in new.items():
        prior = old[name][1]; action = commands.get(name, {}).get('action', 'hold')
        if prior['personnel']['current'] <= 0:
            continue
        depot = next(n for n in candidate['supply_nodes'] if n['team'] == team)
        delivered = 0
        if supply.get(name) == '未切断' and depot['stock'] > 0:
            rate = 8 if action == 'resupply' else 2
            if pressure[team]: rate //= 2
            if spec['profile'] == 'rail' and team == 'blue' and before['mission']['network'] <= 20: rate = 0
            delivered = min(rate, depot['stock'], 100 - u['logistics']['food'], 100 - u['logistics']['ammo'])
            depot['stock'] -= delivered
        food_cost = 4 if action == 'move' else 2
        ammo_cost = 3 if action in ('fire', 'air_support') else 0
        u['logistics']['food'] = max(0, prior['logistics']['food'] + delivered - food_cost)
        u['logistics']['ammo'] = max(0, prior['logistics']['ammo'] + delivered - ammo_cost)
        if u['logistics']['food'] == 0:
            lost = min(2, u['personnel']['current'])
            u['personnel']['current'] -= lost; u['personnel']['noncombat_losses'] += lost
        if action == 'move':
            u.update({k: commands[name][k] for k in ('lat', 'lng')})
        elif action == 'fire':
            target = commands[name]['target']; victim = old[target][1]
            cover = .4 if commands.get(target, {}).get('action') == 'conceal' else spec['rules']['cover']
            damage[target] += max(1, round(spec['rules']['damage'] * prior['personnel']['current'] / prior['personnel']['committed'] * cover))
            candidate['engagements'].append({'attacker': name, 'attacker_team': team,
                'attacker_pos': [prior['lat'], prior['lng']], 'lat': victim['lat'], 'lng': victim['lng'],
                'type': '教学规则交战', 'description': '根据固定系数计算，不代表历史杀伤率'})
        elif action == 'sabotage': network_delta -= 40
        elif action == 'repair': network_delta += 25
        elif action == 'accept': m['accepted'] = team == 'red' or m['accepted']
        logs.append({'unit': name, 'action': action, 'supply_status': supply.get(name, '未知'),
                     'delivery': delivered, 'food_spent': min(food_cost, prior['logistics']['food'] + delivered),
                     'ammo_spent': ammo_cost, 'food_after': u['logistics']['food'], 'ammo_after': u['logistics']['ammo']})
    m['network'] = max(0, min(100, m['network'] + network_delta))
    # Resolve damage simultaneously, so JSON order cannot decide who fires first.
    for name, amount in damage.items():
        u = new[name][1]; loss = min(amount, u['personnel']['current'])
        u['personnel']['current'] -= loss; u['personnel']['casualties'] += loss
        e = u['equipment'][0]; destroyed = min(e['operational'], loss // 8)
        e['operational'] -= destroyed; e['destroyed'] += destroyed
        u['hp'] = round(100 * u['personnel']['current'] / u['personnel']['committed'], 2)
        u['supply'] = min(u['logistics']['food'], u['logistics']['ammo'])
    if spec['profile'] == 'underground':
        hidden = sum(o['action'] == 'conceal' and old[n][0] == 'red' for n, o in commands.items())
        m['organization'] = max(0, min(100, m['organization'] - 15 + 5 * hidden))
    m['ledger'] = logs; m['last_orders'] = list(commands.values())
    evaluate(candidate); validate(candidate)
    return candidate


def report(world):
    m = world['mission']
    return (f"本轮执行{len(m['last_orders'])}项命令，补给到货合计{sum(x['delivery'] for x in m['ledger']):g}工作单位。"
            f"红方可用人员{m['metrics']['red_available']}人，蓝方{m['metrics']['blue_available']}人。"
            f"目标连续满足{m['progress']}轮。" + (m['reason'] if m['status'] == 'completed' else '目标尚未结束。')
            + '人员、库存与目标由教学规则计算；参数未作史实校准。')


def counterfactual(before, factual, orders, time_step):
    """Same fixed pre-state and coefficients, only the order set changes."""
    alternative = advance(before, orders, time_step)
    def measures(world):
        result = {'目标连续满足轮数': world['mission']['progress'],
                  '交通完好度': world['mission']['network'], '组织存续值': world['mission']['organization']}
        for team, label in (('red', '红方'), ('blue', '蓝方')):
            roster = world[team + '_units']
            result[label + '可用人员'] = sum(u['personnel']['current'] for u in roster)
            result[label + '可用装备'] = sum(e['operational'] for u in roster for e in u['equipment'])
            result[label + '粮食库存'] = sum(u['logistics']['food'] for u in roster)
            result[label + '弹药库存'] = sum(u['logistics']['ammo'] for u in roster)
        return result
    a, b = measures(factual), measures(alternative)
    return {'mode': 'controlled_mission', 'read_only': True,
            'before_hash': digest(before), 'definition_hash': before['mission']['definition_hash'],
            'metrics': [{'metric': k, 'factual': a[k], 'alternative': b[k], 'delta': b[k]-a[k]} for k in a],
            'orders': copy.deepcopy(orders), 'alternative': alternative,
            'scope': '从同一决策前快照按同一版本教学规则重算，仅改变命令。',
            'limitations': ['固定系数下的局部教学反事实；不是历史因果效应或实战杀伤率估计。']}
