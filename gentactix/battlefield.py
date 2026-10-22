"""战场展示数据：只读派生图层、人员装备统计，以及跨回合记录保留。

纯标准库；不把射程当杀伤半径，不将HP当人数，不推算地形遮挡或实际道路。
"""

import copy
import math

from .unit_types import classify_unit
from . import supply, fire_control

VIEW_VERSION = "battlefield-2"
STATIC_FIELDS = ("terrain_features", "supply_nodes", "supply_routes")
PERSONNEL_FIELDS = ("committed", "current", "casualties", "noncombat_losses")
EQUIPMENT_FIELDS = ("committed", "operational", "damaged", "destroyed")
CAPABILITY_NOTE = """【工程能力边界】涉及本系统如何计算时，以此为准：界面“战斗减员率”=casualties/committed，
“非战斗减员率”=noncombat_losses/committed；装备“永久损失率”=destroyed/committed，“待修率”=damaged/committed，
“可用率”=operational/committed。战斗与非战斗减员、装备待修与永久损失分别统计，不能相加后冒充其中任一比率。
普通模型生成模式没有确定性伤亡或运输吞吐算法。仅mission固定局部教学模式按版本系数计算战损、库存、行动预算和目标；这些是合成教学假设，不是历史标定。所有模式均无工事毁伤、弹道命中或DEM视线遮挡算法。
几何内核只近似计算机动、补给连通和包围；射程圈不是杀伤半径。战场影响可作推演分析，但须与已经执行的软件计算区分。
未知计数不补成0或确数，模型给出的估计不是经核实的史实。"""


