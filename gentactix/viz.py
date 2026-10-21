"""🗺️ folium 战术地图渲染：北约军标 + 行军轨迹 + 交战圈。

纯渲染层，不持有任何全局可变状态；地图构建所需的 war_state / last_war_state
均由 app.py 显式传入，便于缓存与单测。
"""

import math

import streamlit as st
import folium
from folium import plugins

from .config import to_float, is_naval
from .state import get_unit_field, extract_lat_lng
from .unit_types import classify_unit, unit_symbol
from .battlefield import build_battlefield_view
from .battlefield_viz import draw_overlays

# ==========================================
# 1.1 🗺️ 底图图层组（按实测可达性排序）
# 🐛 修复：原先默认显示 OpenTopoMap，但实测 tile.openstreetmap.org /
#    a.tile.opentopomap.org 在部分网络（尤其国内）**完全不可达**，
#    地图会整片灰白 —— 而地图是本应用的核心 UI。
#    现改为默认卫星实景，并把所有可用底图都挂进右上角图层控制台，
#    用户可随时自行切换。实测：Esri(arcgisonline) 稳定可用、高德可用、
#    OSM / OpenTopoMap 不可达。
# ==========================================
# 渲染器版本标记：界面上直接显示，用来区分「代码没生效」和「本回合确实没有交战」。
# 排查过一轮「改了代码但看不到效果」，根因是浏览器跑的是旧模块 / 旧 iframe，
# 有了这个标记就不必再靠猜。
RENDERER_VERSION = "sector-v3"

TILE_LAYERS = [
    dict(name="🛰️ 天眼卫星实景", show=True,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
         attr="Esri World Imagery", max_native_zoom=18),
    dict(name="🌊 海洋底图", show=False,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
         attr="Esri Ocean Base", max_native_zoom=10),
    # ⚠️ 实测（长平 35.70,112.93）：Esri 地形底图在中国大陆只到 z13，
    #    z14+ 返回官方的 "Map data not yet available" 灰色占位瓦片（2521 字节）。
    #    这里声明 max_native_zoom=13，Leaflet 会在更高缩放时放大 z13 瓦片
    #    —— 画面会糊，但不会整片空白。（卫星实景实测到 z18 正常，故设为 18。）
    dict(name="🏔️ 战术地形图", show=False,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
         attr="Esri World Topo", max_native_zoom=13),
    dict(name="🗺️ OpenTopoMap（国内网络通常不可达）", show=False,
         url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
         attr="OpenTopoMap", max_native_zoom=17),
    # ⚠️ 高德的瓦片是 GCJ-02 坐标系，而本应用所有单位坐标是 WGS-84，
    #    直接叠加会有数百米偏移，仅作应急备用。
    # 🚫 已隐藏（enabled=False）：它和「军标落在真实地形上」这个核心卖点直接冲突 ——
    #    几百米偏移在长平那种几公里纵深的战场上足以把赵军垒摆到丹河对岸。
    #    代码留着是为了以后真要做 GCJ-02→WGS-84 纠偏时还有瓦片地址可捡；
    #    想临时放出来，把 enabled 改成 True 即可。
    dict(name="🇨🇳 高德路网（GCJ-02，点位会有数百米偏移）", show=False, enabled=False,
         url="https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
         attr="高德地图", max_native_zoom=18),
]


