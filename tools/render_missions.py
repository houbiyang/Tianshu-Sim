"""Build explicitly synthetic, replayable local exercises from the source catalog."""
import argparse
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ['delay', 'delay', 'assault', 'breakout', 'assault', 'delay', 'assault',
            'delay', 'breakout', 'assault', 'assault', 'breakout', 'delay', 'rail',
            'underground', 'assault', 'assault', 'air', 'air', 'reception']
GOALS = {'assault': '红方控制目标区，蓝方无可用编组在区内，连续保持两轮',
         'air': '在空中支援影响到货条件下控制目标区两轮',
         'delay': '坚持至截止回合，红方至少两个编组仍有人员，蓝方未进入后方出口600米内',
         'breakout': '红方至少两个编组到达出口，连续保持两轮',
         'rail': '交通节点完好度降至20或以下并连续维持两轮',
         'underground': '截止时组织存续值不少于50且仍有红方可用人员',
         'reception': '停火窗口开放后红方在目标600米内完成接收，两轮确认'}


def render():
    scenarios = json.loads((ROOT / 'data/anti_japanese_war/scenarios.json').read_text())['scenarios']
    anchors = json.loads((ROOT / 'gentactix/anti_japanese_war_anchors.json').read_text())
    result = []
    for s, profile in zip(scenarios, PROFILES):
        lat, lng = anchors['points'][s['points'][0]]['coordinates']
        def pos(x, y):
            return {'lat': round(lat + y / 111.32, 6), 'lng': round(lng + x / (111.32 * math.cos(math.radians(lat))), 6)}
        source = '教学合成初态：人数、库存、局部坐标、钟点与系数均为假设，非历史实际部署；未完成历史地图配准'
        date = next((e['date'] for e in s['events'] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', e['date'])),
                    re.search(r'\d{4}', s['name'])[0] + '-01-01')
        spec = {'id': s['id'] + '-local-v1', 'battle': s['name'], 'title': s['name'] + '／局部教学演习',
                'profile': profile, 'source_ids': s['sources'], 'assumptions': source,
                'time_step': '30分钟', 'deadline_turn': 4, 'required_turns': 2, 'ceasefire_turn': 2,
                'map_center': pos(0, 0), 'map_radius_km': 4, 'objective': pos(0, 0),
                'exit': pos(-1.4, 0), 'goal_radius_km': .45, 'goal': GOALS[profile],
                'rules': {'move_km': .8 if profile in ('breakout', 'delay') else .6,
                          'damage': 20 if profile == 'assault' else 12,
                          'cover': .6 if profile in ('assault', 'underground') else .8},
                'geometry': {**s['geometry'], 'contact_km': .1, 'control_radius_km': .25, 'encircle_radius_km': 2, 'retreat_check_km': 2}}
        world = {'center': [lat, lng], 'current_time': date + ' 08:00', 'era': '抗日战争局部教学假设',
                 'current_terrain': '城市地带' if profile == 'assault' else '丘陵森林',
                 'red_units': [], 'blue_units': [], 'engagements': [], 'terrain_features': [],
                 'supply_nodes': [], 'supply_routes': [], 'source_note': source,
                 'situation_report': '固定局部教学编组已经部署，目标和截止时间由程序记录。'}
        for team, x in [('red', -.8), ('blue', .3)]:
            world['supply_nodes'].append({'id': team + '-depot', 'name': team + '教学补给点',
                'team': team, **pos(-2 if team == 'red' else 2, 0), 'stock': 120,
                'source': source + '；每包同时含1粮食工作单位和1弹药工作单位'})
            for i, y in enumerate((-.35, 0, .35)):
                world[team + '_units'].append({'name': ('红' if team == 'red' else '蓝') + f'方教学编组{i+1}',
                    'type': '步兵' if i != 2 else '工兵', **pos(x, y), 'hp': 100,
                    'combat_power': 50, 'speed': 3, 'attack_range': 900, 'morale': 70, 'supply': 12,
                    'supply_node_id': team + '-depot', 'logistics': {'food': 12, 'ammo': 12},
                    'personnel': {'committed': 100, 'current': 100, 'casualties': 0, 'noncombat_losses': 0, 'source': source},
                    'equipment': [{'name': '教学支援装备', 'unit': '件', 'committed': 4, 'operational': 4,
                                   'damaged': 0, 'destroyed': 0, 'source': source}]})
        spec['initial_state'] = world
        result.append(spec)
    return json.dumps({'version': 1, 'notice': '合成局部教学演习；不是历史战场精确复原', 'missions': result}, ensure_ascii=False, indent=2) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'gentactix/missions.json'
    content = render()
    if args.check:
        assert path.read_text(encoding='utf-8') == content, '使命数据需重新生成'
    else:
        path.write_text(content, encoding='utf-8')
    print('20 local teaching missions checked' if args.check else '20 local teaching missions generated')
