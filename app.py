"""🛡️ GenTactix · 现实地图全自动战略推演系统（全 DeepSeek 版）· 入口编排层。

本文件只做「薄编排」：页面配置、侧栏输入、三个按钮回调（初始化 / 下一轮 / 参谋长复盘）、
璇玑问答、以及带会话级缓存的地图渲染。所有业务逻辑下沉到 gentactix/ 包各模块。

⚖️ 可选模块：确定性裁决内核（gentactix/adjudicator.py，侧栏开关，**默认关闭**）。
打开后，推演前先用几何算出「谁能走到哪、粮道通不通、退路有没有被断」，
再把这些结论作为不可改写的硬边界注入 prompt —— 模型从「即兴推演」
降级为「在边界内叙述」，同想定可复现。关闭时行为与纯叙事沙盘完全一致。
"""

import copy
from pathlib import Path

import streamlit as st
from streamlit_folium import st_folium

from gentactix import (adjudication_lib, adjudicator, anchors, engine, geo,
                       integrity, prompts, rag, state, viz)


def _safe_adjudicate(state_data, time_step, turn, params, phase):
    """裁决失败绝不静默：退回自由推演，但必须在界面上说清楚。

    这里刻意不吞异常 —— 静默降级会让用户以为「这局是可复现的」，而实际上
    模型已经在自由发挥。宁可多一行警告，也不要一个看起来正常的假象。
    """
    try:
        return adjudicator.adjudicate(state_data, time_step, turn=turn, params=params)
    except Exception as exc:  # noqa: BLE001
        st.warning(
            f"⚠️ 裁决内核在{phase}阶段失败（`{type(exc).__name__}: {exc}`），"
            "本回合退回自由推演 —— **结论不可复现**，请核对战局数据。"
        )
        return None


def _retrieve_briefing(query, enabled, top_k, max_chars, phase):
    """检索本地史料并返回 (briefing_text, meta)。

    三条降级路径（开关关闭 / 语料为空 / 零命中）都必须把原因写进 meta 并显示到界面上。
    这个功能最危险的失败方式不是报错，而是**静默退化**：用户以为有史料约束，
    实际模型在自由发挥，而战报看起来同样「言之凿凿」。
    """
    meta = {
        "phase": phase, "query": query, "enabled": bool(enabled),
        "hits": [], "injected": 0, "note": "",
    }
    if not enabled:
        meta["note"] = "史料检索未开启"
        return "", meta

    stats = rag.corpus_stats()
    if stats["chunks"] == 0:
        meta["note"] = f"史料库为空（{stats['dir']}），本次为模型自由发挥"
        return "", meta

    hits = rag.search(query, top_k=top_k)
    if not hits:
        meta["note"] = "未命中相关史料，本次为模型自由发挥"
        return "", meta

    text, used = rag.build_briefing(hits, max_chars=max_chars)
    meta["hits"] = [
        {
            "doc_title": hit["doc_title"],
            "section": hit["section"],
            "source": hit["source"],
            "score": hit["score"],
            "preview": hit["text"][:180] + ("…" if len(hit["text"]) > 180 else ""),
        }
        for hit in hits
    ]
    meta["injected"] = used
    if used < len(hits):
        meta["note"] = f"命中 {len(hits)} 段，受长度上限约束仅注入 {used} 段"
    return text, meta

# --- 页面配置（必须早于任何其他 Streamlit 调用）---
st.set_page_config(layout="wide", page_title="GenTactix AI 推演系统 (DeepSeek 驱动)")

# --- 初始化 Session State ---
state.ensure_session_state()

st.title("🛡️ GenTactix: 现实地图全自动战略推演 (全 DeepSeek 版)")

# --- 侧边栏：用户自定义输入 ---
st.sidebar.image(
    "https://assets.zyrosite.com/cdn-cgi/image/format=auto,w=768,fit=crop,q=95/1evUiS818YahKfZE/pythonlogo2-AfiMET3ydIQjjfId.png",
    width="stretch"
)

st.sidebar.title("Python历史战略实验室")
st.sidebar.markdown("以史为码，推演未来。www.pystrategylab.com")