# ==========================================
# 🌟 核心升级：北约 APP-6 标准军标渲染引擎
# ==========================================
def get_nato_html(unit, color_theme):
    unit_type = str(get_unit_field(unit, 'type', '类型', '兵种', 'unit_type', 'class')).lower()
    # 🐛 修复：原先只认 unit.get('name')，AI 一旦按中文键名（"名称"/"单位名称"）输出，
    #          所有铭牌就集体退化成 "未知单位"。这里补上中文别名，杜绝半坏状态。
    unit_name = str(get_unit_field(
        unit, 'name', '名称', '单位名称', '部队名称', 'unit_name', default='未知单位'))

    # 1. 判定内部战术核心符号
    symbol = "?"
    font_size = "20px"
    if unit_symbol(unit_type) is not None:
        symbol = unit_symbol(unit_type)
    elif any(k in unit_type for k in ['步兵', '陆战', '军团', '师', '旅', 'infantry', 'marine', 'troop', 'army']):
        symbol = "X"  # 交叉带，代表步兵
        font_size = "20px"
    elif any(k in unit_type for k in ['坦克', '装甲', '战车', '机动', '骑兵', 'tank', 'armor', 'vehicle', 'cavalry']):
        # 履带/椭圆，代表装甲与高机动部队
        symbol = "<div style='width: 14px; height: 6px; border: 2px solid black; border-radius: 50%;'></div>"
    elif any(k in unit_type for k in ['炮', '防空', '导弹', '火力', 'artillery', 'missile', 'anti-air']):
        symbol = "●"  # 炮弹落点，代表炮兵/远程打击
        font_size = "20px"
    elif any(k in unit_type for k in ['机', '轰炸', '航空', '飞行', 'plane', 'fighter', 'bomber', 'air']):
        symbol = "∞"  # 螺旋桨/无限符号，代表航空兵
        font_size = "20px"
    elif any(k in unit_type for k in ['舰', '船', '航母', '潜艇', '巡洋', '驱逐', 'ship', 'carrier', 'submarine', 'destroyer', 'cruiser', 'fleet']):
        symbol = "⚓"  # 船锚，通用海军符号
        font_size = "20px"
    elif any(k in unit_type for k in ['指挥', '基地', '总部', '阵地', 'hq', 'base', 'command', 'center']):
        symbol = "★"  # 星号，代表各级指挥所
        font_size = "20px"

    # 2. 判定外部阵营边框 (红方敌军为菱形，蓝方友军为矩形)
    if color_theme == 'red':
        # 绘制菱形边框并灌入淡红色底漆
        frame_html = f"""
        <div style="position: relative; width: 30px; height: 30px;">
            <div style="position: absolute; top: 3px; left: 3px; width: 22px; height: 22px;
                        background: rgba(255, 180, 180, 0.85); border: 2px solid #cc0000;
                        transform: rotate(45deg); box-shadow: 2px 2px 5px rgba(0,0,0,0.5);"></div>
            <div style="position: absolute; top: 0; left: 0; width: 30px; height: 30px;
                        display: flex; align-items: center; justify-content: center;
                        color: black; font-weight: bold; font-size: {font_size}; font-family: sans-serif; z-index: 10;">
                {symbol}
            </div>
        </div>
        """
    else:
        # 绘制矩形边框并灌入淡蓝色底漆
        frame_html = f"""
        <div style="width: 36px; height: 22px;
                    background: rgba(180, 210, 255, 0.85); border: 2px solid #0044cc;
                    box-shadow: 2px 2px 5px rgba(0,0,0,0.5);
                    display: flex; align-items: center; justify-content: center;
                    color: black; font-weight: bold; font-size: {font_size}; font-family: sans-serif;">
            {symbol}
        </div>
        """

    # 3. 组装：将几何军标与底部的深色半透明铭牌合并
    return f"""
    <div style="display: flex; flex-direction: column; align-items: center; width: 120px; margin-left: -60px; margin-top: -15px;">
        {frame_html}
        <div style="background: rgba(0,0,0,0.7); color: #fff; font-size: 11px; padding: 2px 5px; border-radius: 3px; margin-top: 3px; font-family: sans-serif; white-space: nowrap; border: 1px solid rgba(255,255,255,0.2);">
            {unit_name}
        </div>
    </div>
    """


