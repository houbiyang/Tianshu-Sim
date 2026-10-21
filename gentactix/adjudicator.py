"""⚖️ 确定性裁决内核（几何层）。

为什么要有这一层
----------------
原版的所有战场结论都由语言模型即兴给出：同一个想定跑两次，伤亡、走位、
胜负都不同。教学场景里这是致命的 —— 老师问「如果赵括不出壁垒会怎样」，
学生跑两次得到的差异无法归因，因为分不清「结果不同是因为决策不同」还是
「因为模型这次心情不同」。

本模块只裁决三类**可复算**的事：

1. **机动**：距离 ÷ 速度 × 地形系数 → 本回合能走多远、能不能接触上；
2. **连通**：粮道是否穿越敌控区 → 补给线通不通；
3. **拓扑**：单位被敌方控制圈封住了几个方向 → 是否被围、退路是否被断。

**不裁决伤亡数字。** 那是叙事层的事，交给模型；而且以现有数据也做不准，
硬做只会把「编」升级成「算得很精确的编」。

设计约束
--------
- 纯标准库、零第三方依赖、不 import streamlit / folium，可在离线环境单测；
- 全部为纯函数：同输入 → 同输出，无随机、无时间依赖、无网络；
- 输出只有**离散状态**（已切断 / 被围 / 崩溃），不输出连续数值当结论 ——
  布尔判断容易做对、容易写测试钉住、也容易被模型转写成自然语言。

距离算法
--------
内部统一用等距圆柱近似（equirectangular）：50 km 内误差约 0.1%，
1000 km 内约 0.5%。战场尺度上完全够用，且比 haversine 快三倍 ——
包围判定要为每个单位做 24 方向 × 24 步采样，积分下来是十万次量级。
"""

from __future__ import annotations

import hashlib
import json
import math

from . import adjudication_lib
from .unit_types import classify_unit

# 版本标记：界面上显示，用来区分「内核改了」和「这局确实没变化」
ADJUDICATOR_VERSION = "geo-3"

# 平面近似的换算系数（km / 度）
_KM_PER_DEG_LAT = 110.57
_KM_PER_DEG_LNG = 111.32


# ==========================================
# ⚙️ 默认参数
# ==========================================
# 参数定义（默认值 / 允许范围 / 含义）在 adjudication_lib.PARAM_SCHEMA ——
# 那里是单一真相源。本模块只负责拿参数算几何，不负责决定「3 km 算不算接触」。
# 每场战役的覆盖值放在 adjudication/<战役名>.md，由 load_scenario_params 读入。
DEFAULT_PARAMS = adjudication_lib.default_params()


# ==========================================
# ⏱️ 时间跨度
# ==========================================
# ⚠️ 顺序有意义：多字单位必须排在前面（"个月" 先于 "月"），否则 "1个月"
#    会被 "月" 先命中 —— 结果一样，但 "星期" 和 "周" 的顺序错了就会算错。
_TIME_UNITS = (
    ("个月", 720.0),
    ("月", 720.0),
    ("星期", 168.0),
    ("周", 168.0),
    ("天", 24.0),
    ("日", 24.0),
    ("小时", 1.0),
    ("钟头", 1.0),
    ("分钟", 1.0 / 60.0),
    ("分", 1.0 / 60.0),
)


def parse_time_step(label):
    """把「30分钟 / 1小时 / 1天 / 1周」解析成小时数。失败回退 0.5 小时。"""
    text = str(label or "").strip()
    if not text:
        return 0.5
    digits = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            digits += ch
        elif digits:
            break
    try:
        value = float(digits) if digits else 1.0
    except ValueError:
        value = 1.0
    for unit, hours in _TIME_UNITS:
        if unit in text:
            return value * hours
    return 0.5


