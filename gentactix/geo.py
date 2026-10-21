"""🌍 战术雷达阵列 + 物理坐标校验与清洗。

原 Gentactix.py 中散落的「海陆面具 / SRTM 高程 / 强制落水 / 坐标错误扫描」
逻辑统一收归此处，并以可复用的清洗函数替代 init / next-turn 两处重复的落水循环。
"""

import math

try:
    import streamlit as st  # Streamlit 前端可用时走 UI 提示
except ImportError:
    st = None  # 无 Streamlit 运行时（如 FastAPI 后端）降级为静默捕获
from global_land_mask import globe  # 🌟 核心：引入地球海陆面具

from .config import to_float, is_naval
from .state import get_unit_field, normalize_state_units
from .anchors import node_metadata

# 红蓝双方单位容器键名（全应用统一引用，避免硬编码字符串漂移）
UNIT_TEAMS = ["red_units", "blue_units"]

# 无 Streamlit 时，清洗/吸附提示收集到这里，由调用方（后端 API）转交给界面显示。
GEO_NOTICES = []


def _notify(level, message, icon=None):
    """UI 提示单点：Streamlit 在就弹 Streamlit，不在就进 GEO_NOTICES。"""
    if st is not None:
        if level == "toast":
            st.toast(message, icon=icon)
        elif level == "error":
            st.error(message)
        else:
            st.warning(message)
    else:
        GEO_NOTICES.append({"level": level, "message": message})


# ==========================================
# 🌍 战术雷达阵列初始化
# ==========================================
# 启动 SRTM 军用级高程探测器（第一次探测某区域时会在本地自动下载缓存文件）
# 🐛 修复：原代码每次 rerun 都重新执行 srtm.get_data()，属无谓开销；改用进程级缓存
def _load_elevation_data():
    try:
        import srtm
        return srtm.get_data()
    except Exception:
        return None


if st is not None:
    _load_elevation_data = st.cache_resource(show_spinner=False)(_load_elevation_data)
else:
    # 无 Streamlit 时自己做进程级缓存
    _elevation_raw = _load_elevation_data
    _elevation_cache = {}

    def _load_elevation_data():
        if "data" not in _elevation_cache:
            _elevation_cache["data"] = _elevation_raw()
        return _elevation_cache["data"]


elevation_data = _load_elevation_data()
if elevation_data is None:
    _notify("warning", "⚠️ SRTM 地形雷达初始化失败，已降级为「海陆面具」单波段扫描（近岸精度下降）。")


def is_water_precise(lat, lng):
    """
    🌟 双波段海陆雷达扫描 (Macro + Micro)
    返回 True 表示判定为水域。
    """
    lat_f, lng_f = to_float(lat), to_float(lng)
    if lat_f is None or lng_f is None:
        return False

    # 1. 宏观波段：如果判定在大洋深处，瞬间放行，节省算力
    if not globe.is_land(lat_f, lng_f):
        return True

    # 2. 微观波段：如果靠近海岸线或宏观雷达误判为陆地，启动 SRTM 高程微雕复判
    if elevation_data:
        # 🐛 修复：原代码未保护取高程的调用，断网/越界时异常会直接中断整次推演
        try:
            elevation = elevation_data.get_elevation(lat_f, lng_f)
        except Exception:
            return False
        # None 也可能是瓦片缺失/下载失败，不能据此把海陆面具确认的陆地改判为海洋。
        if elevation is not None and elevation <= 0:
            return True

    return False


