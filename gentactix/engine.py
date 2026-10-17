"""🧠 DeepSeek 三脑调用层（推演引擎 A / 参谋长复盘 B / 璇玑问答 C）。

集中管理：API 客户端工厂、模型回退链、JSON 抽取、坐标反思循环、
复盘与实时问答。原先散落在 Gentactix.py 三处的 OpenAI 调用与 system prompt
拼装，统一收归此处，客户端按 api_key 缓存复用。
"""

import json

import streamlit as st
from openai import OpenAI

from .config import (
    DEEPSEEK_BASE_URL,
    MODEL_ENGINE,
    MODEL_STRATEGIST,
    MODEL_CHAT,
    MAX_API_RETRIES,
    MODEL_FALLBACKS,
)
from .geo import get_coordinate_errors

# 已确认失效的模型，后续直接走回退，避免每回合都撞一次 400
_MODEL_DEGRADED = set()


# ==========================================
# 🔌 客户端工厂（按 api_key 缓存复用）
# ==========================================
@st.cache_resource(show_spinner=False)
def get_client(api_key):
    """按 DeepSeek API Key 缓存 OpenAI 客户端，避免每次调用都重建。"""
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def _looks_like_model_error(exc):
    """区分「模型名不存在」与「网络/鉴权/配额」等不该回退的错误。"""
    text = str(exc).lower()
    return "model" in text and any(
        k in text for k in ("not exist", "does not exist", "invalid", "unknown", "not found"))


def chat_completion(client, model, messages, **kwargs):
    """统一模型调用入口：主模型失效时自动回退，保证推演链不因模型名写错而全挂。"""
    chain = [model] if model in _MODEL_DEGRADED else [model] + MODEL_FALLBACKS.get(model, [])
    last_exc = None
    for idx, m in enumerate(chain):
        try:
            resp = client.chat.completions.create(model=m, messages=messages, **kwargs)
            if idx > 0:
                _MODEL_DEGRADED.add(model)
                st.warning(f"⚠️ 模型 `{model}` 不可用，已自动回退至 `{m}`。"
                           f"建议在文件顶部配置区修正 MODEL_* 常量。")
            return resp
        except Exception as exc:
            last_exc = exc
            if not _looks_like_model_error(exc):
                raise      # 鉴权/网络/配额问题原样抛出，别用回退掩盖真实故障
    raise last_exc