# ==========================================
# 🥾 兵种机动速度（km/h，战术推进口径）
# ==========================================
# 口径说明：这里是**战场内战术推进速度**，含列阵、保持队形、等待命令，
# 不是「日行军里程」。古代步兵日行 30 里（15 km / 8 h ≈ 1.9 km/h）是
# 长距离行军口径；战场内短距推进可以到 3 km/h，两者不要混。
# 查询时取「最长匹配」：'骑兵' 同时命中 '骑'，取更具体的 7.5。
MARCH_SPEED_KMH = {
    "骑射": 8.0,
    "弓骑": 8.0,
    "轻骑": 9.0,
    "游骑": 9.0,
    "铁骑": 6.5,
    "骑兵": 7.5,
    "骑": 7.0,
    "战车": 2.5,
    "车兵": 2.5,
    "车阵": 2.5,
    "舟师": 6.0,
    "水军": 6.0,
    "舰": 6.0,
    "船": 6.0,
    "弓弩": 3.0,
    "弩": 3.0,
    "弓": 3.0,
    "步兵": 3.0,
    "步卒": 3.0,
    "步军": 3.0,
    "长矛": 3.0,
    "戟": 3.0,
    "刀盾": 2.8,
    "投石": 1.5,
    "攻城": 1.5,
    "炮兵": 2.0,
}
DEFAULT_SPEED_KMH = 3.0

# 连续行军超过这个时长开始掉速（需要宿营、喂马、炊饭）
LONG_MARCH_HOURS = 8.0
# 0.45 ≈ 24 小时里真正在走的约 11 小时。不做这个折算，1 天回合会算出
# 「步兵日行 45 km」——那是急行军的极限，不是可持续行军。
LONG_MARCH_PENALTY = 0.45


# ==========================================
# 🏔️ 地形系数
# ==========================================
# 与地形标签子串匹配，取最长匹配（"丘陵森林" 优先于 "丘陵"）。
TERRAIN_FACTORS = {
    "深海": 1.0,
    "近海": 1.0,
    "海峡": 1.0,
    "平原": 1.0,
    "草原": 1.0,
    "戈壁": 1.2,
    "沙漠": 1.3,
    "丘陵": 1.4,
    "丘陵森林": 1.6,
    "森林": 1.7,
    "丛林": 1.9,
    "山地": 2.0,
    "高山峻岭": 2.2,
    "城市地带": 1.5,
    "城镇": 1.4,
    "沼泽": 2.0,
    "水网": 2.0,
    "河流": 1.6,
}
DEFAULT_TERRAIN_FACTOR = 1.2


# ==========================================
# 🎯 兵种默认射程（米）
# ==========================================
# ⚠️ 必须与 viz.UNIT_TYPE_RANGE_HINTS 保持同一口径：地图上的交战半径由
#    viz.resolve_engagement_radius 推导，裁决器的控制圈若用另一套数，
#    就会出现「地图画了扇区、裁决说够不着」的自相矛盾。
#    两者一致性由 verify_adjudicator.py 强制校验。
UNIT_TYPE_RANGE_M = (
    ("骑射", 800.0), ("弓骑", 800.0), ("mounted archer", 800.0),
    ("投石", 400.0), ("弩", 300.0), ("弓", 300.0), ("archer", 300.0),
    ("步", 500.0), ("infantry", 500.0), ("战车", 500.0), ("chariot", 500.0),
    ("骑兵", 500.0), ("cavalry", 500.0),
)
DEFAULT_MELEE_RANGE_M = 500.0
ENGAGEMENT_RADIUS_FACTOR = 1.2


# ==========================================
# 📐 基础几何
# ==========================================
def distance_km(a, b):
    """两点平面近似距离（km）。a/b 为 (lat, lng)。"""
    lat1, lng1 = a
    lat2, lng2 = b
    mid = math.radians((lat1 + lat2) / 2.0)
    dy = (lat2 - lat1) * _KM_PER_DEG_LAT
    dx = (lng2 - lng1) * _KM_PER_DEG_LNG * math.cos(mid)
    return math.hypot(dx, dy)


def bearing_deg(a, b):
    """a 指向 b 的方位角（度，0=正北，90=正东）。"""
    lat1, lng1 = a
    lat2, lng2 = b
    mid = math.radians((lat1 + lat2) / 2.0)
    dy = (lat2 - lat1) * _KM_PER_DEG_LAT
    dx = (lng2 - lng1) * _KM_PER_DEG_LNG * math.cos(mid)
    return math.degrees(math.atan2(dx, dy)) % 360.0


