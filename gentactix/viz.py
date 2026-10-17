"""🗺️ folium 战术地图渲染：北约军标 + 行军轨迹 + 交战圈。

纯渲染层，不持有任何全局可变状态；地图构建所需的 war_state / last_war_state
均由 app.py 显式传入，便于缓存与单测。
"""

import streamlit as st
import folium
from folium import plugins

from .config import to_float, is_naval
from .state import get_unit_field, extract_lat_lng

# ==========================================
# 1.1 🗺️ 底图图层组（按实测可达性排序）
# 🐛 修复：原先默认显示 OpenTopoMap，但实测 tile.openstreetmap.org /
#    a.tile.opentopomap.org 在部分网络（尤其国内）**完全不可达**，
#    地图会整片灰白 —— 而地图是本应用的核心 UI。
#    现改为默认卫星实景，并把所有可用底图都挂进右上角图层控制台，
#    用户可随时自行切换。实测：Esri(arcgisonline) 稳定可用、高德可用、
#    OSM / OpenTopoMap 不可达。
# ==========================================
TILE_LAYERS = [
    dict(name="🛰️ 天眼卫星实景", show=True,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
         attr="Esri World Imagery"),
    dict(name="🌊 海洋底图", show=False,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
         attr="Esri Ocean Base"),
    dict(name="🏔️ 战术地形图", show=False,
         url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
         attr="Esri World Topo"),
    dict(name="🗺️ OpenTopoMap（国内网络通常不可达）", show=False,
         url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
         attr="OpenTopoMap"),
    # ⚠️ 高德的瓦片是 GCJ-02 坐标系，而本应用所有单位坐标是 WGS-84，
    #    直接叠加会有数百米偏移，仅作应急备用。
    dict(name="🇨🇳 高德路网（GCJ-02，点位会有数百米偏移）", show=False,
         url="https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
         attr="高德地图"),
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
    if any(k in unit_type for k in ['步兵', '陆战', '军团', '师', '旅', 'infantry', 'marine', 'troop', 'army']):
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


def build_tactical_map(war_state, last_war_state, turn_count):
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
        folium.TileLayer(
            tiles=_cfg["url"],
            attr=_cfg["attr"],
            name=_cfg["name"],
            overlay=False,   # False = 互斥底图，而不是透明叠加层
            control=True,
            show=_cfg["show"]
        ).add_to(m)

    draw_units_and_trails(m, war_state.get('red_units', []), 'red', 'red_units', last_war_state)
    draw_units_and_trails(m, war_state.get('blue_units', []), 'blue', 'blue_units', last_war_state)

    engagements = war_state.get('engagements') or []
    if not isinstance(engagements, list):
        engagements = []
    for battle in engagements:
        if not isinstance(battle, dict):
            continue
        b_lat, b_lng = battle.get('lat'), battle.get('lng')
        desc = battle.get('description', '激烈交火区域')

        # 🐛 修复：原为裸 except，会吞掉 KeyboardInterrupt 等系统级异常
        try:
            impact_radius = float(battle.get('radius', 1500))
        except (TypeError, ValueError):
            impact_radius = 1500.0
        if impact_radius <= 0:
            impact_radius = 1500.0

        if impact_radius >= 20000:
            border_color, fill_color, icon_color = 'black', 'purple', 'darkpurple'
        elif impact_radius >= 5000:
            border_color, fill_color, icon_color = 'darkred', 'crimson', 'darkred'
        elif impact_radius <= 500:
            border_color, fill_color, icon_color = 'darkorange', 'orange', 'orange'
        else:
            border_color, fill_color, icon_color = 'crimson', 'red', 'red'

        # 🐛 修复：坐标可能是字符串，统一做数值校验，避免 folium 收到脏数据
        b_lat_f, b_lng_f = to_float(b_lat), to_float(b_lng)
        if b_lat_f is not None and b_lng_f is not None:
            battle_pos = [b_lat_f, b_lng_f]

            folium.Marker(battle_pos, icon=folium.Icon(color=icon_color, icon='fire', prefix='fa'),
                          tooltip=f"💥 <b>交战发生 (波及 {impact_radius} 米)</b><br>{desc}").add_to(m)
            folium.Circle(location=battle_pos, radius=impact_radius, color=border_color, weight=2,
                          fill=True, fill_color=fill_color, fill_opacity=0.4,
                          tooltip=f"杀伤/影响范围: {impact_radius} 米").add_to(m)

            # 🌟 修复：安全提取 attacker_pos 并绘制火力打击轨迹
            attacker_pos_raw = battle.get('attacker_pos')
            if isinstance(attacker_pos_raw, (list, tuple)) and len(attacker_pos_raw) == 2:
                a_lat = to_float(attacker_pos_raw[0])
                a_lng = to_float(attacker_pos_raw[1])
                if a_lat is not None and a_lng is not None:
                    # 🌟 升级为动态火力打击轨迹
                    plugins.AntPath(
                        locations=[[a_lat, a_lng], battle_pos],
                        color=border_color,
                        pulse_color='white',  # 脉冲颜色设为白色，有种曳光弹/激光的视觉效果
                        weight=2,
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
