"""📜 推演 Prompt 模板。

将原先内联在按钮回调里的两段巨型 f-string 抽离为纯函数，文案与原始版本保持一致，
仅做结构化封装，便于单测与复用。动态锚点提示由 available_nodes 驱动。
"""

import json
from . import sim_clock, reporting
from .unit_types import modern_unit_instruction
from .battlefield import DISPLAY_INSTRUCTION
from .anchors import node_metadata


# ==========================================
# 📖 战报写作规范（讲解体）
# ==========================================
# 为什么单独抽出来：接入史料检索后，模型把「出处、争议、未载」这些**考据信息**
# 全塞进了正文，于是战报读起来像文献综述而不是战场讲解 —— 「赵军约四十万系
# 《史记》所载数字、学界有争议；秦军总数《史记》未载……」这种句子，信息是对的，
# 但它回答的是「史料怎么记载」，而读者想知道的是「现在战场上怎么样了」。
#
# 根因是三个字段挤在一起：态势、考据、时间格式。修法是**拆字段**而不是换形容词 ——
# 正文只讲态势，考据搬到 source_note，时间保持简短。
REPORT_STYLE_RULES = """
        【战报写作规范 · 必须遵守】正文与考据注是两件事，不许混写。

        ▸ situation_report / report ＝ 讲解，像参谋站在地图前指着战场口述
          - 讲清三件事：两军各自在哪、正在做什么；谁占优、凭什么；接下来最可能怎样。
          - 要把因果讲出来，不要只罗列状态。读者看完要能明白「他为什么这么打」。
          - 可以有画面感（晨雾、河岸、旌旗、泥泞），但不要堆文言辞藻。
          - 只保留理解局势必需的一两个数字；出处、争议、未载一律不进正文。
          - 正文严禁出现这些考据用语：「史料未载」「学界有争议」「据《史记》载」
            「系……所载」「可考」「存疑」「未详」，也不许用书名号引用文献。
          - 公文与作文腔一律不要：不写「本章」「综上所述」，不写小标题，不分点。
          - 篇幅：150—200 字。宁可讲透一件事，不要面面俱到。

          ✗ 反例（考据腔，严禁这样写）：
            「赵括已代廉颇，悉更约束、易置军吏。赵军降卒约四十万系《史记》所载数字、
              学界有争议；秦军总数《史记》未载，仅记奇兵二万五千、骑五千。」
          ✓ 正例（讲解腔）：
            「赵括一到任就换掉了廉颇的整套打法，把沿河死守的军令全部作废，催着全军渡河出击。
              秦军等的正是这一刻：主力早就藏进丹河东岸的丘陵，只留小股骑兵在前沿且战且退，
              要把赵军引过河再收口。赵军一旦全军渡河，背后的粮道就成了整场仗最脆弱的地方。」

        ▸ source_note ＝ 考据注，一句话交代数字从哪来、哪里不确定
          - 只写来源与不确定性，不复述正文已经讲过的态势。
          - 有史料时：交代关键数字的出处与争议，例如「兵力数字据《史记·白起王翦列传》，
            学界对四十万之数有争议；秦军总数史书未载」。
          - 无史料时：写「本回合无史料支撑，态势系依军事常识推演」。
          - 30—60 字，不分点，不展开论述。
        """ + reporting.INSTRUCTION

TIME_FORMAT_RULE = (
    "\n        ⏱️ current_time 必须是**简短的时间表述**（8—18 字），"
    "现代已知日期必须使用YYYY-MM-DD HH:MM格式，例如1952-10-14 04:00。\n"
    "        严禁把战局背景、兵力、考据括注写进这个字段——它只是时间，"
    "写成一句话会让整个战报标题变成一段考据。\n"
    "        不确定具体日期就用「某日」「某年」这类模糊表述；"
    "史料未载的说明写进 source_note，不要塞进这里。"
)