def offset_point(pos, bearing, km):
    """从 pos 沿方位角走 km 公里后的坐标。"""
    rad = math.radians(bearing)
    dlat = km * math.cos(rad) / _KM_PER_DEG_LAT
    cos_lat = max(0.1, math.cos(math.radians(pos[0])))
    dlng = km * math.sin(rad) / (_KM_PER_DEG_LNG * cos_lat)
    return (pos[0] + dlat, pos[1] + dlng)


# ==========================================
# 🔤 单位字段读取（与 state.UNIT_KEY_ALIASES 同口径）
# ==========================================
# 这里不 import state，因为 state 顶层 import streamlit —— 裁决器要能在
# 没有 Streamlit 的裸 Python 环境里跑测试。
_UNIT_ALIASES = {
    "name": ("name", "名称", "单位名称", "部队名称", "单位", "部队", "unit_name"),
    "type": ("type", "类型", "兵种", "兵种类型", "单位类型", "类别", "unit_type"),
    "lat": ("lat", "latitude", "纬度", "current_lat"),
    "lng": ("lng", "longitude", "经度", "current_lng"),
    "hp": ("hp", "HP", "兵力", "生命值", "血量", "耐久", "存活兵力"),
    "speed": ("speed", "移动速度", "速度", "行进速度", "mobility"),
    "attack_range": ("attack_range", "射程", "攻击范围", "打击范围", "range"),
    "morale": ("morale", "士气", "士气值"),
    "supply": ("supply", "补给", "补给值", "后勤", "后勤补给"),
}


def _field(unit, key, default=None):
    for alias in _UNIT_ALIASES.get(key, (key,)):
        if isinstance(unit, dict) and unit.get(alias) not in (None, ""):
            return unit[alias]
    return default


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _longest_match(text, table, default):
    """在 table 里找最长命中的键，返回对应值。无命中返回 default。"""
    text = str(text or "")
    if not text:
        return default
    best_value, best_len = default, -1
    for key, value in table.items():
        if key in text and len(key) > best_len:
            best_value, best_len = value, len(key)
    return best_value


def terrain_factor(label):
    """地形对机动的拖慢系数（≥1.0，越大越慢）。"""
    return _longest_match(label, TERRAIN_FACTORS, DEFAULT_TERRAIN_FACTOR)


def unit_march_speed_kmh(unit_type, declared_speed=None, hours=0.5):
    """生效的机动速度（km/h）。

    AI 给出的 `speed` 口径不明（可能是 km/h、也可能是抽象「格数」），
    直接采信会得到「步兵 30 km/h」这种硬伤。这里的做法是：**以兵种表为基准**，
    speed 只当相对强弱的提示（0.75—1.25 倍），并把明显离谱的值直接忽略。
    """
    profile = classify_unit(unit_type)
    base = (profile["march_speed_kmh"] if profile else
            _longest_match(unit_type, MARCH_SPEED_KMH, DEFAULT_SPEED_KMH))

    factor = 1.0
    declared = _to_float(declared_speed)
    if declared is not None and 0 < declared <= 20:
        factor = max(0.75, min(1.25, declared / 5.0))

    if hours > LONG_MARCH_HOURS:
        factor *= LONG_MARCH_PENALTY
    return base * factor


def unit_attack_range_m(unit):
    """攻击射程（米）：优先显式 attack_range，其次兵种表，最后按步战推定。"""
    explicit = _to_float(_field(unit, "attack_range"))
    if explicit is not None and explicit > 0:
        return explicit
    unit_type = str(_field(unit, "type", "") or "")
    profile = classify_unit(unit_type)
    if profile and profile["range_hint_m"] is not None:
        return float(profile["range_hint_m"])
    lowered = unit_type.lower()
    for keyword, meters in UNIT_TYPE_RANGE_M:
        if keyword in lowered or keyword in unit_type:
            return meters
    return DEFAULT_MELEE_RANGE_M


def unit_control_radius_km(unit, minimum=3.0):
    """该单位能形成的区域控制半径（km）= max(下限, 射程 × 1.2)。"""
    profile = classify_unit(_field(unit, "type", ""))
    if profile and not profile["ground_control"]:
        return 0.0
    reach_km = unit_attack_range_m(unit) * ENGAGEMENT_RADIUS_FACTOR / 1000.0
    return max(float(minimum), reach_km)