def draw_units_and_trails(m, current_units, color, team_key, last_war_state):
    """在地图 m 上绘制一支军队的单位军标，并按名称匹配上一回合位置绘制行军轨迹。"""
    for unit in current_units:
        hp = unit.get('hp', unit.get('HP', unit.get('health', unit.get('兵力', '未知'))))
        # 🐛 修复：原代码只判 None，字符串坐标会被原样塞给 folium，导致标记整体不渲染
        lat, lng = extract_lat_lng(unit)

        if lat is None or lng is None:
            continue
        curr_pos = [lat, lng]

        unit_type = get_unit_field(unit, 'type', '类型', '兵种', 'unit_type', 'class')
        # 🐛 修复：同上，名称读取统一走别名链，避免铭牌/弹窗/轨迹提示三处口径不一致
        unit_name = get_unit_field(
            unit, 'name', '名称', '单位名称', '部队名称', 'unit_name', default='未知单位')
        # 🐛 修复：AI 可能把 morale/supply 写成字符串，原代码直接比较会 TypeError
        morale = to_float(unit.get('morale', 100))
        supply = to_float(unit.get('supply', 100))
        morale = 100.0 if morale is None else morale
        supply = 100.0 if supply is None else supply

        morale_icon = "🔥" if morale > 50 else "⚠️"
        supply_icon = "🔋" if supply > 50 else "🪫"

        # 鼠标点击后的浮动详细情报
        popup_text = f"<b>{unit_name}</b><br>类型: {unit_type}<br>HP: ❤️ {hp}<br>士气: {morale_icon} {morale}%<br>补给: {supply_icon} {supply}%"

        # 🌟 调用纯手工编写的 CSS 北约军标
        html_icon = get_nato_html(unit, color)

        # 使用 folium.DivIcon 挂载 HTML 渲染器
        folium.Marker(
            curr_pos,
            popup=folium.Popup(popup_text, max_width=200),
            icon=folium.DivIcon(html=html_icon)
        ).add_to(m)

        if last_war_state:
            for last_unit in last_war_state.get(team_key, []):
                if not isinstance(last_unit, dict):
                    continue
                # 🐛 修复：上一回合单位若带的是中文键名，原 `last_unit.get('name')` 恒为 None，
                #    会导致行军轨迹整段画不出来（只有军标、没有足迹线）。
                last_name = get_unit_field(
                    last_unit, 'name', '名称', '单位名称', '部队名称', 'unit_name')
                if last_name == unit_name:
                    # 🐛 修复：上一回合坐标同样需要数值校验，否则行军轨迹线不渲染
                    last_lat = to_float(last_unit.get('lat'))
                    last_lng = to_float(last_unit.get('lng'))
                    if last_lat is None or last_lng is None:
                        break
                    last_pos = [last_lat, last_lng]
                    if last_pos != curr_pos:
                        movement_line = folium.PolyLine(
                            locations=[last_pos, curr_pos],
                            color=color,
                            weight=2,
                            opacity=0.6,
                            dash_array='5, 5',
                            tooltip=f"{unit_name} 的行军路线"
                        ).add_to(m)

                        plugins.PolyLineTextPath(
                            movement_line,
                            '  ➤  ',
                            repeat=False,
                            center=True,
                            offset=8,
                            attributes={'fill': color, 'font-weight': 'bold', 'font-size': '18'}
                        ).add_to(m)

                        plugins.AntPath(
                            locations=[last_pos, curr_pos],
                            color=color,
                            weight=5,
                            opacity=0.3,
                            dash_array=[15, 20],
                            delay=800,
                        ).add_to(m)
                    break


# ==========================================
# 1.4 🎯 交战波及半径：按攻击单位射程推导（不再写死 1500）
# ==========================================
# 🐛 修复：prompts.py 的返回示例里曾固定写着 "radius": 1500，AI 直接照抄，
#    于是**每一场**交战的半径都恒等于 1500 米 —— 圆的尺寸不携带任何信息，
#    tooltip 全是「1500.0 米」。半径必须由数据推导，而不是由示例文本决定：
#      攻击单位 attack_range × 1.2（射程 + 落点散布）
#      ↓ 射程缺失
#      兵种查表（骑射 800 / 弓弩 300 / 步战 500）
#      ↓ 连攻击单位都匹配不到
#      战局显式半径 → 中性默认值
# ⚠️ 战局显式 radius 只作最后兜底：它最可能就是从示例里抄来的那个 1500。
ENGAGEMENT_RADIUS_FACTOR = 1.2

# 兵种默认射程（米），仅用于 attack_range 缺失时。
# ⚠️ 顺序有意义：更具体的关键词必须排在前面（"骑射" 先于 "骑兵" / "弓"）。
UNIT_TYPE_RANGE_HINTS = (
    ('骑射', 800.0), ('弓骑', 800.0), ('mounted archer', 800.0), ('horse archer', 800.0),
    ('投石', 400.0), ('弩', 300.0), ('弓', 300.0), ('archer', 300.0), ('bow', 300.0),
    ('步', 500.0), ('infantry', 500.0), ('战车', 500.0), ('chariot', 500.0),
    ('骑兵', 500.0), ('cavalry', 500.0),
)
DEFAULT_MELEE_RANGE = 500.0          # 兵种也未知时按步战推定
NEUTRAL_ENGAGEMENT_RADIUS = 1500.0   # 完全没有任何线索时的中性值
MIN_ENGAGEMENT_RADIUS = 50.0
MAX_ENGAGEMENT_RADIUS = 50000.0