with st.sidebar:
    st.header("系统设置")
    # 🌟 统一使用一个 DeepSeek API Key 即可驱动双脑
    deepseek_api_key = st.text_input("🧠 DeepSeek API Key :", type="password")

    st.header("高级功能")
    # DeepSeek 官方 API 暂无原生搜索工具，此处留作接口扩展提示
    use_live_intel = st.toggle("🌐 开启实时情报搜集", value=False, help="注：DeepSeek 原生不支持直接联网，目前此开关仅作为外部 API 预留。开启后 AI 将尝试模拟突发事件。")

    st.header("史料与情报")
    use_rag = st.toggle(
        "📚 启用史料检索（RAG）",
        value=False,
        help="从 corpus/ 目录的 Markdown 史料卡片中检索相关片段，注入推演提示词。"
             "纯本地 BM25：不联网、不调用额外 API、不产生费用。"
             "关闭时模型仅凭自身历史知识推演，数字与时间线不可追溯。",
    )
    _corpus = rag.corpus_stats()
    if not use_rag:
        st.caption("未开启：模型凭自身历史知识推演，**数字与时间线不可追溯**。")
    elif _corpus["chunks"] == 0:
        st.warning(
            f"⚠️ 史料库为空（`{_corpus['dir']}`）。本次推演仍由模型自由发挥，"
            f"**没有史料约束**。把 `.md` 文件放进该目录即可生效，格式见其中的 README.md。"
        )
    else:
        st.caption(
            f"已索引 **{_corpus['files']}** 个文件 / **{_corpus['chunks']}** 个片段"
            f"（{_corpus['chars']:,} 字）"
        )

    _rag_last = st.session_state.get("rag_last")
    if use_rag and _rag_last:
        _hits = _rag_last.get("hits") or []
        with st.expander(f"📚 最近一次注入（{_rag_last.get('injected', 0)}/{len(_hits)} 段）"):
            if _rag_last.get("note"):
                st.caption(_rag_last["note"])
            if not _hits:
                st.caption(f"查询：`{_rag_last.get('query', '')[:60]}`")
            for _hit in _hits:
                st.markdown(f"**{_hit['section']}**　`{_hit['source']}`")
                st.caption(_hit["preview"])
            st.divider()
            st.caption("若这里的内容与战报不符，说明模型没有遵守史料约束，应以史料为准。")

    st.header("裁决内核")
    # ⚖️ 默认关闭：这个开关会改变本项目的性质 —— 打开后模型从「叙事者」变成
    #    「在几何边界内叙述的人」，同想定可复现、可对照；代价是观感变「钝」：
    #    30 分钟跨度下步兵在山地只能走 0.9 km，多数回合根本没有接战。
    #    想要可对照的推演就打开，想读故事就关着 —— 默认保持原样，不替用户作主。
    use_adjudicator = st.toggle(
        "⚖️ 启用确定性裁决（几何层，默认关闭）",
        value=False,
        help="推演前先用几何算出：本回合各单位最远能走多远、粮道是否被敌方控制区截断、"
             "周围几个方向被封锁、退路是否还在。结论以硬约束形式注入模型，"
             "同一战局同一回合必然给出同一答案（附录指纹可核对）。"
             "关闭后模型完全自由发挥，两次运行结果不同 —— 这是默认行为。",
    )
    _battle_choice = None
    if use_adjudicator:
        st.caption(
            f"内核 `{adjudicator.ADJUDICATOR_VERSION}` · 只裁决**机动 / 补给 / 包围**，"
            "不裁决伤亡数字（那仍由模型叙述）。"
        )
        # 🎯 战役裁决库：一场战役一个文件（adjudication/<战役名>.md）。
        #    默认「自动匹配」按场景描述里的战役名命中；也可以显式指定 ——
        #    输入里同时提到两场仗（「如果长平之战按诺曼底的打法」）时，自动匹配会选错。
        _library = adjudication_lib.list_battles()
        if _library:
            _picked = st.selectbox(
                "🎯 战场裁决库",
                ["自动匹配（按场景描述）"] + _library,
                index=0,
                help="每场战役的几何尺度（多近算接触、包围圈多深）单独一份，"
                     "放在 adjudication/ 目录，加新战役不必改代码。"
                     "选「自动匹配」时场景描述里出现战役名即命中；"
                     "同时提到两场仗时请显式选一场。",
            )
            _battle_choice = None if _picked.startswith("自动匹配") else _picked
        else:
            st.caption("裁决库为空：`adjudication/` 下还没有战役文件，将使用默认几何。")
    else:
        st.caption("已关闭：模型自行推演走位与胜负，**同一想定两次运行结果不同**。")

    st.header("推演设定")
    scenario_input = st.text_area("输入历史事件或假设冲突：",
                                 placeholder="例如：1944年诺曼底登陆...",
                                 help="AI 会根据此描述初始化地图坐标和初始时间")

    time_step = st.select_slider(
        "⏱️ 单回合时间跨度",
        options=["5分钟", "15分钟", "30分钟", "1小时", "6小时", "12小时", "1天", "1周", "1个月"],
        value="30分钟",
        help="战术级交火建议选分钟，战略级宏观推演建议选天或月。"
    )

    btn_init = st.button("🚀 初始化沙盘 (DeepSeek)")
    btn_next = st.button("⏩ 下一轮推演 (DeepSeek)")
    st.divider()
    # 🐛 修复：该按钮此前被注释掉，但第 565 行仍在引用 btn_review，
    #         导致应用一启动就抛 NameError 整体白屏。此处重新启用。
    btn_review = st.button("🧐 呼叫总参谋长复盘 (璇玑)", type="primary")
    # ==========================================
    # 🌟 新增功能：一键导出推演数据快照 (JSON)
    # ==========================================
    st.divider()

    # 仅当存在推演历史时才显示导出按钮
    if st.session_state.history:
        json_string = state.generate_json_export()
        st.download_button(
            label="📥 一键导出战局快照 (JSON)",
            data=json_string,
            file_name=f"GenTactix_推演数据_T{st.session_state.turn_count}.json",
            mime="application/json",
            width="stretch",
            help="将当前所有的底层战场坐标、单位状态与复盘记录导出为结构化 JSON 文件"
        )
    else:
        st.button("📥 一键导出战局快照 (JSON)", disabled=True, width="stretch", help="请先进行沙盘推演再导出数据")

    # ==========================================
    # 🔧 渲染自检：不必等 AI 打出交战，一键验证扇区链路是否活着
    # ==========================================
    with st.expander("🔧 渲染自检"):
        st.toggle(
            "叠加 1 个示例交战扇区",
            key="selftest_sector",
            help="只在**地图渲染**时临时加一个虚构交战，不写入战局数据，关掉即消失。"
                 "用来区分「渲染失效」和「本回合确实没交战」。",
        )
        st.caption(f"渲染器版本：`{viz.RENDERER_VERSION}` · 交战图形：定向扇区；"
                   f"AI 未给攻击方位时降级为空心虚线环。")

