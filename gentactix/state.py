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