# 就近匹配攻击单位的容差（度，约 15 km）：超出视为「不是这一场的人」，
# 宁可退回兵种估算，也不要把 20 km 外的部队当成攻击方。
MAX_ATTACKER_MATCH_DEG = 0.15


def _clamp_engagement_radius(radius_m):
    return max(MIN_ENGAGEMENT_RADIUS, min(MAX_ENGAGEMENT_RADIUS, float(radius_m)))


def unit_type_default_range(unit_type):
    """按兵种关键词查默认射程（米）。查不到返回 None，调用方自行兜底。"""
    text = str(unit_type or '').strip().lower()
    if not text:
        return None
    profile = classify_unit(text)
    if profile:
        return profile["range_hint_m"]
    for keyword, default_range in UNIT_TYPE_RANGE_HINTS:
        if keyword in text:
            return default_range
    return None


def _unit_distance_deg(unit, lat, lng):
    """单位到某点的近似平面距离（度，经度已按纬度修正），仅用于就近匹配。"""
    u_lat = to_float(get_unit_field(unit, 'lat', 'latitude', '纬度'))
    u_lng = to_float(get_unit_field(unit, 'lng', 'longitude', '经度'))
    if u_lat is None or u_lng is None:
        return None
    k = max(0.1, math.cos(math.radians(abs(lat))))
    return math.hypot(u_lat - lat, (u_lng - lng) * k)


def _nearest_unit(units, lat, lng, max_deg=MAX_ATTACKER_MATCH_DEG):
    best, best_dist = None, None
    for unit in units:
        if not isinstance(unit, dict):
            continue
        dist = _unit_distance_deg(unit, lat, lng)
        if dist is None or dist > max_deg:
            continue
        if best_dist is None or dist < best_dist:
            best, best_dist = unit, dist
    return best


def find_attacker_unit(battle, war_state, lat, lng):
    """定位交战对应的攻击单位：显式名称 → 攻击方坐标就近 → 交战点就近。

    名称匹配最可靠；坐标匹配覆盖 AI 只给位置的情况；交战点就近是最后的近似。
    全都失败返回 None —— 此时宁可用兵种默认值，也不猜一个单位。
    """
    units = []
    for key in ('red_units', 'blue_units'):
        team = war_state.get(key) or []
        if isinstance(team, list):
            units.extend(u for u in team if isinstance(u, dict))
    if not units:
        return None

    raw_name = battle.get('attacker') or battle.get('attacker_name') or battle.get('攻击方')
    if isinstance(raw_name, str) and raw_name.strip():
        target = raw_name.strip()
        for unit in units:
            name = get_unit_field(unit, 'name', '名称', 'unit_name')
            if str(name or '').strip() == target:
                return unit

    attacker_pos = battle.get('attacker_pos')
    if isinstance(attacker_pos, (list, tuple)) and len(attacker_pos) == 2:
        a_lat = to_float(attacker_pos[0])
        a_lng = to_float(attacker_pos[1])
        if a_lat is not None and a_lng is not None:
            unit = _nearest_unit(units, a_lat, a_lng)
            if unit is not None:
                return unit

    return _nearest_unit(units, lat, lng)