# ==========================================
# 🧠 逻辑处理
# ==========================================
if btn_init:
    if not deepseek_api_key or not scenario_input:
        st.warning("⚠️ 请先输入 API Key 和推演场景！")
    else:
        # 🌟 1. 战前情报检索：检查用户输入是否命中了我们的高精坐标库
        available_nodes = anchors.collect_available_nodes(scenario_input)

        # 📚 2. 史料检索（可选）：初始化阶段把整张战役卡片尽量都取回来——
        #    兵力、时间线、地形、兵力争议都在不同的 `##` 段落里，只要 3 段会漏。
        briefing, rag_meta = _retrieve_briefing(
            scenario_input, use_rag, top_k=8, max_chars=3200, phase="init")
        st.session_state.rag_last = rag_meta

        with st.spinner("DeepSeek正在侦察地形并部署单位..."):
            try:
                # 1. 获取 AI 的初始阵型分布
                init_state = engine.get_ai_response_with_retry(
                    prompts.build_init_prompt(scenario_input, available_nodes, briefing),
                    deepseek_api_key,
                )

                # 2. 将清洗后的纯净数据写入系统状态（锚点吸附 + 海军强制落水）
                geo.sanitize_init_units(init_state, available_nodes)

                st.session_state.war_state = init_state
                st.session_state.last_war_state = None
                st.session_state.turn_count = 0
                # 🧱 新开一局：上一局的名单核对与数值标记必须清空，否则会张冠李戴
                st.session_state.roster_last = None
                st.session_state.numeric_flags = []
                # ⚖️ T+0 也裁一次：开局就能看到哪条粮道是脆的、哪支部队被半包围，
                #    不用等打起来。此时不注入 prompt，只用于面板显示。
                st.session_state.adj_violations = []
                # ⚖️ T+0 用的是**这场战役标定过**的尺度，不是硬编码默认值 ——
                #    否则换一场仗时，第一屏面板（最直观的那一屏）就已经算错了。
                if use_adjudicator:
                    st.session_state.adj_spec = adjudicator.load_battle_spec(
                        scenario_text=scenario_input, battle=_battle_choice)
                    st.session_state.adj_last = _safe_adjudicate(
                        init_state, time_step, 0,
                        dict(st.session_state.adj_spec.explicit), "初始化")
                else:
                    st.session_state.adj_spec = None
                    st.session_state.adj_last = None

                # 📚 保存场景原文：后续回合的史料检索要用「这一局是什么仗」，
                #    而不是用户此刻在输入框里改成的别的内容。
                st.session_state.scenario_input = scenario_input
                # 🐛 修复：重新开新局时旧复盘与旧对话会残留，导致「张冠李戴」；
                #         新沙盘必须是一张白纸。
                st.session_state.review_text = None
                st.session_state.review_reasoning = None
                st.session_state.chat_messages = []

                start_time = st.session_state.war_state.get("current_time", "未知时间")
                initial_report = st.session_state.war_state.get(
                    "situation_report", "各单位已进入战斗位置，战役即将打响。")
                # 📚 考据注单独成行、斜体压暗：正文负责讲战场，考据负责可核对。
                #    两者混在一段里，就是「赵军约四十万系《史记》所载数字、学界有争议」那种
                #    文献综述腔——信息没错，但读者想知道的是现在战场上怎么样了。
                initial_note = st.session_state.war_state.get("source_note")
                st.session_state.history = [state.compose_report_entry(
                    initial_report, initial_note,
                    prefix=f"**【T+0 | {start_time}】初始态势评估**：\n")]

            except Exception as e:
                st.error(f"初始化失败: {e}")