# ==========================================
# 🧩 战局解析
# ==========================================
UNIT_CONTAINERS = (("red", "red_units"), ("blue", "blue_units"))


def _unit_view(unit, team):
    """把参差不齐的 AI 单位字典压成裁决器要的窄结构。坐标不合法返回 None。"""
    if not isinstance(unit, dict):
        return None
    lat = _to_float(_field(unit, "lat"))
    lng = _to_float(_field(unit, "lng"))
    if lat is None or lng is None:
        return None
    return {
        "name": str(_field(unit, "name", "未命名单位") or "未命名单位").strip() or "未命名单位",
        "team": team,
        "type": str(_field(unit, "type", "") or ""),
        "pos": (lat, lng),
        "hp": _to_float(_field(unit, "hp")) or 1.0,
        "raw": unit,
    }


def _collect_units(war_state):
    units, skipped = [], []
    for team, key in UNIT_CONTAINERS:
        for unit in (war_state.get(key) or []):
            profile = classify_unit(_field(unit, "type", ""))
            if profile and not profile["ground_geometry"]:
                skipped.append(f"{_field(unit, 'name', '未命名单位')}（航空/舰艇支援不参加地面几何）")
                continue
            view = _unit_view(unit, team)
            if view is None:
                if isinstance(unit, dict):
                    skipped.append(str(_field(unit, "name", "未命名单位")))
                continue
            units.append(view)
    return units, skipped


def _centroid(units):
    """单位集合的几何重心。无单位返回 None。"""
    if not units:
        return None
    lat = sum(u["pos"][0] for u in units) / len(units)
    lng = sum(u["pos"][1] for u in units) / len(units)
    return (lat, lng)


def _rear_point(own_units, enemy_centroid, unit):
    """补给源位置：己方最靠后的单位（离敌方重心最远者）。

    为什么这样定义：不引入任何假设（不需要知道「后方基地在哪」），
    纯粹从现有态势推出来，同输入必然同输出。若被评估单位自己就是最靠后的
    那一个，则沿「背离敌方重心」方向外推 8 km 作为虚拟辎重位置 ——
    否则单个单位永远「补给未切断」，判定形同虚设。
    """
    if enemy_centroid is None:
        return None
    candidates = [u for u in own_units if u is not unit]
    if candidates:
        rear = max(candidates, key=lambda u: distance_km(u["pos"], enemy_centroid))
        if distance_km(rear["pos"], unit["pos"]) >= 0.5:
            return rear["pos"]
    away = (bearing_deg(enemy_centroid, unit["pos"])) % 360.0
    return offset_point(unit["pos"], away, 8.0)


def _blocked_along(start, end, threats, step_km=0.5):
    """从 start 到 end 的直线是否穿过敌方控制圈。

    返回 (是否被阻断, 阻断者名称, 距 start 多远被阻断)。
    直线采样而非图搜索：补给线是道路，道路在沙盘尺度上就是直线段，
    再做 A* 只会把「公路绕行」这种不存在的信息编出来。
    """
    total = distance_km(start, end)
    if total < 1e-6:
        return False, None, None
    bearing = bearing_deg(start, end)
    steps = max(1, int(math.ceil(total / step_km)))
    for i in range(steps + 1):
        point = offset_point(start, bearing, total * i / steps)
        for name, tpos, radius in threats:
            if distance_km(point, tpos) <= radius:
                return True, name, total * i / steps
    return False, None, None


def _sealed_ratio(pos, threats, max_km, directions=24, step_km=0.5):
    """敌方控制了单位周围几个方向（0—1）。1.0 = 全向封闭。"""
    if not threats:
        return 0.0
    sealed = 0
    for i in range(directions):
        bearing = 360.0 * i / directions
        hit = False
        dist = step_km
        while dist <= max_km:
            point = offset_point(pos, bearing, dist)
            if any(distance_km(point, tpos) <= radius for _, tpos, radius in threats):
                hit = True
                break
            dist += step_km
        if hit:
            sealed += 1
    return sealed / float(directions)