def resolve_engagement_radius(battle, war_state, lat, lng):
    """返回 (半径米, 推导依据)。依据会直接进 tooltip，让读数可追溯而非凭空。"""
    unit = find_attacker_unit(battle, war_state, lat, lng)

    if unit is not None:
        unit_name = str(get_unit_field(unit, 'name', '名称', 'unit_name') or '攻击方')
        unit_type = str(get_unit_field(unit, 'type', '类型', '兵种', 'unit_type') or '')
        attack_range = to_float(get_unit_field(
            unit, 'attack_range', '射程', '攻击范围', '打击范围', 'range'))

        if attack_range is not None and attack_range > 0:
            radius = _clamp_engagement_radius(attack_range * ENGAGEMENT_RADIUS_FACTOR)
            return radius, f"{unit_name} 射程 {attack_range:g}m × {ENGAGEMENT_RADIUS_FACTOR:g}"

        hint = unit_type_default_range(unit_type)
        if hint is not None:
            radius = _clamp_engagement_radius(hint * ENGAGEMENT_RADIUS_FACTOR)
            label = f"{unit_name}·{unit_type}" if unit_type else unit_name
            return radius, f"{label} 按兵种射程 {hint:g}m × {ENGAGEMENT_RADIUS_FACTOR:g}"

        # 有单位但射程/兵种都读不出：按步战推定，明确标注，不假装精确。
        radius = _clamp_engagement_radius(DEFAULT_MELEE_RANGE * ENGAGEMENT_RADIUS_FACTOR)
        return radius, f"{unit_name} 射程与兵种均未知，按步战推定 {DEFAULT_MELEE_RANGE:g}m × {ENGAGEMENT_RADIUS_FACTOR:g}"

    explicit = to_float(battle.get('radius'))
    if explicit is not None and explicit > 0:
        return _clamp_engagement_radius(explicit), "未匹配到攻击单位，沿用战局给出的半径"
    return _clamp_engagement_radius(NEUTRAL_ENGAGEMENT_RADIUS), "未匹配到攻击单位，使用中性默认值"


def impact_sector_vertices(origin_lat, origin_lng, target_lat, target_lng,
                           radius_m, half_angle_deg=30.0, step_deg=5.0):
    """生成「定向扇区」的多边形顶点（folium 要求的 [lat, lng] 序列）。

    设计取舍（替代原先的正圆）：
      1. 圆心是**攻击方位置**，不是交战点 —— 扇区回答的是「谁朝哪个方向能打到」，
         交战点只是打击落点。用交战点当圆心会让方向信息彻底消失。
      2. 方位角从正北顺时针计（a=0 → 北，a=90 → 东），与 atan2(Δlng, Δlat) 一致。
      3. 半角取 ±30°：冷兵器/线列交战是正面接触，不是全向覆盖；
         真正的"全向"场景请用空心环（调用方会在无 attack 方向时降级）。
      4. 东西向按 cos(lat) 修正，避免高纬度扇区被压扁。战场尺度下这是必要且足够的近似。
    """
    bearing = math.degrees(math.atan2(target_lng - origin_lng, target_lat - origin_lat))

    r_lat = radius_m / 111320.0
    r_lng = radius_m / (111320.0 * max(0.1, math.cos(math.radians(abs(origin_lat)))))

    steps = max(1, int(round(2 * half_angle_deg / step_deg)))
    pts = [[origin_lat, origin_lng]]
    for i in range(steps + 1):
        angle = math.radians(bearing - half_angle_deg + i * step_deg)
        pts.append([
            origin_lat + r_lat * math.cos(angle),
            origin_lng + r_lng * math.sin(angle),
        ])
    pts.append([origin_lat, origin_lng])
    return pts


