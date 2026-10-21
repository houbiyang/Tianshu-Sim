"""战场展示数据：只读派生图层、人员装备统计，以及跨回合记录保留。

纯标准库；不把射程当杀伤半径，不将HP当人数，不推算地形遮挡或实际道路。
"""

import copy
import math

from .unit_types import classify_unit

VIEW_VERSION = "battlefield-1"
STATIC_FIELDS = ("terrain_features", "supply_nodes", "supply_routes")
PERSONNEL_FIELDS = ("committed", "current", "casualties", "noncombat_losses")
EQUIPMENT_FIELDS = ("committed", "operational", "damaged", "destroyed")
CAPABILITY_NOTE = """【工程能力边界】涉及本系统如何计算时，以此为准：界面“战斗减员率”=casualties/committed，
“非战斗减员率”=noncombat_losses/committed；装备“永久损失率”=destroyed/committed，“待修率”=damaged/committed，
“可用率”=operational/committed。战斗与非战斗减员、装备待修与永久损失分别统计，不能相加后冒充其中任一比率。
没有确定性伤亡、工事毁伤、弹道命中、DEM视线遮挡或运输吞吐算法。不能声称系统已计算这些修正。
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
        for key, value in previous["personnel"].items():
            if key not in current["personnel"]:
                current["personnel"][key] = copy.deepcopy(value)
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
        for key, value in old.items():
            if key not in e:
                e[key] = copy.deepcopy(value)
    if isinstance(current.get("equipment"), list):
        for identity, e in old_equipment.items():
            if identity not in present:
                current["equipment"].append(copy.deepcopy(e))


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
            if field not in u and field in old:
                u[field] = copy.deepcopy(old[field])
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
        if key in records:
            merged = records[key]
            _merge_record(merged, r)
            merged.update(copy.deepcopy(r))
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


def build_battlefield_view(state, adjudication=None):
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
    covered = set()
    for r in _objects(state.get("supply_routes")):
        points = _path(r.get("waypoints"))
        if r.get("team") not in ("red", "blue") or not points:
            warnings.append("补给路线坐标或阵营无效，未绘制：" + str(r.get("name", "未命名")))
            continue
        covered.add((r["team"], str(r.get("unit_name", ""))))
        routes.append({"name": str(r.get("name") or "补给路线"), "team": r["team"], "coordinates": points,
                       "estimated": False, "status": str(r.get("status") or "unknown"),
                       "source": str(r.get("source") or "战局记录"), "cut_by": None})
    for team, u in _units(state):
        key = (team, str(u.get("name", "")))
        node = node_index.get((team, str(u.get("supply_node_id", ""))))
        pos = _coord([u.get("lat"), u.get("lng")])
        if key not in covered and node and pos:
            covered.add(key)
            routes.append({"name": key[1], "team": team, "coordinates": [node["position"], pos],
                           "estimated": True, "status": "unknown", "cut_by": None,
                           "source": "补给节点至单位直连估计；未沿道路规划"})
    for u in _objects(adj.get("units")):
        key = (u.get("team"), str(u.get("name", "")))
        points = _path(u.get("supply_path"))
        if key not in covered and points:
            routes.append({"name": key[1], "team": key[0], "coordinates": points, "estimated": True,
                           "status": "cut" if u.get("supply") == "已切断" else "unknown",
                           "cut_by": u.get("cut_by"), "source": "裁决后方直线估计；非真实道路/补给站"})
    return {"version": VIEW_VERSION,
            "terrain": {"label": str(state.get("current_terrain") or "未记录"),
                        "factor": adj.get("terrain_factor"), "source": adj.get("terrain_source"),
                        "features": features},
            "fire_ranges": ranges, "fields_of_fire": sectors, "supply_nodes": nodes,
            "supply_lines": routes, "losses": _losses(state, warnings), "warnings": warnings,
            "notes": ["射程表示几何覆盖，不代表命中、杀伤范围或地面控制；射界未计算高程遮挡。",
                      "人员战斗/非战斗减员分账；装备摧毁/待修分账；比例分母为各自累计投入。"]}


DISPLAY_INSTRUCTION = """
        【战场展示数据】以下字段应在有依据时填写；无资料用null或[]，不得拿hp换算人数，不编造测绘结论。
        - 单位personnel: {"committed":累计投入人数,"current":现有可用人数,"casualties":累计战斗减员人数,"noncombat_losses":累计非战斗减员人数,"source":"史料或推演估计"}。
          各减员类别互不重计，数字为非负整数；committed仅随补充增加，移出编组不表示全部阵亡。
        - 单位equipment: [{"name":"具体装备类别","unit":"辆/门/架等","committed":累计投入,"operational":可用,"damaged":当前待修,"destroyed":累计永久损失,"source":"依据"}]；不同类别不可混算。
        - attack_range、min_attack_range单位为米。fire_sector:{"bearing_deg":从正北顺时针0至360,"arc_deg":已知水平射界角}；未知方向不填，禁止由敌军位置猜方向。
        - 地形terrain_features:[{"name":"名称","kind":"ridge/river/road/woods/urban/fortification","geometry":"point/line/polygon","coordinates":[[纬度,经度],...],"source":"坐标依据"}]；无核定坐标就留空，不凭地形标签虚构山脊或河道。
        - supply_nodes:[{"id":"稳定编号","name":"补给点","team":"red/blue","lat":纬度,"lng":经度,"source":"依据"}]；单位可填supply_node_id。
        - supply_routes:[{"name":"路线名","team":"red/blue","unit_name":"受供单位名","waypoints":[[纬度,经度],...],"status":"open/threatened/cut/unknown","source":"路线依据或推演估计"}]；无已知道路节点就留空。
        - 后续回合保留这些字段并更新变化，committed不重置。确实退场的单位，其最终personnel/equipment可放在loss_records:[{"team":"red/blue","name":"原名称","personnel":{...},"equipment":[...]}]；不得把未知结局补成100%战损。
"""