def _retreat_open(pos, threats, away_bearing, max_km, spread=75.0, rays=7, step_km=0.5):
    """朝「背离敌军」的扇区内，是否存在一条能跑出包围圈的通道。

    只看背离方向 ±75°：朝敌军的「退路」不叫退路。
    """
    if not threats:
        return True, away_bearing
    for i in range(rays):
        offset = -spread + (2 * spread) * i / (rays - 1)
        bearing = (away_bearing + offset) % 360.0
        clear = True
        dist = step_km
        while dist <= max_km:
            point = offset_point(pos, bearing, dist)
            if any(distance_km(point, tpos) <= radius for _, tpos, radius in threats):
                clear = False
                break
            dist += step_km
        if clear:
            return True, bearing
    return False, None


# ==========================================
# 📊 弹药与兵力对比（只做定性，不报具体伤亡）
# ==========================================
def _force_ratio(units, own_team):
    own = sum(u["hp"] for u in units if u["team"] == own_team)
    other = sum(u["hp"] for u in units if u["team"] != own_team)
    if other <= 0:
        return None
    return own / other


def _morale_state(supply, encirclement, ratio):
    """把补给/包围/兵力比压成三档士气。故意粗 —— 粗才不容易错。"""
    score = 0
    if supply == "已切断":
        score += 2
    elif supply == "无后方":
        score += 1
    if encirclement == "被围":
        score += 2
    elif encirclement == "半包围":
        score += 1
    if ratio is not None:
        if ratio < 0.5:
            score += 1
        if ratio < 0.25:
            score += 1
    if score >= 4:
        return "崩溃"
    if score >= 2:
        return "动摇"
    return "稳定"