def _objects(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def _count(value):
    n = _number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def _coord(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lat, lng = map(_number, value)
    if lat is None or lng is None or abs(lat) > 90 or abs(lng) > 180:
        return None
    return [lat, lng]


def _path(value, minimum=2):
    if not isinstance(value, list) or len(value) < minimum:
        return None
    points = [_coord(p) for p in value]
    return points if all(p is not None for p in points) else None


def _units(state):
    return [(team, u) for team in ("red", "blue")
            for u in _objects(state.get(team + "_units"))]


def _merge_record(previous, current):
    """只补齐已知的嵌套计数字段，不覆写当前值（包括显式的0）。"""
    if isinstance(previous.get("personnel"), dict) and isinstance(current.get("personnel"), dict):
        _check_committed(previous["personnel"], current["personnel"], str(current.get("name", "单位")) + "人员")
        _carry_counts(previous["personnel"], current["personnel"], PERSONNEL_FIELDS)
    old_equipment = {(str(e.get("name", "")), str(e.get("unit", ""))): e
                     for e in _objects(previous.get("equipment"))}
    present = set()
    for e in _objects(current.get("equipment")):
        if not e.get("unit"):
            matches = [old for (name, _), old in old_equipment.items() if name == str(e.get("name", ""))]
            if len(matches) == 1 and matches[0].get("unit"):
                e["unit"] = matches[0]["unit"]
        identity = (str(e.get("name", "")), str(e.get("unit", "")))
        present.add(identity)
        old = old_equipment.get(identity, {})
        _check_committed(old, e, str(e.get("name", "装备")))
        _carry_counts(old, e, EQUIPMENT_FIELDS)
    if isinstance(current.get("equipment"), list):
        for identity, e in old_equipment.items():
            if identity not in present:
                carried = copy.deepcopy(e)
                carried['carried_forward'] = True
                current["equipment"].append(carried)


def _carry_counts(previous, current, fields):
    missing = [key for key in fields if current.get(key) is None and previous.get(key) is not None]
    for key, value in previous.items():
        if key != 'carried_forward' and (key not in current or current[key] is None):
            current[key] = copy.deepcopy(value)
    current['carried_forward'] = bool(missing or current.get('carried_forward'))
    for key in ('casualties', 'noncombat_losses', 'destroyed'):
        before, after = _count(previous.get(key)), _count(current.get(key))
        if before is not None and after is not None and after < before:
            raise ValueError(f'{key}累计损失从{before}降为{after}；本回合拒收，原记录保留')


def _check_committed(previous, current, label):
    before, after = _count(previous.get("committed")), _count(current.get("committed"))
    if before is not None and after is not None and after < before:
        raise ValueError(f"{label}累计投入从{before}降为{after}，不符合累计计数；本回合拒收，原战局保留")


def carry_battlefield_state(previous, current):
    """在候选战局上保留地形、节点与退场单位的最后记录；不修改上一回合。"""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return current
    for key in STATIC_FIELDS:
        if key not in current and key in previous:
            current[key] = copy.deepcopy(previous[key])
    old_units = {(team, str(u.get("name", ""))): u for team, u in _units(previous)}
    old_records = {(str(r.get("team", "")), str(r.get("name", ""))): r
                   for r in _objects(previous.get("loss_records"))}
    active = set()
    for team, u in _units(current):
        key = (team, str(u.get("name", "")))
        active.add(key)
        old = old_units.get(key, old_records.get(key, {}))
        for field in ("personnel", "equipment", "fire_sector", "min_attack_range", "supply_node_id"):
            if u.get(field) is None and field in old:
                u[field] = copy.deepcopy(old[field])
                if field == 'personnel' and isinstance(u[field], dict):
                    u[field]['carried_forward'] = True
                elif field == 'equipment':
                    for entry in _objects(u[field]):
                        entry['carried_forward'] = True
        _merge_record(old, u)
    records = {(str(r.get("team", "")), str(r.get("name", ""))): copy.deepcopy(r)
               for r in _objects(previous.get("loss_records"))}
    for key, u in old_units.items():
        if key not in active and (u.get("personnel") or u.get("equipment")):
            records[key] = {"team": key[0], "name": key[1],
                            "personnel": copy.deepcopy(u.get("personnel")),
                            "equipment": copy.deepcopy(u.get("equipment", [])),
                            "recorded_at": previous.get("current_time", ""),
                            "status": "已移出编组；保留最后记录，未推定全员伤亡"}
    for r in _objects(current.get("loss_records")):
        key = (str(r.get("team", "")), str(r.get("name", "")))
        if key[0] not in ('red', 'blue') or not key[1] or key in active:
            continue
        if key in records:
            merged = records[key]
            for field in ('personnel', 'equipment'):
                if r.get(field) is None and field in merged:
                    r[field] = copy.deepcopy(merged[field])
            _merge_record(merged, r)
            merged.update(copy.deepcopy(r))
        else:
            records[key] = copy.deepcopy(r)
    archived = [r for key, r in records.items() if key not in active]
    if archived or "loss_records" in current:
        current["loss_records"] = archived
    return current


def _metric(raw, equipment=False):
    if not isinstance(raw, dict):
        return None
    fields = EQUIPMENT_FIELDS if equipment else PERSONNEL_FIELDS
    result = {k: _count(raw.get(k)) for k in fields}
    result["source"] = str(raw.get("source") or "战局记录（未经独立核验）")
    counts = list(result[k] for k in fields)
    complete = all(v is not None for v in counts)
    valid = complete and sum(counts[1:]) <= counts[0]
    result["valid"] = valid
    result["note"] = "" if valid else "计数缺失或超过累计投入，暂不计算比例"
    result['carried_forward'] = bool(raw.get('carried_forward'))
    if result['carried_forward']:
        result['note'] = (result['note'] + '；' if result['note'] else '') + '含沿用的既有计数，本回合未完整更新'
    denominator = result["committed"]
    result["loss_rate"] = (result["destroyed" if equipment else "casualties"] / denominator
                           if valid and denominator else None)
    result["secondary_rate"] = (result["damaged" if equipment else "noncombat_losses"] / denominator
                                if valid and denominator else None)
    result["readiness_rate"] = (result["operational" if equipment else "current"] / denominator
                                if valid and denominator else None)
    if equipment:
        result["name"] = str(raw.get("name") or "未命名装备")
        result["unit"] = str(raw.get("unit") or "件")
    return result


def _aggregate(records, equipment=False):
    fields = EQUIPMENT_FIELDS if equipment else PERSONNEL_FIELDS
    valid = [r for r in records if r and r["valid"]]
    if not valid:
        return None
    combined = {k: sum(r[k] for r in valid) for k in fields}
    combined["source"] = "已记录单位汇总"
    combined['carried_forward'] = any(r.get('carried_forward') for r in valid)
    if equipment:
        combined.update(name=valid[0]["name"], unit=valid[0]["unit"])
    return _metric(combined, equipment)


def _losses(state, warnings):
    rows = []
    seen = set()
    for team, u in _units(state):
        name = str(u.get("name") or "未命名单位")
        identity = (team, name)
        if identity in seen:
            warnings.append(f"{team}/{name}单位重复，统计只计首条记录")
            continue
        seen.add(identity)
        rows.append({"team": team, "name": name,
                     "status": "当前编组", "archived": False, "personnel": _metric(u.get("personnel")),
                     "equipment": [_metric(e, True) for e in _objects(u.get("equipment"))]})
    for r in _objects(state.get("loss_records")):
        if r.get("team") not in ("red", "blue"):
            continue
        name = str(r.get("name") or "未命名单位")
        identity = (r["team"], name)
        if identity in seen:
            warnings.append(f"{r['team']}/{name}退场记录重复或已在当前编组，未重复计数")
            continue
        seen.add(identity)
        status = str(r.get("status") or "最后记录")
        rows.append({"team": r["team"], "name": name, "archived": True,
                     "status": status if status.startswith("已移出编组") else "已移出编组；" + status,
                     "personnel": _metric(r.get("personnel")),
                     "equipment": [_metric(e, True) for e in _objects(r.get("equipment"))]})
    teams = []
    for team in ("red", "blue"):
        members = [r for r in rows if r["team"] == team]
        gear = {}
        for r in members:
            for e in r["equipment"]:
                gear.setdefault((e["name"], e["unit"]), []).append(e)
        teams.append({"team": team, "total_units": len(members),
                      "recorded_units": sum(bool(r["personnel"] and r["personnel"]["valid"]) for r in members),
                      "archived_units": sum(r["archived"] for r in members),
                      "active_current": (sum(r["personnel"]["current"] for r in members if not r["archived"])
                                         if all(r["personnel"] and r["personnel"]["valid"]
                                                for r in members if not r["archived"]) else None),
                      "personnel": _aggregate([r["personnel"] for r in members]),
                      "equipment": [g for entries in gear.values() if (g := _aggregate(entries, True))]})
    return {"units": rows, "teams": teams}


def _operational_state(state, previous=None, trajectory=None):
    """Derive abstract HP changes from committed snapshots, never headcounts.

    First observation is a per-unit baseline, including reinforcements. Missing
    units/HP stay unknown; absence cannot establish destruction or casualties.
    """
    baselines = {}
    for record in trajectory or []:
        world = record.get('after') if isinstance(record, dict) else None
        if not isinstance(world, dict):
            continue
        for team, unit in _units(world):
            key = (team, str(unit.get('name', '')))
            hp = _number(unit.get('hp'))
            if key[1] and hp is not None and hp >= 0:
                baselines.setdefault(key, (hp, world.get('current_time', '首次记录')))
    old = {(team, str(u.get('name', ''))): u for team, u in _units(previous or {})}
    for key, u in old.items():
        hp = _number(u.get('hp'))
        if key[1] and hp is not None and hp >= 0:
            baselines.setdefault(key, (hp, (previous or {}).get('current_time', '上一回合')))
    current = {(team, str(u.get('name', ''))): u for team, u in _units(state) if u.get('name')}
    rows = []
    for team, name in sorted(current.keys() | baselines.keys() | old.keys()):
        key = (team, name)
        u = current.get(key, {})
        hp = _number(u.get('hp'))
        hp = hp if hp is not None and hp >= 0 else None
        prev = _number(old.get(key, {}).get('hp'))
        prev = prev if prev is not None and prev >= 0 else None
        baseline, baseline_time = baselines.get(key, (None, None))
        decline = max(0, baseline - hp) if baseline is not None and hp is not None else None
        def score(field):
            n = _number(u.get(field))
            return n if n is not None and 0 <= n <= 100 else None
        rows.append({'team': team, 'name': name, 'active': key in current,
                     'status': '已移出编组，结局未推定' if key not in current else '已失能（HP为0）' if hp == 0 else '当前编组',
                     'baseline_hp': baseline, 'baseline_time': baseline_time, 'current_hp': hp,
                     'previous_hp': prev, 'hp_change': hp - prev if hp is not None and prev is not None else None,
                     'hp_decline': decline, 'hp_decline_rate': decline / baseline if decline is not None and baseline else None,
                     'morale': score('morale'), 'supply': score('supply')})
    teams = []
    for team in ('red', 'blue'):
        active = [r for r in rows if r['team'] == team and r['active']]
        comparable = [r for r in active if r['hp_decline'] is not None]
        baseline = sum(r['baseline_hp'] for r in comparable)
        decline = sum(r['hp_decline'] for r in comparable)
        changed = [r for r in active if r['hp_change'] is not None]
        def mean(field):
            values = [r[field] for r in active if r[field] is not None]
            return sum(values) / len(values) if values else None
        teams.append({'team': team, 'active_units': len(active),
                      'disabled_units': sum(r['current_hp'] == 0 for r in active),
                      'hp_recorded_units': sum(r['current_hp'] is not None for r in active),
                      'comparable_units': len(comparable), 'changed_units': len(changed),
                      'current_hp': sum(r['current_hp'] for r in active) if active and all(r['current_hp'] is not None for r in active) else None,
                      'hp_decline': decline if comparable else None,
                      'hp_decline_rate': decline / baseline if baseline else None,
                      'hp_change': sum(r['hp_change'] for r in changed) if changed else None,
                      'mean_morale': mean('morale'), 'mean_supply': mean('supply')})
    return {'units': rows, 'teams': teams,
            'note': 'HP是抽象状态值，不是人数或装备数。下降按各单位首次已保存HP对比；补充可能抵消下降，不能解释为累计伤亡。已退场或缺值单位不推定全损。'}


def _data_quality(state, features, sectors, nodes, routes):
    units = [u for _, u in _units(state)]
    total = len(units)
    def row(key, label, count, denominator, note):
        return {'key': key, 'label': label, 'recorded': count, 'total': denominator,
                'status': 'missing' if not count else 'partial' if denominator is not None and count < denominator else 'available',
                'note': note}
    return [
        row('hp', 'HP状态', sum(_number(u.get('hp')) is not None and _number(u.get('hp')) >= 0 for u in units), total,
            '已有HP可与保存的快照对比，不转换为人数。'),
        row('personnel', '人员数量', sum(bool((m := _metric(u.get('personnel'))) and m['valid']) for u in units), total,
            '需要累计投入、现有、战斗与非战斗减员四项；未知人数不显示为零伤亡。'),
        row('equipment', '装备数量', sum(any(_metric(e, True)['valid'] for e in _objects(u.get('equipment'))) for u in units), total,
            '统计至少一个类别计数完整的单位；空数组不能证明没有装备，各类别须单独建账。'),
        row('terrain', '地形要素坐标', len(features), None,
            '地形文字描述不等于山脊、河道、道路的测绘几何；参考锚点也不等于完整地形。'),
        row('sectors', '射界方向', len(sectors), None,
            '最大射程可画范围圈；缺少方向和水平角度时不能生成射界，不由敌方位置猜朝向。'),
        row('supply_nodes', '补给节点', len(nodes), None,
            '单位补给指数不等于仓库位置；需要带来源的节点坐标。'),
        row('supply_routes', '补给路线', len(routes), None,
            '需提供路径节点；裁决生成的几何连线会单独标为估计，不代表真实道路。'),
    ]


def _destination(pos, bearing, distance_m):
    lat, lng = map(math.radians, pos)
    angle, d = math.radians(bearing), distance_m / 6371008.8
    y = math.asin(math.sin(lat) * math.cos(d) + math.cos(lat) * math.sin(d) * math.cos(angle))
    x = lng + math.atan2(math.sin(angle) * math.sin(d) * math.cos(lat),
                        math.cos(d) - math.sin(lat) * math.sin(y))
    return [math.degrees(y), (math.degrees(x) + 540) % 360 - 180]


def _sector(pos, bearing, arc, outer, inner):
    bearings = [bearing - arc / 2 + arc * i / 32 for i in range(33)]
    outside = [_destination(pos, b, outer) for b in bearings]
    return outside + ([_destination(pos, b, inner) for b in reversed(bearings)] if inner else [pos])


def build_battlefield_view(state, adjudication=None, *, previous=None, trajectory=None):
    """统一提供给Vue与Folium。坐标为WGS84，距离为米，比例为0—1。"""
    state = state if isinstance(state, dict) else {}
    adj = adjudication if isinstance(adjudication, dict) else {}
    warnings, ranges, sectors, features, routes, nodes = [], [], [], [], [], []
    for f in _objects(state.get("terrain_features")):
        geometry = str(f.get("geometry", "line"))
        points = _path(f.get("coordinates"), {"point": 1, "line": 2, "polygon": 3}.get(geometry, 2))
        if geometry not in ("point", "line", "polygon") or not points:
            warnings.append("地形要素坐标无效，未绘制：" + str(f.get("name", "未命名")))
            continue
        features.append({"name": str(f.get("name") or "地形要素"), "kind": str(f.get("kind") or "other"),
                         "geometry": geometry, "coordinates": points,
                         "elevation_m": _number(f.get("elevation_m")),
                         "source": str(f.get("source") or "战局记录")})
    for team, u in _units(state):
        pos = _coord([u.get("lat"), u.get("lng")])
        hp = _number(u.get("hp", u.get("兵力")))
        if hp is not None and hp <= 0:
            continue
        if not pos:
            continue
        profile = classify_unit(u.get("type"))
        explicit = _number(u.get("attack_range"))
        outer = explicit if explicit is not None else (profile or {}).get("range_hint_m")
        if outer is None or not 0 < outer <= 500000:
            continue
        inner = _number(u.get("min_attack_range", 0))
        if inner is None or not 0 <= inner < outer:
            warnings.append(str(u.get("name", "单位")) + "最小射程无效，未绘制火力范围")
            continue
        item = {"name": str(u.get("name") or "未命名单位"), "team": team, "position": pos,
                "max_range_m": outer, "min_range_m": inner,
                "estimated": explicit is None,
                "source": str(u.get("range_source") or ("战局记录" if explicit is not None else "兵种工作估计"))}
        ranges.append(item)
        sector = u.get("fire_sector")
        if isinstance(sector, dict):
            bearing, arc = _number(sector.get("bearing_deg")), _number(sector.get("arc_deg"))
            if bearing is not None and arc is not None and 0 < arc < 360:
                sectors.append({**item, "bearing_deg": bearing % 360, "arc_deg": arc,
                                "coordinates": _sector(pos, bearing, arc, outer, inner)})
            else:
                warnings.append(item["name"] + "射界方向或角度无效，未绘制扇区")
    node_index = {}
    for n in _objects(state.get("supply_nodes")):
        pos = _coord([n.get("lat"), n.get("lng")])
        if pos and n.get("team") in ("red", "blue") and n.get("id"):
            item = {"id": str(n["id"]), "name": str(n.get("name") or n["id"]),
                    "team": n["team"], "position": pos, "source": str(n.get("source") or "战局记录")}
            nodes.append(item)
            node_index[(item["team"], item["id"])] = item
    # The map renders the exact paths evaluated by the kernel. Older or stale
    # adjudications must not contribute a status to new geometry.
    evaluated = {(r.get('team'), r.get('name')): r for r in _objects(adj.get('units'))}
    matched = adj.get('supply_input_digest') == supply.input_digest(state)
    for team, u in _units(state):
        if (_number(u.get('hp')) or 0) <= 0 and _number(u.get('hp')) is not None:
            continue
        row = evaluated.get((team, u.get('name')), {}) if matched else {}
        if 'supply_paths' in row:
            paths = row['supply_paths']
            warnings.extend(row.get('supply_warnings', []))
        else:
            paths, notices = supply.resolve_paths(state, team, u)
            warnings.extend(notices)
        routes.extend(paths)
    warnings.extend(issue['message'] for issue in fire_control.check(state))
    return {"version": VIEW_VERSION,
            "terrain": {"label": str(state.get("current_terrain") or "未记录"),
                        "factor": adj.get("terrain_factor"), "source": adj.get("terrain_source"),
                        "features": features},
            "fire_ranges": ranges, "fields_of_fire": sectors, "supply_nodes": nodes,
            "supply_lines": routes, "losses": _losses(state, warnings), "warnings": warnings,
            "operational": _operational_state(state, previous, trajectory),
            "data_quality": _data_quality(state, features, sectors, nodes, routes),
            "notes": ["射程表示几何覆盖，不代表命中、杀伤范围或地面控制；射界未计算高程遮挡。",
                      "人员战斗/非战斗减员分账；装备摧毁/待修分账；比例分母为各自累计投入。"]}


DISPLAY_INSTRUCTION = """
        【战场展示数据·必须随状态输出】每个单位必须包含personnel与equipment；不得只在战报中描述损耗却遗漏结构化记录。
        人员数量未知时也输出personnel对象，未知数字用null并在source说明缺失原因；装备种类/数量未知用[]。不得拿hp换算人数，不编造测绘结论。
        - 单位personnel: {"committed":累计投入人数,"current":现有可用人数,"casualties":累计战斗减员人数,"noncombat_losses":累计非战斗减员人数,"source":"史料或推演估计"}。
          各减员类别互不重计，数字为非负整数；committed仅随补充增加，移出编组不表示全部阵亡。
          用户想定明确给出的单位人数/装备数量必须建账，注明“用户想定”；不可把整场战役总数机械摊给某单位。战役前已发生的损失不得初始化为0。
          已建账单位每轮同时更新current、casualties、noncombat_losses及装备operational、damaged、destroyed；无变化也保留。累计减员/永久损失不归零。
          本回合模型估计的损失必须注明“推演估计，非史实”；只有HP变化而无人数依据时人员保持未知，不用0冒充无伤亡。战报与结构化计数须一致。
        - 单位equipment: [{"name":"具体装备类别","unit":"辆/门/架等","committed":累计投入,"operational":可用,"damaged":当前待修,"destroyed":累计永久损失,"source":"依据"}]；不同类别不可混算。
        - attack_range、min_attack_range单位为米。fire_sector:{"bearing_deg":从正北顺时针0至360,"arc_deg":已知水平射界角}；未知方向不填，禁止由敌军位置猜方向。
          engagements须给attacker及attacker_pos，射击目标须在声明射程和射界内。转向须显式更新fire_sector并给fire_sector_change:{"reason":"转向依据"}；fixed:true的固定射界不可旋转。
        - 地形terrain_features:[{"name":"名称","kind":"ridge/river/road/woods/urban/fortification","geometry":"point/line/polygon","coordinates":[[纬度,经度],...],"source":"坐标依据"}]；无核定坐标就留空，不凭地形标签虚构山脊或河道。
        - supply_nodes:[{"id":"稳定编号","name":"补给点","team":"red/blue","lat":纬度,"lng":经度,"source":"依据"}]；单位可填supply_node_id。
        - supply_routes:[{"name":"路线名","team":"red/blue","unit_name":"受供单位名","waypoints":[[纬度,经度],...],"status":"open/threatened/cut/unknown","source":"路线依据或推演估计"}]；无已知道路节点就留空。
          路线按补给源→受供单位排列，起点须对应本阵营supply_node_id；节点是固定仓库时不跟随运输单位移动。内核按此折线计算通断，模型status仅保留为声明，不替代几何裁决。无道路则用节点直连估计，未切断不等于粮弹实际运达。
        - 后续回合保留这些字段并更新变化，committed不重置。确实退场的单位，其最终personnel/equipment可放在loss_records:[{"team":"red/blue","name":"原名称","personnel":{...},"equipment":[...]}]；不得把未知结局补成100%战损。
"""