def iter_engagement_specs(war_state):
    """逐条解析交战对象，产出绘制所需的结构化数据（含丢弃原因）。

    单一入口：**地图绘制与界面统计都走这里**，避免「统计说 2 处、地图上 0 个」
    这类自相矛盾。原先渲染层只认英文键且遇到非法坐标直接 continue，
    AI 一旦改口径（中文键 / 字符串坐标），整场交火就静默消失、无任何提示。
    """
    engagements = war_state.get('engagements') if isinstance(war_state, dict) else None
    if not isinstance(engagements, list):
        engagements = []

    for battle in engagements:
        if not isinstance(battle, dict):
            yield {'valid': False, 'reason': '条目不是对象'}
            continue

        b_lat = to_float(get_unit_field(battle, 'lat', '纬度', 'latitude', default=None))
        b_lng = to_float(get_unit_field(battle, 'lng', '经度', 'longitude', default=None))
        if b_lat is None or b_lng is None:
            yield {'valid': False, 'reason': '缺少可用经纬度'}
            continue

        desc = get_unit_field(battle, 'description', '描述', '交战描述', '战况',
                              default='激烈交火区域')

        # 半径：按攻击单位射程推导（详见 resolve_engagement_radius 注释）
        impact_radius, radius_basis = resolve_engagement_radius(battle, war_state, b_lat, b_lng)

        # 🌟 定向扇区：攻击方坐标决定方位角（弧从攻击方指向交战点）
        attacker_raw = get_unit_field(battle, 'attacker_pos', '攻击者位置', '攻击方坐标',
                                      default=None)
        a_lat = a_lng = None
        if isinstance(attacker_raw, (list, tuple)) and len(attacker_raw) == 2:
            a_lat, a_lng = to_float(attacker_raw[0]), to_float(attacker_raw[1])

        # 攻击方缺失 / 非法 / 与交战点重合（方位角无意义）⇒ 降级为空心虚线环。
        # ⚠️ 绝不能「猜一个方向」硬画：指向错误的扇区比不表达方向更有害。
        same_as_target = (a_lat is not None and a_lng is not None
                          and abs(a_lat - b_lat) < 1e-9 and abs(a_lng - b_lng) < 1e-9)
        use_sector = (a_lat is not None and a_lng is not None and not same_as_target)

        yield {
            'valid': True,
            'lat': b_lat, 'lng': b_lng,
            'desc': desc,
            'radius': impact_radius,
            'basis': radius_basis,
            'use_sector': use_sector,
            'attacker': (a_lat, a_lng),
        }


def summarize_engagements(war_state):
    """统计本回合交战：总数 / 可绘制 / 被丢弃 / 扇区数 / 降级环数。"""
    stats = {'total': 0, 'valid': 0, 'skipped': 0, 'sector': 0, 'ring': 0, 'reasons': []}
    for spec in iter_engagement_specs(war_state):
        stats['total'] += 1
        if not spec['valid']:
            stats['skipped'] += 1
            stats['reasons'].append(spec['reason'])
            continue
        stats['valid'] += 1
        stats['sector' if spec['use_sector'] else 'ring'] += 1
    return stats


def with_demo_engagement(war_state):
    """返回一份**副本**，在战区中心附近追加一个示例交战（仅供渲染自检，不改战局数据）。

    用途：不必等 AI 真的打出交战（长平那种开局距接战还有几十小时的情况），
    一眼就能确认扇区渲染链路是否活着。
    """
    state_copy = dict(war_state or {})
    center = state_copy.get('center')
    try:
        c_lat, c_lng = float(center[0]), float(center[1])
    except (TypeError, ValueError, IndexError, KeyError):
        c_lat, c_lng = 35.70, 112.93

    demo = {
        'lat': c_lat,
        'lng': c_lng + 0.012,
        'description': '🧪 渲染自检示例（虚构交战，不写入战局）',
        'attacker': '自检攻击方',
        'attacker_pos': [c_lat - 0.010, c_lng - 0.004],
        'radius': 2500,
    }
    state_copy['engagements'] = list(state_copy.get('engagements') or []) + [demo]
    return state_copy