# ==========================================
# 🧮 主入口
# ==========================================
def adjudicate(war_state, time_step="30分钟", turn=0, params=None):
    """对当前战局做一次确定性裁决。

    纯函数：同样的 (war_state, time_step, turn, params) 必然得到同样的结果，
    digest 与 fingerprint 逐字节相同 —— 这是「可复现」的可验证形式。
    """
    war_state = war_state if isinstance(war_state, dict) else {}
    settings = dict(DEFAULT_PARAMS)
    settings.update(params or {})

    hours = parse_time_step(time_step)
    terrain = str(war_state.get("current_terrain") or "")
    # 战役参数库写下 terrain_factor 的瞬间即覆盖模型标签：模型常把河谷丘陵
    # 标成「平原」（×1.0），而内核不能靠模型的诚实度来保证机动尺度。
    # 注意只认 params（= spec.explicit）里真实写下的键，DEFAULT_PARAMS 里的
    # terrain_factor 默认值不算「标定」，否则「未标定」与「标定成 1.0」无法区分。
    calibrated_factor = (params or {}).get("terrain_factor")
    if calibrated_factor is not None:
        factor = float(calibrated_factor)
        terrain_source = "战役参数库标定"
    else:
        factor = terrain_factor(terrain)
        terrain_source = "模型地形标签"
    contact_km = float(settings["contact_km"])
    control_km = float(settings["control_radius_km"])
    encircle_km = float(settings["encircle_radius_km"])
    retreat_km = float(settings["retreat_check_km"])

    units, skipped = _collect_units(war_state)
    by_team = {
        "red": [u for u in units if u["team"] == "red"],
        "blue": [u for u in units if u["team"] == "blue"],
    }
    centroids = {team: _centroid(members) for team, members in by_team.items()}

    rows, facts = [], []
    for unit in units:
        enemy_team = "blue" if unit["team"] == "red" else "red"
        enemies = by_team[enemy_team]
        own = by_team[unit["team"]]
        enemy_centroid = centroids[enemy_team]

        speed = unit_march_speed_kmh(
            unit["type"], _field(unit["raw"], "speed"), hours)
        reach_km = speed * hours / factor

        # --- 最近敌军 ---
        nearest_name, nearest_km = None, None
        for enemy in enemies:
            dist = distance_km(unit["pos"], enemy["pos"])
            if nearest_km is None or dist < nearest_km:
                nearest_name, nearest_km = enemy["name"], dist
        contact_reach_km = reach_km + contact_km
        can_contact = nearest_km is not None and nearest_km <= contact_reach_km
        hours_to_contact = None
        if nearest_km is not None:
            remaining = max(0.0, nearest_km - contact_km)
            if remaining == 0:
                hours_to_contact = 0.0
            elif speed > 0:
                hours_to_contact = remaining * factor / speed

        # --- 威胁圈（敌方每个单位形成一个控制区）---
        threats = [
            (e["name"], e["pos"], radius)
            for e in enemies
            if (radius := unit_control_radius_km(e["raw"], control_km)) > 0
        ]

        # --- 补给线 ---
        rear = _rear_point(own, enemy_centroid, unit)
        if rear is None:
            supply, cut_by, cut_km = "无后方", None, None
        else:
            blocked, cut_by, cut_km = _blocked_along(unit["pos"], rear, threats)
            supply = "已切断" if blocked else "未切断"

        # --- 包围 / 退路 ---
        sealed = _sealed_ratio(unit["pos"], threats, encircle_km)
        if sealed >= float(settings["sealed_ratio_encircled"]):
            encirclement = "被围"
        elif sealed >= float(settings["sealed_ratio_partial"]):
            encirclement = "半包围"
        else:
            encirclement = "未被围"

        away = bearing_deg(enemy_centroid, unit["pos"]) if enemy_centroid else 0.0
        retreat_ok, retreat_bearing = _retreat_open(
            unit["pos"], threats, away, retreat_km)

        ratio = _force_ratio(units, unit["team"])
        morale = _morale_state(supply, encirclement, ratio)

        row = {
            "name": unit["name"],
            "team": unit["team"],
            "type": unit["type"],
            "lat": round(unit["pos"][0], 5),
            "lng": round(unit["pos"][1], 5),
            "speed_kmh": round(speed, 2),
            "reach_km": round(reach_km, 2),
            "contact_reach_km": round(contact_reach_km, 2),
            "nearest_enemy": nearest_name,
            "nearest_enemy_km": None if nearest_km is None else round(nearest_km, 2),
            "can_contact": bool(can_contact),
            "hours_to_contact": None if hours_to_contact is None else round(hours_to_contact, 2),
            "supply": supply,
            "cut_by": cut_by,
            "cut_at_km": None if cut_km is None else round(cut_km, 2),
            "supply_path": [list(rear), list(unit["pos"])] if rear else [],
            "sealed_ratio": round(sealed, 3),
            "encirclement": encirclement,
            "retreat": "可退" if retreat_ok else "已断",
            "morale_state": morale,
        }
        rows.append(row)

        # --- 事实句：只写有信息量的，不把 24 个单位全铺一遍 ---
        if nearest_name is not None:
            if can_contact:
                facts.append(
                    f"{unit['name']}（{_team_cn(unit['team'])}）距 {nearest_name} "
                    f"{nearest_km:.1f} km，可机动 {reach_km:.1f} km＋接触带 {contact_km:g} km ⇒ **能够接触**"
                )
            else:
                facts.append(
                    f"{unit['name']}（{_team_cn(unit['team'])}）距 {nearest_name} "
                    f"{nearest_km:.1f} km，可机动 {reach_km:.1f} km＋接触带 {contact_km:g} km ⇒ **无法接触**"
                )
        if supply == "已切断":
            facts.append(
                f"{unit['name']} 的粮道在出发后 {cut_km:.1f} km 处被 {cut_by} 的控制区截断 ⇒ **补给线已切断**"
            )
        if encirclement != "未被围":
            facts.append(
                f"{unit['name']} 周围 {sealed * 100:.0f}% 方向被敌方控制 ⇒ **{encirclement}**"
            )
        if not retreat_ok:
            facts.append(f"{unit['name']} 的退路方向已全部被封锁 ⇒ **退路已断**")
        if morale == "崩溃":
            facts.append(f"{unit['name']} 补给/包围/兵力比三项叠加 ⇒ **士气崩溃**")

    summary = _summarize(rows)
    result = {
        "version": ADJUDICATOR_VERSION,
        "turn": int(turn or 0),
        "time_step": str(time_step),
        "hours": round(hours, 3),
        "terrain": terrain,
        "terrain_factor": factor,
        "terrain_source": terrain_source,
        "contact_km": contact_km,
        "control_radius_km": control_km,
        "units": rows,
        "skipped_units": skipped,
        "facts": facts,
        "summary": summary,
        "deterministic": True,
    }
    result["digest"] = _compose_digest(result, max_chars=int(settings.get("digest_max_chars", 2000)))
    result["fingerprint"] = _fingerprint(result)
    return result


