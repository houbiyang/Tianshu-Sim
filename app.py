"""🛡️ GenTactix · 现实地图全自动战略推演系统（全 DeepSeek 版）· 入口编排层。

本文件只做「薄编排」：页面配置、侧栏输入、三个按钮回调（初始化 / 下一轮 / 参谋长复盘）、
璇玑问答、以及带会话级缓存的地图渲染。所有业务逻辑下沉到 gentactix/ 包各模块。
"""

import copy
from pathlib import Path

import streamlit as st
from streamlit_folium import st_folium

from gentactix import anchors, engine, geo, prompts, state, viz

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
# 🧠 逻辑处理
# ==========================================
if btn_init:
    if not deepseek_api_key or not scenario_input:
        st.warning("⚠️ 请先输入 API Key 和推演场景！")
    else:
        # 🌟 1. 战前情报检索：检查用户输入是否命中了我们的高精坐标库
        available_nodes = anchors.collect_available_nodes(scenario_input)

        # 🌟 2. 动态组装高精导航指令（由 prompts 内部完成）
        # 🌟 3. 将动态指令无缝嵌入 Prompt（见 prompts.build_init_prompt）

        with st.spinner("DeepSeek正在侦察地形并部署单位..."):
            try:
                # 1. 获取 AI 的初始阵型分布
                init_state = engine.get_ai_response_with_retry(
                    prompts.build_init_prompt(scenario_input, available_nodes),
                    deepseek_api_key,
                )

                # 2. 将清洗后的纯净数据写入系统状态（锚点吸附 + 海军强制落水）
                geo.sanitize_init_units(init_state, available_nodes)

                st.session_state.war_state = init_state
                st.session_state.last_war_state = None
                st.session_state.turn_count = 0
                # 🐛 修复：重新开新局时旧复盘与旧对话会残留，导致「张冠李戴」；
                #         新沙盘必须是一张白纸。
                st.session_state.review_text = None
                st.session_state.review_reasoning = None
                st.session_state.chat_messages = []

                start_time = st.session_state.war_state.get("current_time", "未知时间")
                initial_report = st.session_state.war_state.get("situation_report", "各单位已进入战斗位置，战役即将打响。")
                st.session_state.history = [f"**【T+0 | {start_time}】初始态势评估**：\n{initial_report}"]

            except Exception as e:
                st.error(f"初始化失败: {e}")

if btn_next:
    if not deepseek_api_key or not st.session_state.war_state:
        st.warning("⚠️ 请先初始化沙盘！")
    else:
        current_time_str = st.session_state.war_state.get('current_time', '00:00')
        next_turn_num = st.session_state.turn_count + 1

        with st.spinner(f"DeepSeek正在推演未来 {time_step} 的战况..."):
            try:
                result = engine.get_ai_response_with_retry(
                    prompts.build_step_prompt(st.session_state.war_state, next_turn_num, time_step, use_live_intel),
                    deepseek_api_key,
                )

                updated_data = result.get("updated_state", result)

                # 🌟🌟🌟 强制洗数据，执行“落水算法” 🌟🌟🌟
                geo.sanitize_step_units(updated_data)

                # 继续走原有的渲染和日志更新逻辑
                report_text = result.get("report")
                if not report_text and isinstance(updated_data, dict):
                    report_text = updated_data.get("report", f"【T+{next_turn_num}】系统未能成功生成文字战报，但战场态势已发生改变。")

                st.session_state.last_war_state = copy.deepcopy(st.session_state.war_state)
                st.session_state.war_state = updated_data

                if report_text:
                    st.session_state.history.insert(0, report_text)

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
        m = viz.build_tactical_map(
            st.session_state.war_state,
            st.session_state.last_war_state,
            st.session_state.turn_count,
        )

        # 渲染最终地图
        # 🐛 修复：补上固定 key，避免每次 rerun（点按钮/聊一句）都把地图缩放和中心重置
        # returned_objects=[]：本应用不消费地图的点击/缩放回传值，
        # 关掉可减少 payload，并避免组件回传默认值引发多余 rerun。
        st_folium(m, width=None, height=700, returned_objects=[], key="gentactix_tactical_map")

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