if btn_next:
    if not deepseek_api_key or not st.session_state.war_state:
        st.warning("⚠️ 请先初始化沙盘！")
    else:
        current_time_str = st.session_state.war_state.get('current_time', '00:00')
        next_turn_num = st.session_state.turn_count + 1

        # 📚 史料检索：单回合只取 4 段。查询拼上「哪场战役 + 当前时间 + 当前地形」，
        #    让排名偏向与本回合时段相关的小节，而不是每回合把整张卡片重复灌一遍。
        _query = " ".join(str(x) for x in (
            st.session_state.get("scenario_input", ""),
            current_time_str,
            st.session_state.war_state.get("current_terrain", ""),
        ) if x)
        briefing, rag_meta = _retrieve_briefing(
            _query, use_rag, top_k=4, max_chars=2200, phase="step")
        st.session_state.rag_last = rag_meta

        # ⚖️ 推演前先裁决。此刻 war_state 仍是上一回合末的真实态势，
        #    算出的机动上限就是本回合的物理边界；必须在调用模型之前算完，
        #    否则模型先动完再核算，裁决就变成了事后找补。
        _prev_state = st.session_state.war_state
        _adj_params = {}
        pre_adj = None
        if use_adjudicator:
            # 用「这一局是什么仗」（开局时存下的 scenario_input），而不是输入框
            # 此刻写着什么 —— 中途改输入框不该让已经开打的战役换一套几何尺度。
            st.session_state.adj_spec = adjudicator.load_battle_spec(
                scenario_text=st.session_state.get("scenario_input", ""),
                battle=_battle_choice)
            _adj_params = dict(st.session_state.adj_spec.explicit)
            pre_adj = _safe_adjudicate(
                _prev_state, time_step, next_turn_num, _adj_params, "推演前")

        with st.spinner(f"DeepSeek正在推演未来 {time_step} 的战况..."):
            try:
                result = engine.get_ai_response_with_retry(
                    prompts.build_step_prompt(
                        st.session_state.war_state, next_turn_num, time_step,
                        use_live_intel, briefing, adjudication=pre_adj),
                    deepseek_api_key,
                )

                updated_data = result.get("updated_state", result)

                # 🌟🌟🌟 强制洗数据，执行“落水算法” 🌟🌟🌟
                geo.sanitize_step_units(updated_data)

                # 🧱 名单守恒：`war_state = updated_data` 是整体替换，模型漏返回一个单位，
                #    下一回合的 prompt 里就没有这个名字 —— 它永远补不回来。
                #    所以必须在赋值之前核对，无理由消失的按上一回合快照回填。
                #    eliminated 可能被模型写在 updated_state 外层，两层都认。
                if isinstance(updated_data, dict) and isinstance(result, dict):
                    _declared = result.get("eliminated")
                    if isinstance(_declared, list) and _declared \
                            and not updated_data.get("eliminated"):
                        updated_data["eliminated"] = _declared
                updated_data, _roster = integrity.reconcile_roster(_prev_state, updated_data)
                st.session_state.roster_last = _roster
                st.session_state.numeric_flags = integrity.check_numeric_integrity(
                    _prev_state, updated_data)

                # 继续走原有的渲染和日志更新逻辑
                report_text = result.get("report")
                if not report_text and isinstance(updated_data, dict):
                    report_text = updated_data.get("report", f"【T+{next_turn_num}】系统未能成功生成文字战报，但战场态势已发生改变。")

                st.session_state.last_war_state = copy.deepcopy(_prev_state)
                st.session_state.war_state = updated_data

                # ⚖️ 事后裁决 + 位移越界检查。注意 check_movement 只**报告**，
                #    不把坐标拉回来 —— 硬拉可能把部队拖进河里，反而制造更难解释的错误。
                if use_adjudicator:
                    st.session_state.adj_last = _safe_adjudicate(
                        updated_data, time_step, next_turn_num, _adj_params, "推演后")
                    st.session_state.adj_violations = adjudicator.check_movement(
                        _prev_state, updated_data, pre_adj)
                else:
                    st.session_state.adj_last = None
                    st.session_state.adj_violations = []

                if report_text:
                    # 📚 考据注拼在正文之后、斜体压暗，与 T+0 的呈现保持一致
                    _note = result.get("source_note")
                    if not _note and isinstance(updated_data, dict):
                        _note = updated_data.get("source_note")
                    st.session_state.history.insert(
                        0, state.compose_report_entry(report_text, _note))

                st.session_state.turn_count = next_turn_num

            except Exception as e:
                st.error(f"❌ 本回合推演因为 AI 生成数据错误而终止，请重试。错误详情: {e}")

