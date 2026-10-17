"""🗃️ Session State 默认值与单位字段提取助手。

原先在 Gentactix.py 内散落了约 6 处 `unit.get('type', unit.get('类型', ...))` 式的字段提取链，
现统一为 get_unit_field / extract_lat_lng，单点维护、口径一致。
"""

import json

import streamlit as st

from .config import to_float

# --- 初始化 Session State ---
# 🐛 修复：原代码缺少 chat_messages 初始化，一旦战局生成后切到「参谋长复盘」页
#         就会抛 AttributeError: st.session_state has no attribute 'chat_messages'
DEFAULT_SESSION_STATE = {
    "war_state": None,
    "last_war_state": None,
    "history": [],
    "turn_count": 0,
    "review_text": None,
    "review_reasoning": None,
    "chat_messages": [],   # 璇玑对话记录
}


def ensure_session_state():
    """幂等初始化所有 Session State 键，避免后续访问 AttributeError。"""
    for _key, _default in DEFAULT_SESSION_STATE.items():
        if _key not in st.session_state:
            st.session_state[_key] = _default


# ==========================================
# 🔤 单位字段名归一化（中文键 → 标准英文键）
# ==========================================
# 🐛 修复「地图上所有单位都显示为『未知单位』」：
#    Init Prompt 里原本用中文描述单位属性（"必须包含：名称、类型、经纬度、HP、战斗力…"），
#    只有坐标被强制要求用 "lat"/"lng"，其余字段没规定键名。
#    AI 于是很自然地输出 {"名称": "...", "类型": "步兵", ...}，
#    而渲染层只认 unit.get('name') ⇒ 铭牌全部回退成 "未知单位"；
#    同时 '类型' 恰好被 get_unit_field 的别名链兜住，所以符号还能显示（步兵 X），
#    表现为「名字全丢、军标还在」这种极具迷惑性的半坏状态。
#    现在在鉴权入口统一归一化，下游（渲染 / 海陆判定 / 锚点吸附 / 复盘）只认英文标准键。
UNIT_TEAM_KEYS = ('red_units', 'blue_units')

UNIT_KEY_ALIASES = {
    'name': ('名称', '单位名称', '部队名称', '单位', '部队', 'unit_name'),
    'type': ('类型', '兵种', '兵种类型', '单位类型', '类别', 'unit_type'),
    'hp': ('HP', 'hp值', '兵力', '生命值', '血量', '耐久', '存活兵力'),
    'combat_power': ('战斗力', '战力', '攻击力', '攻击强度', 'power', 'attack'),
    'speed': ('移动速度', '速度', '行进速度', 'mobility'),
    'attack_range': ('射程', '攻击范围', '打击范围', 'range'),
    'morale': ('士气', '士气值'),
    'supply': ('补给', '补给值', '后勤', '后勤补给'),
    'lat': ('纬度', 'latitude', 'current_lat'),
    'lng': ('经度', 'longitude', 'current_lng'),
}

# 🐛 修复「交战永远画不出来 / 地图上连火图标都没有」：
#    上一轮只归一化了单位，**engagements 里的对象没归一化**，
#    而 viz.py 读交战严格只认 lat / lng / attacker_pos / description。
#    AI 一旦把交战写成 {"纬度":…, "经度":…, "描述":…}（和当初单位键漂移同一个毛病），
#    to_float(battle.get('lat')) 拿到 None ⇒ 直接 continue 静默丢弃，
#    结果是既不报错、也没有任何图形 —— 用户看到的就是「这个功能没生效」。
ENGAGEMENT_KEY_ALIASES = {
    'lat': ('纬度', 'latitude', 'battle_lat', 'target_lat'),
    'lng': ('经度', 'longitude', 'battle_lng', 'target_lng'),
    'description': ('描述', '交战描述', '战况', '说明', 'desc', 'detail'),
    'attacker': ('攻击者', '攻击方', '发起方', 'attacker_name', 'from'),
    'attacker_pos': ('攻击者位置', '攻击方坐标', '攻击者坐标', 'attacker_position', 'from_pos'),
    'radius': ('波及半径', '影响半径', '范围', 'impact_radius'),
    'intensity': ('强度', '烈度', 'intensity_level'),
}


