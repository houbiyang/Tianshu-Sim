"""引擎参数集中配置（原先散落在各处的硬编码统一到此）。"""

import math  # noqa: F401  # 供 geo 等模块间接使用，集中 import 便于管理

# ==========================================
# ⚙️ DeepSeek 接入配置
# ==========================================
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_ENGINE = "deepseek-v4-flash"      # 引擎 A：快速 JSON 战局推演
MODEL_STRATEGIST = "deepseek-v4-pro"    # 引擎 B：深度战略复盘
MODEL_CHAT = "deepseek-reasoner"        # 侧栏璇玑实时问答
MAX_API_RETRIES = 3                     # 坐标修正反思循环上限
MAX_AGENT_STEPS = 4                     # 智能体回合：每回合至多几次模型往返（默认关，时延×N）
MAX_TOOL_CALLS_PER_STEP = 4             # 智能体回合：单步至多几条领域工具调用

# 🛡️ 模型回退链：若 MODEL_* 常量在你的账号下不存在（API 返回 400 model not exist），
#    系统会自动降级到下列稳定模型名，而不是让整条推演链直接崩掉。
MODEL_FALLBACKS = {
    MODEL_ENGINE: ["deepseek-chat"],
    MODEL_STRATEGIST: ["deepseek-reasoner", "deepseek-chat"],
    MODEL_CHAT: ["deepseek-chat"],
}


# ==========================================
# 🧰 通用工具
# ==========================================
def to_float(value):
    """安全数值转换：失败返回 None，避免坐标异常把整次推演拖崩。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 海军单位判定关键词（原先在 3 处重复实现，口径易漂移）
# 「海军」「水军」必须显式在列：模型最常写的兵种标签就是这两个词，
# 只靠「舰/船」子串会把 type="海军" 的单位整个漏出海陆审计（历史缺陷）。
NAVAL_KEYWORDS = ['舰', '船', '航母', '潜艇', '海军', '水军', 'ship', 'carrier',
                  'submarine', 'cruiser', 'destroyer', 'fleet']


def is_naval(unit_type):
    """统一的「是否海军单位」判定。"""
    return any(k in str(unit_type).lower() for k in NAVAL_KEYWORDS)