# ==========================================
# 🧠 引擎 A：DeepSeek 动态推演 (负责快速生成 JSON 战局)
# ==========================================
def get_ai_response(prompt, api_key):
    """调用引擎 A 生成 JSON 战局，并做 markdown 代码块 / 裸文本兜底抽取。"""
    client = get_client(api_key)

    # 构建消息列表
    messages = [
        {"role": "system", "content": "你是一个高度严密的战棋推演引擎，你的任务是计算战损和坐标，并严格按照要求输出 JSON 格式。"},
        {"role": "user", "content": prompt}
    ]

    # 调用 DeepSeek V3 (deepseek-chat) 进行快速 JSON 生成
    response = chat_completion(
        client, MODEL_ENGINE, messages,
        # 强制开启 JSON Mode，提高格式稳定性
        response_format={"type": "json_object"},
    )

    raw_text = response.choices[0].message.content or ""

    # 提取 JSON 数据（包围在 markdown 代码块里的情况也一并处理）
    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.split("\n", 1)[-1].rsplit("```", 1)[0]

    start_idx = clean_text.find('{')
    end_idx = clean_text.rfind('}')

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(clean_text[start_idx:end_idx + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI 返回的 JSON 无法解析：{exc}\n原始回复：\n{raw_text}") from exc
    raise ValueError(f"未能提取到有效的 JSON。AI 原始回复：\n{raw_text}")


def get_ai_response_with_retry(prompt, api_key, max_retries=MAX_API_RETRIES):
    """带有反思循环的推演引擎，强制纠正 AI 坐标幻觉。"""
    current_prompt = prompt
    result_json = None

    for attempt in range(max_retries):
        result_json = get_ai_response(current_prompt, api_key)
        state_to_check = result_json.get("updated_state", result_json)
        errors = get_coordinate_errors(state_to_check)

        if not errors:
            if attempt > 0:
                st.toast("✅ DeepSeek 已成功自我修正坐标！", icon="🎉")
            return result_json

        st.toast(f"🛑 检测到航母上岸，DeepSeek 正在进行第 {attempt + 1} 次重新推理...", icon="🔄")

        error_feedback = "\n\n【系统最高警告：坐标物理规则冲突！】\n你刚才生成的 JSON 中存在以下致命地理错误：\n"
        for err in errors:
            error_feedback += f"- {err}\n"
        error_feedback += "请你立刻重新评估战局，重新生成一组合法的 JSON 数据。必须确保所有海军单位的经纬度都在海洋中！"

        current_prompt += error_feedback

    st.error(f"⚠️ AI 连续 {max_retries} 次未能修正坐标错误，系统强制放行，请注意地图穿模。")
    return result_json


# ==========================================
# 🧠 引擎 B：DeepSeek 战略复盘 (负责深度思考)
# ==========================================
def get_r1_strategic_review(history, current_state, api_key):
    """总参谋长璇玑：基于全量战报 + 当前状态输出深度复盘（含思维链）。"""
    client = get_client(api_key)

    prompt = f"""
    你现在是最高统帅部的首席战略推演官璇玑。
    以下是本次沙盘推演从开战至今的全部战报记录：
    {json.dumps(history, ensure_ascii=False, indent=2)}

    这是当前最新的战场单位状态（包含残余兵力、位置、士气、补给）：
    {json.dumps(current_state, ensure_ascii=False, indent=2)}

    请你抛开一切 JSON 格式束缚，用极其专业的军事术语，写一份 500 字左右的【战局深度复盘】。
    要求：
    1. 分析双方在刚才几次交锋中的战略得失（谁占了便宜，谁犯了致命错误）。
    2. 评估地形对当前局势的影响。
    3. 为处于劣势的一方提供下一步的“破局建议”，为处于优势的一方提供“稳固胜势建议”。
    4. 不仅要分析战术得失，还要说明当前的战术成果是否可以影响到战略层面的得失。
    """

    response = chat_completion(
        client, MODEL_STRATEGIST,
        [
            {"role": "system", "content": "你是一位冷酷、理性的军事战略大师。"},
            {"role": "user", "content": prompt}
        ]
    )

    # 🐛 修复：原代码直接访问 .reasoning_content，一旦所用模型不返回思维链
    #         （非 reasoner 系）就会抛 AttributeError，导致复盘整个失败。
    message = response.choices[0].message
    reasoning = getattr(message, 'reasoning_content', None) or ""
    return reasoning, message.content


# ==========================================
# 💬 引擎 C：璇玑实时问答（侧栏聊天）
# ==========================================
def ask_xuanji(history, war_state, api_key, chat_messages):
    """统帅向璇玑提问：注入战场上下文，返回 (reasoning, answer)。"""
    client = get_client(api_key)

    # 将战场环境动态注入到 System Prompt 中
    system_prompt = f"""
    你是最高统帅部的首席战略推演官璇玑。冷酷、理性，深谙军事战略。
    以下是开战至今的全部战报：{json.dumps(history, ensure_ascii=False)}
    这是当前最新沙盘状态：{json.dumps(war_state, ensure_ascii=False)}
    请直接用专业的中文军事术语回答统帅的问题，不要输出 JSON，不要有多余寒暄。
    """

    messages = [{"role": "system", "content": system_prompt}]
    # 追加最近的对话历史（保持记忆，最多回带 5 轮，避免上下文无限膨胀）
    for m in chat_messages[-5:]:
        messages.append({"role": m["role"], "content": m.get("content") or ""})

    # 调用 DeepSeek R1 思考模型
    response = chat_completion(client, MODEL_CHAT, messages)

    # 提取思维链 (CoT) 与最终回复
    reasoning = getattr(response.choices[0].message, 'reasoning_content', '') or ''
    answer = response.choices[0].message.content or "（璇玑未返回内容，请重试）"
    return reasoning, answer