def build_tactical_map(war_state, last_war_state, turn_count, battlefield_view=None):
    """根据当前战局构建 folium 战术地图对象。纯函数：相同入参产出相同地图。"""
    # 🐛 修复：原代码直接索引 war_state['center']，AI 漏字段时会 KeyError 白屏
    center = war_state.get('center')
    if not (isinstance(center, (list, tuple)) and len(center) == 2
            and to_float(center[0]) is not None and to_float(center[1]) is not None):
        center = [0.0, 0.0]
        st.warning("⚠️ AI 未返回有效的战区中心坐标，地图暂以 (0, 0) 显示，请检查战局数据。")

    # 1. 基础战区地图
    # 🐛 修复：原 tiles="选择地图类型" 是无效瓦片模板，会导致底图整片灰白；
    #         这里交由下方显式声明的两个 TileLayer 承担底图职责。
    m = folium.Map(
        location=center,
        zoom_start=11,
        tiles=None,
        attr="GenTactix Tactical Engine | Python History Strategy Lab"
    )

    for _cfg in TILE_LAYERS:
        # enabled=False 的底图不挂进图层控件，等于从界面上消失（配置仍保留在 TILE_LAYERS 里）
        if not _cfg.get("enabled", True):
            continue
        folium.TileLayer(
            tiles=_cfg["url"],
            attr=_cfg["attr"],
            name=_cfg["name"],
            overlay=False,   # False = 互斥底图，而不是透明叠加层
            control=True,
            show=_cfg["show"],
            # 超过该级别不再请求瓦片，改为放大最后一级 —— 避免整片
            # "Map data not yet available" 占位图（Esri 地形/街道在中国的实际上限）
            max_native_zoom=_cfg.get("max_native_zoom"),
        ).add_to(m)

    draw_overlays(m, battlefield_view or build_battlefield_view(war_state))
    draw_units_and_trails(m, war_state.get('red_units', []), 'red', 'red_units', last_war_state)
    draw_units_and_trails(m, war_state.get('blue_units', []), 'blue', 'blue_units', last_war_state)

    for spec in iter_engagement_specs(war_state):
        if not spec['valid']:
            continue
        b_lat_f, b_lng_f = spec['lat'], spec['lng']
        battle_pos = [b_lat_f, b_lng_f]
        desc = spec['desc']
        impact_radius = spec['radius']
        radius_basis = spec['basis']
        use_sector = spec['use_sector']

        if impact_radius >= 20000:
            border_color, fill_color, icon_color = 'black', 'purple', 'darkpurple'
        elif impact_radius >= 5000:
            border_color, fill_color, icon_color = 'darkred', 'crimson', 'darkred'
        elif impact_radius <= 500:
            border_color, fill_color, icon_color = 'darkorange', 'orange', 'orange'
        else:
            border_color, fill_color, icon_color = 'crimson', 'red', 'red'

        # 形状自身的 tooltip 必须带上交战描述：
        # 原实现把描述放在 marker 上，而 marker 被整片圆盖住，hover 命中的永远是圆，
        # 于是「谁打谁」这条最关键的信息在界面上等于不存在。
        shape_tooltip = (f"💥 <b>{desc}</b><br>交战示意范围 ≈ {impact_radius:.0f} 米（非杀伤半径）"
                         f"<br><span style='color:#8a8a8a'>依据：{radius_basis}</span>"
                         + ("" if use_sector else "<br>（未提供攻击方位，仅显示范围）"))

        if use_sector:
            a_lat, a_lng = spec['attacker']
            folium.Polygon(
                locations=impact_sector_vertices(a_lat, a_lng, b_lat_f, b_lng_f, impact_radius),
                color=border_color, weight=1.5,
                fill=True, fill_color=fill_color, fill_opacity=0.12,
                dash_array='6 4', tooltip=shape_tooltip,
            ).add_to(m)
        else:
            folium.Circle(
                location=battle_pos, radius=impact_radius, color=border_color,
                weight=1.5, fill=False, dash_array='6 4', tooltip=shape_tooltip,
            ).add_to(m)

        folium.Marker(battle_pos, icon=folium.Icon(color=icon_color, icon='fire', prefix='fa'),
                      tooltip=f"💥 <b>交战发生 (示意 {impact_radius:.0f} 米，非杀伤半径)</b><br>{desc}").add_to(m)

        # 火力打击轨迹：扇区已经表达了方向，这里的动画只负责指向落点，故压细压淡
        if use_sector:
            a_lat, a_lng = spec['attacker']
            plugins.AntPath(
                locations=[[a_lat, a_lng], battle_pos],
                color=border_color,
                pulse_color='white',  # 脉冲颜色设为白色，有种曳光弹/激光的视觉效果
                weight=1.5,
                opacity=0.55,
                dash_array=[5, 20],
                delay=400,            # 动画速度极快，模拟火力投射
                tooltip="🔥 火力打击方向"
            ).add_to(m)

    # 🌟 核心升级：战术沙盘全屏展开模块
    plugins.Fullscreen(
        position='topleft',       # 全屏按钮的放置位置
        title='展开全屏战术地图',   # 鼠标悬停时的中文提示
        title_cancel='退出全屏',   # 全屏状态下的退出提示
        force_separate_button=True
    ).add_to(m)

    # 3. 在所有图层和元素绘制完毕后，添加图层控制台
    # 必须放在所有的 .add_to(m) 的最后面
    folium.LayerControl(position='topright', collapsed=False).add_to(m)

    return m