def normalize_engagement_keys(battle):
    """把交战对象里的中文/别名键补成标准英文键（幂等，不覆盖已有英文值）。"""
    if not isinstance(battle, dict):
        return battle
    for std_key, aliases in ENGAGEMENT_KEY_ALIASES.items():
        if battle.get(std_key) not in (None, ''):
            continue
        for alias in aliases:
            if alias in battle and battle[alias] not in (None, ''):
                battle[std_key] = battle[alias]
                break

    # 攻击方坐标可能被写成 {"lat":…, "lng":…} 或 "35.1,112.9" 字符串，统一成 [lat, lng]
    pos = battle.get('attacker_pos')
    if isinstance(pos, dict):
        p_lat = to_float(pos.get('lat', pos.get('纬度')))
        p_lng = to_float(pos.get('lng', pos.get('经度')))
        if p_lat is not None and p_lng is not None:
            battle['attacker_pos'] = [p_lat, p_lng]
    elif isinstance(pos, str):
        parts = [p.strip() for p in pos.replace('，', ',').split(',')]
        if len(parts) == 2:
            p_lat, p_lng = to_float(parts[0]), to_float(parts[1])
            if p_lat is not None and p_lng is not None:
                battle['attacker_pos'] = [p_lat, p_lng]
    return battle


def normalize_unit_keys(unit):
    """把单位字典里的中文/别名键补成标准英文键（幂等，且不覆盖已有英文值）。

    只做「补缺失键」，不删除原始键 —— 导出的 JSON 仍保留 AI 原始字段，
    便于排查 AI 又发明了什么新键名。
    """
    if not isinstance(unit, dict):
        return unit
    for std_key, aliases in UNIT_KEY_ALIASES.items():
        if unit.get(std_key) not in (None, ''):
            continue
        for alias in aliases:
            if alias in unit and unit[alias] not in (None, ''):
                unit[std_key] = unit[alias]
                break
    return unit


def normalize_state_units(war_state):
    """对一整份战局数据（含 updated_state 嵌套层）里的所有单位和交战做键名归一化。"""
    if not isinstance(war_state, dict):
        return war_state
    targets = [war_state]
    inner = war_state.get('updated_state')
    if isinstance(inner, dict):
        targets.append(inner)
    for container in targets:
        for team in UNIT_TEAM_KEYS:
            units = container.get(team)
            if isinstance(units, list):
                for unit in units:
                    normalize_unit_keys(unit)
        # 🐛 交战对象同样需要归一化：漏这一步会让整场交火静默消失（详见别名表注释）
        engagements = container.get('engagements')
        if isinstance(engagements, list):
            for battle in engagements:
                normalize_engagement_keys(battle)
    return war_state


def get_unit_field(unit, *keys, default=''):
    """从单位字典中按候选键名顺序取出第一个**存在且非 None** 的字段值。

    取代原先各处重复的 `unit.get('type', unit.get('类型', unit.get('unit_type', ...)))` 链，
    统一口径，避免某处漏改导致字段读取不一致。
    """
    for k in keys:
        if k in unit and unit[k] is not None:
            return unit[k]
    return default


def extract_lat_lng(unit):
    """统一提取单位的经纬度（覆盖全应用出现过的所有别名键），非数值返回 (None, None)。"""
    lat = to_float(unit.get('lat', unit.get('current_lat', unit.get('latitude', unit.get('纬度')))))
    lng = to_float(unit.get('lng', unit.get('current_lng', unit.get('longitude', unit.get('经度')))))
    return lat, lng


def generate_json_export():
    """提取 Session State 并打包为完整的 JSON 数据。"""
    export_data = {
        "turn_count": st.session_state.turn_count,
        "war_state": st.session_state.war_state,
        "history_logs": st.session_state.history,
        "review": {
            "reasoning": st.session_state.review_reasoning,
            "conclusion": st.session_state.review_text
        },
        # 🐛 修复：原快照漏掉了璇玑对话记录，导出后无法完整复原战局
        "chat_messages": st.session_state.chat_messages,
    }
    # 使用 indent=4 进行格式化，ensure_ascii=False 确保中文不乱码
    return json.dumps(export_data, ensure_ascii=False, indent=4)