# ==========================================
# 🛡️ 引擎底层校验与物理修正法则
# ==========================================
def get_coordinate_errors(state):
    """扫描地图，如果有海军在陆地上，返回具体的错误信息列表 (SRTM 升级版)"""
    errors = []

    for team in UNIT_TEAMS:
        for unit in state.get(team, []):
            unit_type = get_unit_field(unit, 'type', '类型', '兵种', 'unit_type', 'class')

            if is_naval(unit_type):
                lat = get_unit_field(unit, 'lat', 'latitude', '纬度')
                lng = get_unit_field(unit, 'lng', 'longitude', '经度')

                # 🌟 核心升级：使用双波段雷达进行绝对物理校验
                if not is_water_precise(lat, lng):
                    unit_name = get_unit_field(
                        unit, 'name', '名称', '单位名称', '部队名称', 'unit_name', default='未命名单位')
                    errors.append(
                        f"错误：你将海军单位 '{unit_name}' 部署在了坐标 [{lat}, {lng}]，"
                        f"该坐标的绝对海拔高度处于陆地之上！海军必须在水域，请重新规划航线。"
                    )
    return errors


def force_into_water(lat, lng, max_radius_deg=0.2, step_deg=0.005):
    """
    🌊 高精度强制落水算法 (SRTM 近岸微雕版)

    🐛 修复：
      1. 原实现在 while 循环内重复构造 angles，且缺少数值校验；
      2. 传入字符串/None 坐标时会抛出 TypeError 而不是安全返回。
    """
    lat_f, lng_f = to_float(lat), to_float(lng)
    if lat_f is None or lng_f is None:
        return lat, lng

    # 1. 启动高精雷达探测，如果已经在水里，安全放行
    if is_water_precise(lat_f, lng_f):
        return lat, lng

    # 2. 如果确认在陆地上，开启极密雷达外扩搜索
    angles = range(0, 360, 10)  # 每 10 度扫一下，共 36 个方向的高频阵列
    radius = step_deg
    while radius <= max_radius_deg:
        for angle in angles:
            rad = math.radians(angle)
            test_lat = lat_f + radius * math.cos(rad)
            test_lng = lat_f + radius * math.sin(rad)

            test_lat = max(-90, min(90, test_lat))
            test_lng = ((test_lng + 180) % 360) - 180

            # 只要微雕雷达扫到了海平线，立刻把船拖过去并返回！
            if is_water_precise(test_lat, test_lng):
                return round(test_lat, 5), round(test_lng, 5)

        radius += step_deg

    return lat, lng


# ==========================================
# 🧭 高精锚点匹配与单位坐标清洗
# ==========================================
def match_anchor(unit_name, available_nodes):
    """在已解锁的高精锚点库中按核心短名匹配单位，命中返回 (anchor_name, coords)。"""
    unit_name = unit_name or ''
    # ⚠️ 必须排除空名：`"" in 任意字符串` 恒为 True，否则所有缺名字的单位都会被
    #    吸到字典里第一个锚点上，十几支部队叠成一个点，还看不出是 bug。
    if not unit_name:
        return None, None
    for anchor_name, coords in available_nodes.items():
        # 朝鲜战争参考点包含城市、山体，单位名称提到目标不等于驻于该点。
        # 必须显式声明 node_name；保留旧舰艇/长平库原有名称匹配契约。
        if node_metadata(anchor_name):
            continue
        # 提取核心短键名（如 "福特岛-战列舰大街-亚利桑那号" → "亚利桑那号"）
        core_name = anchor_name.split('-')[-1]
        if core_name and (core_name in unit_name or unit_name in core_name):
            return anchor_name, coords
    return None, None


