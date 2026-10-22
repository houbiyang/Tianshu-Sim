"""Shared, read-only supply path resolution. No road or inventory inference."""
import math
import hashlib
import json


def input_digest(state):
    payload = {k: state.get(k) for k in ('red_units', 'blue_units', 'supply_nodes', 'supply_routes')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def coord(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        return None
    return list(value) if abs(value[0]) <= 90 and abs(value[1]) <= 180 else None


def objects(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def resolve_paths(state, team, unit, inferred_rear=None):
    """Explicit polylines > declared node direct link > visibly inferred rear.

    Paths run source -> recipient. A moved recipient gets an explicitly estimated
    last-mile segment; existing waypoints are never silently moved. Bad explicit
    references cannot silently select an enemy node or a different rear depot.
    """
    from .adjudicator import distance_km
    name = str(unit.get('name', ''))
    pos = coord([unit.get('lat'), unit.get('lng')])
    warnings, paths = [], []
    if pos is None:
        return paths, [name + '：受供单位坐标无效']
    nodes = objects(state.get('supply_nodes'))
    node_id = unit.get('supply_node_id')
    matches = [n for n in nodes if n.get('id') == node_id and n.get('team') == team] if node_id else []
    node = matches[0] if len(matches) == 1 else None
    node_pos = coord([node.get('lat'), node.get('lng')]) if node else None
    if node_id and not node_pos:
        return [], [name + '：补给节点缺失、重名、跨阵营或坐标无效，连通状态未知']
    declared = [r for r in objects(state.get('supply_routes'))
                if r.get('team') == team and r.get('unit_name') == name]
    for index, route in enumerate(declared):
        raw = route.get('waypoints')
        points = [coord(p) for p in raw] if isinstance(raw, list) else []
        if len(points) < 2 or any(p is None for p in points):
            warnings.append(name + '：忽略无效补给折线')
            continue
        route_node = route.get('source_node_id')
        if route_node and route_node != node_id:
            warnings.append(name + '：路线供给节点与单位声明不一致，忽略该路线')
            continue
        if node_pos:
            # Accept a reversed polyline only when its endpoint identifies the depot.
            if distance_km(points[-1], node_pos) < .001 and distance_km(points[0], node_pos) >= .001:
                points.reverse()
            if distance_km(points[0], node_pos) > .05:
                warnings.append(name + '：路线起点偏离声明仓库，回退节点直连估计')
                continue
            if points[0] != node_pos:
                points.insert(0, node_pos)
        estimated = bool(route.get('estimated')) or '估计' in str(route.get('source', ''))
        source = str(route.get('source') or '战局声明路径；未核实道路')
        if distance_km(points[-1], pos) > .001:
            points.append(pos)
            estimated = True
            source += '；末段至当前位置为直连估计，非道路规划'
        else:
            points[-1] = pos
        paths.append({'name': str(route.get('name') or name), 'team': team,
                      'unit_name': name, 'route_index': index, 'coordinates': points,
                      'estimated': estimated, 'source': source, 'basis': 'declared_route',
                      'node_id': node_id, 'declared_status': str(route.get('status') or 'unknown'),
                      'status': 'unknown', 'cut_by': None})
    if paths:
        return paths, warnings
    if node_pos:
        start, basis, source = node_pos, 'node_direct', '补给节点至单位直连估计；未沿道路规划'
    elif declared:
        return [], warnings + [name + '：无可用声明路线，连通状态未知']
    elif inferred_rear is not None:
        start, basis, source = list(inferred_rear), 'inferred_rear', '裁决后方直线估计；非真实道路/补给站'
    else:
        return [], warnings
    return [{'name': name, 'team': team, 'unit_name': name, 'coordinates': [start, pos],
             'estimated': True, 'source': source, 'basis': basis, 'node_id': node_id,
             'declared_status': 'unknown', 'status': 'unknown', 'cut_by': None}], warnings