# ==========================================
# 🦅 参谋长复盘区
# ==========================================
if btn_review:
    if not deepseek_api_key:
        st.error("⚠️ 请在左侧输入 DeepSeek API Key！")
    elif not st.session_state.war_state or len(st.session_state.history) == 0:
        st.warning("⚠️ 请先进行至少一轮推演，再呼叫总参谋长复盘！")
    else:
        with st.spinner("🧠 参谋长正在盯着沙盘进行深度脑力推演 (V4模型需思考，请耐心等待)..."):
            try:
                # 获取报告并直接存入 session_state
                reasoning, conclusion = engine.get_r1_strategic_review(
                    st.session_state.history,
                    st.session_state.war_state,
                    deepseek_api_key
                )
                st.session_state.review_reasoning = reasoning
                st.session_state.review_text = conclusion

            except Exception as e:
                st.error(f"总参谋长复盘请求失败: {e}")

# ==========================================
# 🗺️ 地图展示端
# ==========================================
col1, col2 = st.columns([3, 1])

with col1:
    if st.session_state.war_state:
        current_time_display = st.session_state.war_state.get('current_time', '')
        st.markdown(f"### 🕒 当前战区时间：**{current_time_display}** (第 {st.session_state.turn_count} 回合)")

        # 🐛 修复（地图一直闪烁 / 画面反复重置）：
        #    曾在此处做「会话级地图对象缓存」，把同一个 folium.Map 对象存进 session_state 复用。
        #    但 streamlit_folium 内部会用 _get_map_string() **改动传入的 folium 结构**
        #    （实测同一个对象连续渲染，JS 体长 12025 → 12244 → 12416 → 12588 持续增长），
        #    而组件 key = generate_js_hash(leaflet)：结构一变哈希就变 ⇒ Streamlit 视为
        #    **全新组件** ⇒ iframe 整个重建（瓦片重新拉取、缩放/中心复位），组件 mount 又回传
        #    初始值触发 rerun，于是形成自我维持的死循环 —— 表现为地图一直闪烁。
        #    实测「每次重建一个新对象」哈希恒定（614aed89...），组件不会重挂载，故改为直接重建。
        #    代价：每回合多花几毫秒构建 folium 对象，远小于一次 iframe 重载。
        # 🔧 渲染自检：开启时**只在渲染层**追加一个虚构交战（副本，不改战局）
        _map_state = st.session_state.war_state
        if st.session_state.get("selftest_sector"):
            _map_state = viz.with_demo_engagement(_map_state)

        m = viz.build_tactical_map(
            _map_state,
            st.session_state.last_war_state,
            st.session_state.turn_count,
        )

        # 渲染最终地图
        # 🐛 修复：补上固定 key，避免每次 rerun（点按钮/聊一句）都把地图缩放和中心重置
        # returned_objects=[]：本应用不消费地图的点击/缩放回传值，
        # 关掉可减少 payload，并避免组件回传默认值引发多余 rerun。
        st_folium(m, width=None, height=700, returned_objects=[], key="gentactix_tactical_map")

        # 🐛 修复「推演过程中从没看到交战扇区」：
        #    原实现遇到非法/中文键的交战会静默 continue，界面上既不报错也无提示，
        #    用户无法区分「代码没生效」与「本回合确实没打起来」。这里把数据实况写出来。
        _eng = viz.summarize_engagements(st.session_state.war_state)
        if _eng['total'] == 0:
            st.caption("⚔️ 本回合交战：**0 处** —— AI 未输出 `engagements`（双方尚未接触），"
                       f"所以地图上没有扇区可画，**不是渲染失效**。可展开侧栏「🔧 渲染自检」一键验证"
                       f"（当前渲染器 `{viz.RENDERER_VERSION}`）。")
        else:
            _parts = [f"定向扇区 {_eng['sector']} 处"]
            if _eng['ring']:
                _parts.append(f"降级空心环 {_eng['ring']} 处（AI 未给攻击方位）")
            _note = ""
            if _eng['skipped']:
                _unique = "、".join(dict.fromkeys(_eng['reasons']))
                _note = f" · ⚠️ 另有 {_eng['skipped']} 处被丢弃：{_unique}"
            st.caption(f"⚔️ 本回合交战：**{_eng['valid']} 处**（{'；'.join(_parts)}）{_note}")

        # 📚 史料约束状态：让看地图的人一眼知道「这局的数字可不可追溯」。
        #    开启 RAG 但没注入成功时，必须明说，否则战报看起来同样言之凿凿。
        _rag_meta = st.session_state.get("rag_last") or {}
        if _rag_meta.get("enabled"):
            if _rag_meta.get("injected"):
                _first = (_rag_meta.get("hits") or [{}])[0]
                st.caption(
                    f"📚 本回合注入史料 **{_rag_meta['injected']}** 段"
                    f" · 主要来源：{_first.get('doc_title', '')}·{_first.get('section', '')}"
                )
            else:
                st.caption(f"📚 本回合**无史料注入**：{_rag_meta.get('note', '')}"
                           f" —— 数字与时间线不可追溯，仅作推演参考。")

        # ==========================================
        # ⚖️ 裁决面板：把四个离散状态直接摆出来
        # ==========================================
        # 为什么放在地图正下方：战报是叙述，可以写得很漂亮；这四个字段是算出来的，
        # 读者想核对「战报有没有在编」时，眼睛往下扫一眼就有答案。
        _adj = st.session_state.get("adj_last")
        _spec = st.session_state.get("adj_spec")
        if use_adjudicator and _spec is None:
            _spec = adjudicator.load_battle_spec(
                scenario_text=st.session_state.get("scenario_input", ""),
                battle=_battle_choice)
            st.session_state.adj_spec = _spec
        # 🐛 兜底：从旧会话恢复、或中途打开开关时，adj_last 可能为空。
        #    这里当场补算一次，避免「开关开着却一块面板都没有」这种看起来像没生效的状态。
        #    补算必须用当前战役的尺度 —— 用默认值补算会得到一块和后续回合对不上的面板。
        if use_adjudicator and not _adj:
            _adj = _safe_adjudicate(
                st.session_state.war_state, time_step,
                st.session_state.turn_count,
                dict(_spec.explicit) if _spec else {}, "面板补算")
            st.session_state.adj_last = _adj
        if use_adjudicator and _adj:
            _s = _adj["summary"]
            st.markdown(
                f"#### ⚖️ 确定性裁决 `{_adj['version']}` · 指纹 `{_adj['fingerprint']}`")
            # 🎯 把「这局用的是哪套尺度」摆在结论上方：几何参数换一份，结论就全变了，
            #    读者核对结论前必须先知道它是在什么尺度下算出来的。
            if _spec is not None:
                st.caption("参数：" + _spec.summary_line())
                for _w in _spec.warnings:
                    st.caption(f"ℹ️ {_w}")
                if _spec.errors:
                    st.error("裁决参数有误（出错项已忽略，其余照常生效）："
                             + "；".join(_spec.errors))
            st.caption(
                f"单位 **{_s['units']}** · 本回合可接触 **{_s['can_contact']}** · "
                f"粮道被断 **{_s['supply_cut']}** · 被围 **{_s['encircled']}**"
                f"（半包围 {_s['partial']}）· 退路已断 **{_s['retreat_cut']}** · "
                f"士气崩溃 **{_s['morale_broken']}** / 动摇 {_s['morale_shaken']}"
            )
            # 全部够不着是最容易被误读成 bug 的状态：用户以为裁决器坏了，
            # 其实是「30 分钟回合 + 山地」下步兵只能走 0.9 km。这里把话说透。
            if _s["units"] >= 2 and _s["can_contact"] == 0:
                _reaches = [u["reach_km"] for u in _adj["units"]]
                st.caption(
                    f"ℹ️ 本回合没有任何单位能接战：跨度 `{_adj['time_step']}` 下，"
                    f"各单位最多走 **{min(_reaches):.1f}–{max(_reaches):.1f} km**"
                    f"（地形 ×{_adj['terrain_factor']:g}）。这是几何结论，不是渲染问题 ——"
                    "想看到接战，请调大时间跨度，或让部队初始距离更近。"
                )

            _panel = [
                {
                    "单位": r["name"],
                    "方": "红" if r["team"] == "red" else "蓝",
                    "兵种": r["type"] or "—",
                    "可机动(km)": r["reach_km"],
                    "最近敌军(km)": (
                        f"{r['nearest_enemy']} {r['nearest_enemy_km']:.1f}"
                        if r["nearest_enemy"] else "—"),
                    "补给": r["supply"],
                    "包围": r["encirclement"],
                    "退路": r["retreat"],
                    "士气": r["morale_state"],
                }
                for r in _adj["units"]
            ]
            if _panel:
                st.dataframe(_panel, hide_index=True, width="stretch")

            # 📐 尺度明细放在结论表**之后**：先看「这局算出了什么」，再看「按什么尺度算的」。
            #    放前面会把真正的结论挤到下面，而且会改变「第一张表」的顺序语义。
            if _spec is not None:
                with st.expander("📐 本局几何尺度明细"):
                    st.dataframe(adjudication_lib.param_table(_spec),
                                 hide_index=True, width="stretch")
                    if _spec.source is not None:
                        st.caption(f"来源：`{_spec.source}`")
                    else:
                        st.caption("来源：默认几何（`adjudication/` 下没有这场战役的文件）")

            # 🚨 位移越界：只报告，不修正。这条提示是「模型有没有守规矩」的直接证据。
            _violations = st.session_state.get("adj_violations") or []
            if _violations:
                _detail = "；".join(
                    f"**{v['name']}** 实走 {v['moved_km']:.1f} km（上限 {v['allowed_km']:.1f} km，"
                    f"超 {v['over_km']:.1f} km）" for v in _violations)
                st.warning(
                    f"⚠️ 上回合有 **{len(_violations)}** 个单位的位移超出裁决上限：{_detail}。"
                    "裁决器不修正坐标（硬拉可能把部队拖进河里），只如实记录。"
                )

            with st.expander("⚖️ 裁决口径（怎么算的）"):
                st.markdown(
                    f"""
- **机动上限** = 兵种战术速度 × 本回合时长 ÷ 地形系数（当前地形「{_adj['terrain'] or '未标注'}」×{_adj['terrain_factor']:g}）
- **区域控制圈** = max({_adj['contact_km']:g} km, 攻击射程 × 1.2) —— 与地图上扇区半径同一口径
- **补给源** = 己方最靠后的单位（离敌方重心最远者），粮道穿敌控区即判「已切断」
- **包围** = 以单位为中心 24 个方向，被敌方控制圈封锁 ≥85% 判「被围」，≥50% 判「半包围」
- **退路** = 背离敌军的 ±75° 扇区内，是否存在一条能跑出的通道
- **可复现** = 同一战局 + 同一回合（当前第 {_adj['turn']} 回合）+ 同一跨度，指纹必然相同
                    """
                )
                st.caption(
                    "不裁决的项目：伤亡数字、士气具体分值、天气、指挥失误。"
                    "这些留在叙事层，由模型叙述 —— 裁决器刻意只碰「位置、时间、连通性」"
                    "这三类可以复算的东西。"
                )

        # 🧱 名单与数值守恒：回合数越多越容易失真的两个地方。
        #    没有异常时保持安静 —— 每回合都弹一句「一切正常」只会让人把面板关掉。
        _roster = st.session_state.get("roster_last") or {}
        _nflags = st.session_state.get("numeric_flags") or []

        if _roster.get("recovered") or _roster.get("structure_missing"):
            _detail = "、".join(
                (f"{e['name']}（hp {e['hp']:g}）" if isinstance(e.get("hp"), (int, float))
                 else e["name"])
                for e in _roster.get("recovered", [])
            )
            _miss = "；".join(_roster.get("structure_missing", []))
            st.warning(
                f"🔁 上回合有 **{len(_roster.get('recovered', []))}** 个单位的返回里凭空消失，"
                f"已按上一回合快照回填：{_detail}。"
                + (f"\n\n结构层面：{_miss}。" if _miss else "")
                + "\n\n战局状态是**整体替换**的，漏返回的单位下一回合连名字都不会出现在 "
                  "prompt 里 —— 它永远补不回来，所以这里必须补。若这支部队确实已被歼灭，"
                  "应写进 `eliminated` 声明；没有声明的消失一律当作漏返回。"
            )

        if _roster.get("eliminated"):
            _names = "、".join(f"{e['name']}（{e['why']}）" for e in _roster["eliminated"])
            st.caption(f"☠️ 本回合确认歼灭 **{len(_roster['eliminated'])}** 个：{_names}")

        if _roster.get("suspected"):
            _names = "、".join(
                f"{e['name']}（上回合 hp {e['hp']:g}）" for e in _roster["suspected"])
            st.caption(
                f"☑️ 这些单位的消失已被回填，但上一回合 hp 已经很低，也可能确实被歼灭：{_names}。"
                "下一回合若确认歼灭，请让模型写进 `eliminated`。"
            )

        if _nflags:
            _lines = []
            for _f in _nflags:
                if _f["kind"] == "hp_gain":
                    _lines.append(
                        f"**{_f['name']}** hp {_f['prev']:g} → {_f['new']:g}"
                        f"（+{_f['delta']:g}）")
                elif _f["kind"] == "hp_zero_alive":
                    _lines.append(f"**{_f['name']}** hp 已归零但仍在编制里（地图会留一个满编军标）")
                elif _f["kind"] == "hp_negative":
                    _lines.append(f"**{_f['name']}** hp 为负（{_f['new']:g}）")
            st.warning(
                "📉 数值守恒提示：" + "；".join(_lines) + "。\n\n"
                "**没有自动改** —— 兵力上涨可能是援军抵达或就地整补（合法），"
                "也可能是模型漏减（误差）。系统不替你判断，只把可疑处摆出来。"
            )

    else:
        # --- 🌟 启动前的中心 Logo 欢迎界面 ---
        # 1. 使用换行符制造一些顶部空白，使其垂直方向大致居中
        st.markdown("<br><br><br>", unsafe_allow_html=True)

        # 2. 使用嵌套的 3 列布局，把图片夹在中间，实现完美水平居中
        spacer_left, img_col, spacer_right = st.columns([1, 2, 1])

        with img_col:
            try:
                # 🐛 修复：原用相对路径 "logo.png"，依赖启动时的 cwd；
                #         改为基于脚本自身所在目录定位，从任何位置启动都能加载。
                logo_path = Path(__file__).resolve().parent / "logo.png"
                if not logo_path.exists():
                    raise FileNotFoundError(str(logo_path))
                st.image(str(logo_path), width="stretch")
            except Exception:
                st.warning("⚠️ 待机 Logo 未找到，请确保已将图片重命名为 logo.png 并放入代码同级目录。")

            # 添加居中的待机文字提示
            st.markdown(
                "<h3 style='text-align: center; color: #888888; font-weight: normal;'>"
                "系统处于最高物理静默状态"
                "</h3>",
                unsafe_allow_html=True
            )
            st.markdown("<br>", unsafe_allow_html=True)
            # 保留原本的操作提示，并居中显示
            st.info("👈 统帅，请在左侧配置 DeepSeek API Key 与推演场景，并点击 [🚀 初始化沙盘] 唤醒系统。")