def sanitize_init_units(init_state, available_nodes):
    """
    🌟 核心防线修补：在创世阶段就进行绝对物理清洗。

    正确语义：
      - 显式 node_name 优先；朝鲜战争地名参考不按单位名称自动吸附；
      - 未命中      → AI 自由坐标，海军单位必须过海陆雷达。
    """
    # 🐛 修复：AI 若用中文键名（"名称"/"类型"），锚点匹配和海军判定会全部失效，
    #    这里先归一化，保证下面的 lat/lng/type/name 读取口径统一。
    normalize_state_units(init_state)

    for team in UNIT_TEAMS:
        for unit in init_state.get(team, []):
            unit_type = get_unit_field(unit, 'type', '类型', '兵种', 'unit_type', 'class')
            unit_name = get_unit_field(
                unit, 'name', '名称', '单位名称', '部队名称', 'unit_name') or ''

            # 1. 先履行 prompt 的显式锚定契约，再兼容旧库名称匹配。
            requested = unit.get('node_name')
            if isinstance(requested, str) and requested in available_nodes:
                anchor_name, coords = requested, available_nodes[requested]
            else:
                anchor_name, coords = match_anchor(unit_name, available_nodes)
                if requested:
                    _notify("warning", f"未收录或不属当前战役的锚点：{requested}，保留自由坐标并执行常规检查")
            # 新库全为陆上参考，不能让舰艇借 node_name 绕过海陆检查。
            if anchor_name and node_metadata(anchor_name) and is_naval(unit_type):
                anchor_name, coords = None, None
            if anchor_name:
                unit['lat'] = coords[0]
                unit['lng'] = coords[1]
                _notify("toast", f"🎯 地理参考：[{unit_name}] 已按声明部署至 [{anchor_name}]", icon="⚓")
                continue

            # 2. 降级路径：AI 自由坐标，海军单位必须过一遍海陆雷达
            if not is_naval(unit_type):
                continue

            lat = unit.get('lat')
            lng = unit.get('lng')
            new_lat, new_lng = force_into_water(lat, lng)

            if (new_lat != lat) or (new_lng != lng):
                lat_f, lng_f = to_float(lat), to_float(lng)
                new_lat_f, new_lng_f = to_float(new_lat), to_float(new_lng)
                if None in (lat_f, lng_f, new_lat_f, new_lng_f):
                    offset_km = 0.0
                else:
                    # 计算拖拽的粗略直线距离 (1度约等于111公里)
                    offset_km = math.sqrt((new_lat_f - lat_f) ** 2 + (new_lng_f - lng_f) ** 2) * 111

                if offset_km <= 5:
                    # 🟢 5公里以内：属于战术微调，正常放行
                    _notify("toast", f"⚓ 战术微调：{unit_name} 偏航，已拖拽 {offset_km:.1f} 公里至港口/水域。", icon="🌊")
                elif offset_km <= 15:
                    # 🟡 5~15公里：大模型产生中度幻觉，提出警告
                    _notify("toast", f"⚠️ 战略警告：{unit_name} 偏航严重！系统强行跨越 {offset_km:.1f} 公里将其拖入海中，可能偏离原定战区！", icon="⚠️")
                else:
                    # 🔴 15公里以上（逼近22公里极限）：发生严重空间扭曲
                    _notify("error", f"🚨 极限地理穿透：{unit_name} 被 AI 投送至内陆深处！系统实施了 {offset_km:.1f} 公里的强行拖拽。战术意图已遭破坏，建议总参谋长重点核查该单位坐标！")

                unit['lat'] = new_lat
                unit['lng'] = new_lng


def sanitize_step_units(updated_data):
    """
    🌟🌟🌟 单回合推演后的强制洗数据（落水算法） 🌟🌟🌟
    仅对海军单位执行强制落水；非海军单位不受海陆雷达约束。
    """
    normalize_state_units(updated_data)

    for team in UNIT_TEAMS:
        for unit in updated_data.get(team, []):
            unit_type = get_unit_field(unit, 'type', '类型', '兵种', 'unit_type', 'class')
            if is_naval(unit_type):
                lat = unit.get('lat')
                lng = unit.get('lng')

                # 调用算法：如果在陆地上，强制获取最近的海水坐标
                new_lat, new_lng = force_into_water(lat, lng)

                # 如果坐标被算法纠正过，通过 Toast 提示用户
                if (new_lat != lat) or (new_lng != lng):
                    unit_name = get_unit_field(
                        unit, 'name', '名称', '单位名称', '部队名称', 'unit_name',
                        default='未命名单位')
                    _notify("toast", f"⚓ 系统强制干预：检测到 {unit_name} 偏航至陆地，已强制拖入附近海域！", icon="🌊")
                    unit['lat'] = new_lat
                    unit['lng'] = new_lng