def build_node_instruction(available_nodes):
    """给模型实际坐标、来源与精度边界，而非仅给地名。"""
    if available_nodes:
        rows = []
        for name, coords in available_nodes.items():
            meta = node_metadata(name)
            rows.append({"node_name": name, "lat": coords[0], "lng": coords[1],
                         **({"kind": meta["kind"],
                             "uncertainty_km": meta["uncertainty_km"],
                             "source": meta["source_url"], "note": meta["note"]}
                            if meta else {})})
        nodes_json = json.dumps(rows, ensure_ascii=False)
        return f"""
        5. ⚓ 【地理参考锚点】：下列为当前战役可用的经纬度参考（lat=纬度，lng=经度）：
           {nodes_json}
           - 先用参考点确定真实战区与center；按用户指定阶段和战线分别部署，第二次战役东西两线不可混为一处。
           - 仅当单位确实驻于该点时，在单位 JSON 中填写完全一致的 "node_name"，系统会据此吸附坐标。
           - 进攻、途经或支援某点不等于已占领该点；这些单位用参考点周边合理的独立 lat/lng，不填写node_name。
           - 地名中心不代表战时阵地；uncertainty_km是工作不确定范围，不是测绘保证。不得把所有部队堆在参考点上。
           - 未收录的部署位置仍须有合理历史依据，不能据这些参考点虚构坑道、补给站、道路或DEM地形。
            """
    return "5. 【通用地理生成】：请依靠你的历史地理知识，自行生成合理的 lat 和 lng 坐标。"


def build_init_prompt(scenario_input, available_nodes, briefing="", simulation_mode="open"):
    """🚀 初始化沙盘：返回战前部署 Prompt（含动态锚点指令与可选史料摘录）。"""
    node_instruction = build_node_instruction(available_nodes)

    # 📚 史料注入：只有真的检索到内容时才出现，避免给模型一个空的「史料库」暗示
    briefing_block = f"\n{briefing}\n" if briefing else ""
    briefing_rule = (
        "\n           - 📚 【史料优先】：上方史料若给出双方兵力数字，保留其统计范围与编制层级，不按可视单位个数摊派人数"
        "\n             （未知的局部人数保持未知，合成编组必须标注教学假设）。"
        "\n           - 📚 数字的出处与争议写进 source_note，**不要写进 situation_report 正文**。"
        "\n           - 📚 史料标注「未载」「有争议」的项，禁止补成确数。"
        if briefing else ""
    )

    return f"""
        你是一位顶级的军事战略分析师和历史学家。请针对以下背景进行推演初始化： '{scenario_input}'。
{briefing_block}
        1. 确定核心战区的真实经纬度。
        2. ⏱️ 【场景起始时间】：按用户指定的战役或子阶段设置起点；未给具体时刻时，可采用便于教学的工作时刻，并在source_note标注时刻假设。不得把1951—1953年场景倒退到1950年战争爆发。第0回合描述已有态势，不新增本回合交火；前期损耗和既有阵地必须保留。

        3. 🌟 【动态非对称兵力生成】：请严格依据真实历史背景中的【红蓝实力对比】，进行符合史实的兵力部署！
           - 数量分配：根据资料的编制层级和局部范围选择少量编组；不得为凑足12至24个单位虚构番号。单位个数不代表人数比例，人数须独立记录；未知人数明确标未知。{briefing_rule}
           - 质量代换：对于历史上【兵力较少，但单兵素质/装备更优】的一方，可在战斗力、morale上作有标注的教学假设；hp不是人员计数，禁止因装备优势增加人数。
           - 每个单位必须包含以下【英文键名】（键名严禁翻译成中文，字段值可以是中文）：
             "name"(名称/番号)、"type"(兵种类型)、"lat"(纬度)、"lng"(经度)、"hp"(兵力值)、
             "combat_power"(战斗力)、"speed"(移动速度)、"attack_range"(射程)、
             "morale"(士气0-100)、"supply"(补给0-100)
           - 单个单位的标准写法（严格照此结构输出，字段一个都不能少）：
             {{"name": "赵军前锋营", "type": "步兵", "lat": 35.71, "lng": 112.93, "hp": 1000,
               "combat_power": 70, "speed": 5, "attack_range": 200, "morale": 85, "supply": 90,
               "personnel": {{"committed": null, "current": null, "casualties": null, "noncombat_losses": null, "source": "缺少该单位人数资料"}}, "equipment": []}}
           - ⚠️ 最高级警告：键名绝对不允许写成 "名称"、"类型"、"兵力"、"战斗力" 这类中文键名，
             否则地图渲染层取不到单位名，所有军标都会退化成「未知单位」。键名只准用上面给出的英文。
           - ⚠️ 示例里的 lat/lng 只是**结构占位**，严禁照抄到实际单位上。每个单位的坐标必须
             各自按下方锚点或史实给出，否则十二支部队会叠在同一个点上，阵地纵深全部消失。
        4. ⚓ 【微观锚地与坐标授权】：
           - 如果推演的是港口遇袭战（如珍珠港事件），你**必须**将防守方舰队部署在真实的港口内部锚地（例如珍珠港福特岛战列舰大街，坐标范围大致在 [21.36, -157.96] 附近），**绝对禁止**为了规避地理限制而将舰队违背历史地放置在外海大洋上！
              - 其他海战场景也必须尽量贴近历史真实的海域位置，绝对禁止将舰队放在完全不合理的陆地坐标上（例如：将航母放在某个内陆城市的坐标上）。
        {node_instruction}
        5. 撰写一段【初始战场态势评估】。请注意，目前是战前部署阶段，请描述出"大战略下的平静"
           或"暗流涌动"，不要出现战斗已经打响的描述。
           - 正文写进 situation_report，考据写进 source_note，时间写进 current_time —— 三者不许混。
           - 正文按下方【战报写作规范】写，150—200 字。
        【绝对地理红线】：所有包含“舰”、“船”、“航母”、“潜艇”的海洋单位，经纬度必须位于水域中！
        6. {mode_instruction(simulation_mode)}
        【最高级 JSON 格式强制警告】：
        经纬度必须且只能使用 "lat" 和 "lng" 这两个独立的键名！
        绝对不允许擅自发明同义词或将其打包合并！
{REPORT_STYLE_RULES}{TIME_FORMAT_RULE}{modern_unit_instruction(scenario_input + briefing)}{DISPLAY_INSTRUCTION}
        请直接返回 JSON 数据，格式如下：
        {{
          "center": [lat, lng],
          "current_time": "设定的起始时间（简短，8—18 字）",
          "era": "时代背景",
          "red_units": [...],
          "blue_units": [...],
          "current_terrain": "当前核心战区的地形特征",
          "situation_report": "在这里填写初始态势评估（讲解体，150—200 字）...",
          "source_note": "在这里填写考据注（30—60 字，交代数字出处与争议；无史料则说明系推演）..."
        }}
        """