def _team_cn(team):
    return "红方" if team == "red" else "蓝方"


def _summarize(rows):
    return {
        "units": len(rows),
        "can_contact": sum(1 for r in rows if r["can_contact"]),
        "supply_cut": sum(1 for r in rows if r["supply"] == "已切断"),
        "encircled": sum(1 for r in rows if r["encirclement"] == "被围"),
        "partial": sum(1 for r in rows if r["encirclement"] == "半包围"),
        "retreat_cut": sum(1 for r in rows if r["retreat"] == "已断"),
        "morale_broken": sum(1 for r in rows if r["morale_state"] == "崩溃"),
        "morale_shaken": sum(1 for r in rows if r["morale_state"] == "动摇"),
    }


def _compose_digest(result, max_chars=2000):
    """把裁决结果压成注入 prompt 的紧凑文本。

    结构固定三段：机动边界 / 状态判定 / 硬约束。超长时按行截断并注明，
    绝不破坏段落结构 —— 模型看到半截的「硬约束」列表会直接忽略整段。
    """
    hours = result["hours"]
    head = (
        f"\n【⚖️ 确定性裁决 · 第 {result['turn']} 回合 · 本回合 {result['time_step']}"
        f"（{hours:g} 小时）· 地形「{result['terrain'] or '未标注'}」×{result['terrain_factor']:g}"
        f"（{result['terrain_source']}）】\n"
        "以下结论来自纯几何计算（距离 ÷ 速度 × 地形系数），**不可改写、不可忽略**。\n"
        "你的任务是把它们讲成战场故事，不是重新推演一遍。\n"
    )

    move_lines = [f"\n■ 机动上限（接触条件为敌距≤可机动距离＋{result['contact_km']:g} km接触带；不代表直接射击或命中）"]
    for row in result["units"]:
        if row["nearest_enemy"] is None:
            move_lines.append(
                f"· {row['name']}（{_team_cn(row['team'])}）：可机动 {row['reach_km']:.1f} km（无敌军参照）"
            )
        elif row["can_contact"]:
            move_lines.append(
                f"· {row['name']}（{_team_cn(row['team'])}）：可机动 {row['reach_km']:.1f} km；"
                f"距 {row['nearest_enemy']} {row['nearest_enemy_km']:.1f} km ⇒ 本回合**可接触**"
            )
        else:
            move_lines.append(
                f"· {row['name']}（{_team_cn(row['team'])}）：可机动 {row['reach_km']:.1f} km；"
                f"距 {row['nearest_enemy']} {row['nearest_enemy_km']:.1f} km ⇒ 本回合**够不着**"
            )

    abnormal = [r for r in result["units"] if (
        r["supply"] == "已切断" or r["encirclement"] != "未被围" or r["retreat"] == "已断"
        or r["morale_state"] != "稳定")]
    status_lines = ["\n■ 状态判定（只能引用，不得改判）"]
    if not abnormal:
        status_lines.append("· 全部单位：补给线未切断、未被包围、退路通畅、士气稳定。")
    for row in abnormal:
        extra = ""
        if row["supply"] == "已切断" and row["cut_by"]:
            extra = f"（{row['cut_at_km']:.1f} km 处被 {row['cut_by']} 截断）"
        status_lines.append(
            f"· {row['name']}（{_team_cn(row['team'])}）：补给 {row['supply']}{extra}"
            f"｜{row['encirclement']}（封锁 {row['sealed_ratio'] * 100:.0f}% 方向）"
            f"｜退路 {row['retreat']}｜士气 {row['morale_state']}"
        )

    tail = """
■ 硬约束（违反其中任一条，本次输出即判为无效）
1. 任何单位的位移不得超过「机动上限」；够不着的单位本回合不得交战，
   `engagements` 里不得出现以它为攻击方的条目。
2. 状态判定为「补给已切断」的，正文不得写成粮道畅通，不得出现补给成功的描述。
3. 状态判定为「被围 / 退路已断」的，正文必须体现出这一困境，不得写成自由机动。
4. 本回合的胜负倾向必须与上述状态一致：被围且补给断的一方不得判为占优。
5. 裁决只给状态，不给具体伤亡数字 —— 伤亡仍由你叙述，但必须与上述状态自洽。
"""
    body = head + "\n".join(move_lines) + "\n" + "\n".join(status_lines) + "\n" + tail

    if len(body) <= max_chars:
        return body

    # 超长：先砍「机动上限」中够不着的行（信息量最低），保留硬约束完整。
    compact = ["\n■ 机动上限（仅列本回合可接触的单位，未列出的均够不着）"]
    for row in result["units"]:
        if row["can_contact"] or row["nearest_enemy"] is None:
            compact.append(
                f"· {row['name']}（{_team_cn(row['team'])}）：可机动 {row['reach_km']:.1f} km；"
                f"距 {row['nearest_enemy']} {row['nearest_enemy_km']:.1f} km ⇒ 可接触"
            )
    trimmed = (head + "\n".join(compact) + "\n" + "\n".join(status_lines)
               + "\n" + tail)
    if len(trimmed) <= max_chars:
        return trimmed
    keep = max(0, max_chars - len(tail) - 60)
    return (head + "\n".join(compact))[:keep] + "\n……（其余单位均未接触）\n" + tail