with col2:
    # 🌟 核心改造：使用 tabs 将右侧空间一分为二
    tab_log, tab_review = st.tabs(["📡 动态战报", "🦅 参谋长复盘"])

    with tab_log:
        with st.container(height=650):
            if not st.session_state.history:
                st.caption("尚无战报。请点击左侧「🚀 初始化沙盘」开始推演。")
            for log in st.session_state.history:
                st.markdown(f"**·** {log}")
                st.divider()

    with tab_review:
        # 🐛 修复：review_text / review_reasoning 此前「只计算、不展示」，
        #         首席参谋长那次 V4 调用等于白花；此处补上结果渲染。
        if st.session_state.review_text:
            st.markdown("#### 🦅 战局深度复盘")
            if st.session_state.review_reasoning:
                with st.expander("💡 参谋长的推演思维链 (CoT)"):
                    st.markdown(st.session_state.review_reasoning)
            st.markdown(st.session_state.review_text)
            st.divider()

        # 使用固定高度的容器展示对话记录
        chat_container = st.container(height=580)
        with chat_container:
            if not st.session_state.chat_messages:
                st.caption("沙盘启动后，可在此直接向总参谋长璇玑提问。")
            for msg in st.session_state.chat_messages:
                content = msg.get("content") or ""
                if not content:
                    continue
                with st.chat_message(msg.get("role", "assistant")):
                    st.markdown(content)

        # 底部聊天输入框
        if user_query := st.chat_input("向总参谋长璇玑提问（如：帮我评估当前优势在哪方？）"):
            if not deepseek_api_key:
                st.error("⚠️ 请在左侧输入 DeepSeek API Key！")
            elif not st.session_state.war_state:
                st.warning("⚠️ 请先进行至少一轮推演，让战局数据生成！")
            else:
                # 1. 界面立刻显示用户问题
                st.session_state.chat_messages.append({"role": "user", "content": user_query})
                with chat_container:
                    with st.chat_message("user"):
                        st.markdown(user_query)

                    # 2. 调用璇玑进行深度思考
                    with st.chat_message("assistant"):
                        with st.spinner("🧠 璇玑正在分析实时战报，请稍候..."):
                            try:
                                reasoning, answer = engine.ask_xuanji(
                                    st.session_state.history,
                                    st.session_state.war_state,
                                    deepseek_api_key,
                                    st.session_state.chat_messages,
                                )

                                # 如果有思考过程，用折叠面板展示
                                if reasoning:
                                    with st.expander("💡 璇玑的战术推演思维链 (CoT)"):
                                        st.markdown(reasoning)

                                # 展示并保存回复
                                st.markdown(answer)
                                st.session_state.chat_messages.append({"role": "assistant", "content": answer})
                                # 对话记录限长，防止长时间推演后 session 无限增长
                                if len(st.session_state.chat_messages) > 40:
                                    st.session_state.chat_messages = st.session_state.chat_messages[-40:]

                            except Exception as e:
                                st.error(f"⚠️ 指挥部通讯中断: {e}")