def mode_instruction(simulation_mode="open"):
    if simulation_mode == "replay":
        return "【历史回放】按有出处的事件顺序讲解，未载细节标明推演；回放不是决策效果或因果检验，不得按历史总伤亡机械扣减本回合人员。"
    return "【开放推演】只以起点已知条件和本回合决策计算后果，允许偏离历史结果；历史结局不是必须发生的事件。禁止因抵达某个史实日期强制位移、伤亡或胜负；不得使用未来战果作为决策依据。"


def build_step_prompt(war_state, next_turn_num, time_step, use_live_intel, briefing="",
                      adjudication=None, simulation_mode="open"):
    """⏩ 单回合推演：返回含史料锚定与物理约束的 Prompt。

    `adjudication` 为 adjudicator.adjudicate() 的结果（可选）。传入时把几何裁决
    作为**不可改写的硬边界**注入 —— 模型从「即兴推演」降级为「在边界内叙述」。
    不传则完全退回原来的叙事沙盘行为。
    """
    if use_live_intel:
        intel_instruction = "【突发变量接入（黑天鹅事件）】：请主动假设并引入一个符合当前时代的突发战场变量（如恶劣天气、后方破坏、第三方介入等），打破目前的战术僵局！"
    else:
        intel_instruction = "【封闭沙盘模式】：本回合纯粹依据现有的兵力、地形和物理规则进行硬核军事对抗，切勿凭空捏造外界干预。"

    briefing_block = f"\n{briefing}\n" if briefing else ""

    # ⚖️ 裁决块：放在史料之后、任务之前。位置有讲究 —— 放在任务后面，
    #    模型会当成「参考资料」；放在任务前面，才会被当成「前提条件」。
    adjudication_block = ""
    if adjudication and adjudication.get("digest"):
        adjudication_block = "\n" + adjudication["digest"] + "\n"

    if adjudication:
        physics_cmd = (
            "2. **物理边界（已由系统裁决，不可改写）**：各单位本回合的位移上限见上方"
            "【确定性裁决 · 机动上限】，你必须在该上限内安排走位；被判为「够不着」的单位"
            "本回合不得交战。\n"
            "           - 补给低于 20 机动减半；士气低于 30 战力下降。"
        )
    else:
        physics_cmd = (
            "2. **物理计算辅助**：在当前状态与回合时间约束下，结合各单位的"
            "【移动速度】和【射程】计算合理的走位微调。\n"
            "           - 补给低于 20 机动减半；士气低于 30 战力下降。"
        )

    # 🐛 原文写的是「立即检索你数据库中关于该历史事件的真实时间线」——
    #    项目并没有任何数据库或联网检索，这句话只是在诱导模型「编得像查过」。
    #    现在分两种措辞：有史料就要求逐条比对，没史料就要求对不确定处标注「存疑」。
    if simulation_mode != "replay":
        timeline_cmd = mode_instruction("open")
    elif briefing:
        # 注意：这里不能写「在战报中注明依据（如『据《史记》载……』）」——
        # 那与下方【战报写作规范】禁止考据用语直接冲突，模型会写成四不像。
        # 依据一律落到 source_note，正文只讲战场变化。
        timeline_cmd = (
            f"1. **精准时间推进与史料锚定（最高级命令）**：你必须将系统时间准确向后推进【{time_step}】。"
            "推进后，**逐条比对上方【史料摘录】中有明确记载的时间节点**：若新时间点已抵达其中某个事件，"
            "在讲解中说明相关历史事件与出处；本回合状态只按本次行动更新，历史累计战损不能直接写入单位账本。依据写进 source_note"
            "（正文只讲战场怎么变，不要在正文里引用书名）。"
            "史料未覆盖的时段按军事常识推演，但**禁止编造具体史实、禁止虚构出处或书名**。"
        )
    else:
        timeline_cmd = (
            f"1. **精准时间推进（最高级命令）**：你必须将系统时间准确向后推进【{time_step}】。"
            "本次**未提供史料摘录**，你只能依据自身历史知识推演：若新时间点对应你已知的标志性事件，"
            "可以在讲解中注明，但不得将未知战损补造为单位账；对没有把握的史实，需在 source_note 中说明"
            "「此项无出处、系推演」，不要写成确证，更不得虚构书名或章节。"
        )

    return f"""
        当前系统时间：{war_state.get('current_time', '00:00')}
        这是第{next_turn_num} 轮推演。当前回合流逝的时间跨度为：【{time_step}】。
{modern_unit_instruction(json.dumps(war_state, ensure_ascii=False) + briefing)}
{DISPLAY_INSTRUCTION}
        当前战场数据：{json.dumps(war_state, ensure_ascii=False)}
        updated_state内每个单位保留personnel与equipment；数值未知用null，已有记录必须按本回合结果更新，不得省略。
{briefing_block}{adjudication_block}
       ### 强制任务：
       {sim_clock.instruction(war_state, time_step)}
       {intel_instruction}
        {mode_instruction(simulation_mode)}
        {timeline_cmd}
        {physics_cmd}
           - 🌟 【绝对地理红线】：在计算位移时，必须确保所有海军单位的新坐标依然位于水域中！
           - 🌟 【锚地静默规则】：海军在港口等待且本轮没有机动命令时保持原位；开放推演不因历史上未出动而禁止合法机动。
        3. **战报输出**：编写一段 150—200 字的实时战报（讲解体），
           正文写进 report，考据写进 source_note，两者不许混写。写作要求见下方【战报写作规范】。

        【地形强制判定规则】：强制新增一个字段 "current_terrain"，从["深海", "近海海峡", "平原", "丘陵森林", "高山峻岭", "城市地带"]中选择。地形必须严格影响你的战术计算。

        【最高级 JSON 格式强制警告】：
        经纬度必须且只能使用 "lat" 和 "lng" 这两个独立的键名！
        单位对象的键名必须沿用初始化的英文键：name / type / lat / lng / hp / combat_power /
        speed / attack_range / morale / supply，严禁改成 "名称"、"类型"、"兵种" 等中文键；
        "name"、"type"、"lat"、"lng" 这四个字段一个都不能丢。

        engagements 里不要输出 radius 字段：波及半径由系统按攻击单位的 attack_range × 1.2 推导。
        你必须为每场交战给出 "attacker"（发起攻击的单位名称，必须与 red_units/blue_units 的 name 完全一致）
        与 "attacker_pos"（该单位坐标），否则系统只能按兵种估算，范围会失真。

        【engagements 硬性规则】
        1. 该键**每回合都必须出现**：本回合无接触就输出空数组 []，不要省略该键；
        2. 交战对象内部**同样只允许英文键**：lat / lng / description / attacker / attacker_pos，
           严禁 "纬度"、"经度"、"描述"、"攻击者" 这类中文键（系统会直接丢弃该条交战）；
        3. 只有本回合实际安排射程内的攻击并通过接触条件时才输出交战；3公里内靠近、追击或伏击准备本身不等于开火。
           确实尚未接触时，请在 report 里明确写出「双方仍在对峙/机动，尚未接战」。

        【单位名单守恒规则】（系统会逐回合核对，违反的部分会被自动回填或标记）
        1. 上一回合还活着的单位，本回合**一个都不许省略** —— 名字必须保留，坐标按本轮合法移动更新；未移动才原样带出。
           系统发现「既没阵亡、也没声明」的单位凭空消失，会按上一回合快照把它补回来，
           你这一回合对它做的任何安排都会失效。
        2. 确实被歼灭的单位：从 red_units / blue_units 里移除，**并把名字写进
           updated_state 内的 "eliminated" 数组**（写在 updated_state 外层也认，但别两处都写）。
           没有声明的移除一律视为漏返回，会被回填。
        3. 兵力只能减少。只有「援军抵达 / 就地整补」这类事件可以让 hp 增加，
           且必须在 report 里写明理由；否则会被标记为兵力异常。
        4. 单位已失去战斗力但仍保留在列表里时，把 hp 写成 0，不要留一个满编数字。
        5. 在顶层decisions数组逐单位记录本回合行动（包括待命与失能），不得限制条数或遗漏单位：team（red/blue）、unit_name（已有番号）、
           action（具体命令）、rationale（简短依据）、expected_effect（预期影响）。
           这是可公开核对的决策摘要，不是内部思维链。决策意图不等于已证明的因果关系；
           未能确认决策时返回空数组，不为已发生的战果编造意图或因果贡献百分比。
{REPORT_STYLE_RULES}{TIME_FORMAT_RULE}
        请直接返回以下 JSON 结构：
        {{
          "updated_state": {{
            "center": [lat, lng],
            "current_time": "推进后的新时间（简短，8—18 字）",
            "era": "时代背景",
            "red_units": [...],
            "blue_units": [...],
            "current_terrain": "当前地形",
            "engagements": [
               {{
                 "lat": 目标纬度,
                 "lng": 目标经度,
                 "description": "交战描述（讲解体，一句话说清谁打谁、打到什么程度）",
                 "attacker": "发起攻击的单位名称",
                 "attacker_pos": [攻击者lat, 攻击者lng]
               }}
            ],
            "eliminated": ["本回合被歼灭的单位名称；没有就给空数组 []"]
          }},
          "report": "必须以 '【T+{next_turn_num} | 新时间】' 开头。战报正文（讲解体，150—200 字）...",
          "decisions": [{{"team":"red","unit_name":"已有番号","action":"具体命令","rationale":"可核对的简短依据","expected_effect":"预期影响"}}],
          "source_note": "考据注（30—60 字，交代数字出处与争议；无史料则说明系推演）..."
        }}
        """