def _fingerprint(result):
    """结果指纹：同输入同输出时必然一致，可用来证明「可复现」。"""
    payload = {k: v for k, v in result.items() if k not in ("digest", "fingerprint")}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ==========================================
# 🚨 位移合规检查（报告，不改数据）
# ==========================================
def check_movement(prev_state, new_state, adjudication, tolerance=1.15):
    """对比两轮坐标，找出超出裁决机动上限的位移。

    **只报告，不修正**。裁决器的定位是「叙述的边界」，不是「数据的主人」：
    硬把坐标拉回来可能把部队拖进河里或山上，反而制造出更难解释的错误。
    违规事实显示在界面上，让读者自己判断。
    """
    if not isinstance(prev_state, dict) or not isinstance(new_state, dict):
        return []
    allowed = {row["name"]: row["reach_km"] for row in (adjudication or {}).get("units", [])}

    prev_units, _ = _collect_units(prev_state)
    new_units, _ = _collect_units(new_state)
    prev_by_name = {u["name"]: u for u in prev_units}

    violations = []
    for unit in new_units:
        before = prev_by_name.get(unit["name"])
        if before is None:
            continue
        moved = distance_km(before["pos"], unit["pos"])
        limit = allowed.get(unit["name"])
        if limit is None or limit <= 0:
            continue
        if moved > limit * tolerance:
            violations.append({
                "name": unit["name"],
                "moved_km": round(moved, 2),
                "allowed_km": round(limit, 2),
                "over_km": round(moved - limit, 2),
            })
    return violations


# ==========================================
# 📇 战役参数表（adjudication/ 裁决库）
# ==========================================
def load_scenario_params(corpus_dir=None, scenario_text="", battle=None,
                         library_dir=None):
    """返回本回合要覆盖默认值的参数（只含本战役**显式写下**的键）。

    读取逻辑全部在 `adjudication_lib.resolve()` —— 发现文件、按名字匹配、
    校验范围、报错都在那儿。这里只把 BattleSpec.explicit 交出去：
    `adjudicate()` 自己会 `dict(DEFAULT_PARAMS)` 再 update，不需要完整字典。

    ⚠️ 与旧版语义相反的地方：旧版会把目录里**所有** `*.md` 的 frontmatter
    依次合并（后者覆盖前者），场景没命中也会借用别的战役的参数。现在
    **没命中就是没命中**，返回 `{}` —— 战役之间互不串味，才敢往库里加第二场。
    """
    spec = adjudication_lib.resolve(
        scenario_text=scenario_text,
        battle=battle,
        corpus_dir=corpus_dir,
        library_dir=library_dir,
    )
    return dict(spec.explicit)


def load_battle_spec(scenario_text="", battle=None, corpus_dir=None, library_dir=None):
    """同上，但返回完整 BattleSpec —— 界面要用它显示「用了哪个文件、写错了什么」。"""
    return adjudication_lib.resolve(
        scenario_text=scenario_text,
        battle=battle,
        corpus_dir=corpus_dir,
        library_dir=library_dir,
    )

