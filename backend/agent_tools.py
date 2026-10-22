"""🤖 领域工具协议（应用层）：智能体回合的侦察 / 检索 / 试算工具。

本模块同时提供原生Cordis工具的只读执行器与兼容JSON模式。
原生工具由plugins/dsh-gentactix注册，经本机操作上下文桥接调用本模块；
未选择原生模式时，保留**应用层 JSON 协议**：
模型每步回复恰为一个 JSON —— `{"tool_calls":[…]}`（至多 MAX_TOOL_CALLS_PER_STEP 条）
或 `{"action":"submit_orders","orders":{…}}`（终局）—— 由本模块执行，
结果回喂**同一角色会话**，模型据此继续。

执行器全部只读 / dry-run：`evaluate_action` 先 deepcopy 再挪单位，
真实战局绝不被工具改写；状态写入永远由后端提交管线完成
（名单守恒 → 数值守恒 → 事后裁决 → 位移审计 → 原子提交全程共用，
整体替换不变量不因智能体模式而破）。

时延上界：每回合至多 MAX_AGENT_STEPS 次模型往返，每次都是局域网 vLLM
的大生成（分钟级）⇒ 智能体开关默认关，前端 label 明确警示。
"""

from __future__ import annotations

import copy
import json

from gentactix import adjudicator, causal, geo, rag
from gentactix import state
from gentactix.config import MAX_AGENT_STEPS, MAX_TOOL_CALLS_PER_STEP, to_float

from . import harness_engine

# 观察视图的白名单字段：只暴露推演决策真正需要的量，
#  combat_power/speed 之类派生值交给模型自己会编，不如不给。
_OBSERVE_KEYS = (
    ("name", ("name", "单位名称", "unit_name")),
    ("type", ("type", "兵种", "类型", "unit_type")),
    ("hp", ("hp", "兵力", "兵力值")),
    ("morale", ("morale", "士气")),
    ("supply", ("supply", "补给")),
)

_LAT_KEYS = ("lat", "current_lat", "latitude", "纬度")
_LNG_KEYS = ("lng", "current_lng", "longitude", "经度")


def build_tool_protocol(max_steps: int = MAX_AGENT_STEPS) -> str:
    """协议说明拼在**用户消息**尾部：单进程只有一个 DSH_SYSTEM_PROMPT，
    按会话开关协议只能随任务消息走。"""
    return f"""

【智能体回合 · 领域工具协议】
你处于智能体回合：先侦察、再评估、后下令。每一步只回复一个 JSON 对象，二选一：
1) 查询战场（每步至多 {MAX_TOOL_CALLS_PER_STEP} 条工具调用）：
   {{"tool_calls":[{{"tool":"get_observation","args":{{"names":["单位名"]}}}},{{"tool":"retrieve_history","args":{{"query":"粮道"}}}}]}}
2) 提交命令（本回合终局，orders 与常规回合完全同构、updated_state 整体替换）：
   {{"action":"submit_orders","orders":{{"updated_state":{{…}},"report":"…","eliminated":[…],"source_note":"…"}}}}
可用工具（全部只读 / dry-run，不会改变战局）：
- get_observation args {{"names":["单位名"] 或 {{}}}}：单位白名单字段视图（name/type/hp/morale/supply/lat/lng）
- retrieve_history args {{"query":"关键词"}}：检索史料库（开启时）与本局战报
- evaluate_action args {{"moves":[{{"name":"单位名","lat":35.71,"lng":112.83}}]}}：dry-run 试算位移，返回坐标合法性与机动越界
- inspect_trajectory args {{"turn":1}}：查看已提交回合的决策轨迹与状态变化
- evaluate_counterfactual args {{"turn":1,"undo":[{{"team":"red","name":"单位名"}}]}}：固定其他终态，撤销位移并重算几何
只能调用上述工具；禁止调用 harness 内置工具（bash、str_replace_editor 等），按本协议输出 JSON 不属于工具调用。
至多 {max_steps} 步；步数用尽仍未提交命令，本回合判为失败。"""


def _unit_name(unit) -> str:
    return str(state.get_unit_field(unit, "name", "单位名称", "unit_name") or "")


def _iter_units(war_state):
    for team_key, team in (("red_units", "red"), ("blue_units", "blue")):
        for unit in (war_state or {}).get(team_key) or []:
            if isinstance(unit, dict):
                yield team, unit


# ==========================================
# 🔍 执行器（全部只读 / dry-run）
# ==========================================
def exec_get_observation(war_state, args) -> dict:
    names = None
    if isinstance(args, dict) and isinstance(args.get("names"), list) and args["names"]:
        names = {str(x) for x in args["names"]}
    units = []
    for team, unit in _iter_units(war_state):
        name = _unit_name(unit)
        if names and name not in names:
            continue
        lat, lng = state.extract_lat_lng(unit)
        row = {"team": team, "lat": lat, "lng": lng}
        for out_key, aliases in _OBSERVE_KEYS:
            row[out_key] = state.get_unit_field(unit, *aliases)
        units.append(row)
    if names and not units:
        return {"ok": False, "error": f"未找到指定单位：{sorted(names)}"}
    return {"ok": True, "units": units}


def exec_retrieve_history(session, args) -> dict:
    query = str((args or {}).get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    out = {"ok": True, "passages": [], "history_hits": []}
    if not session.get("use_rag"):
        # 与 _retrieve_briefing 同一可见性规则：检索没开就必须说出口，
        # 绝不能让「读起来像有出处」的即兴发挥冒充史料支撑。
        out["passages_note"] = "史料检索未开启"
    else:
        scoped_query = " ".join(filter(None, (session.get("scenario_input", ""), query)))
        hits = rag.search(scoped_query, top_k=4, battle=session.get("battle_choice"), phase='step',
                          simulation_mode=session.get('simulation_mode', 'open'))
        passages = rag.select_passages(hits, max_chars=2200)
        out["passages"] = [
            {"doc_title": h["doc_title"], "section": h["section"], "text": h["text"]}
            for h in passages
        ]
        if not hits:
            out["passages_note"] = "未命中相关史料"
    q = query.lower()
    out["history_hits"] = [str(h)[:400] for h in session.get("history", [])[:30]
                           if q in str(h).lower()][:5]
    if not out["passages"] and not out["history_hits"]:
        out["ok"] = False
        out["error"] = out.get("passages_note") or "未命中任何材料"
    return out


def _set_lat_lng(unit, lat, lng) -> None:
    """写回单位实际使用的坐标别名键（找不到就落标准键 lat/lng）。"""
    for key in _LAT_KEYS:
        if key in unit:
            unit[key] = lat
            break
    else:
        unit["lat"] = lat
    for key in _LNG_KEYS:
        if key in unit:
            unit[key] = lng
            break
    else:
        unit["lng"] = lng


def exec_evaluate_action(prev_state, args, pre_adj=None) -> dict:
    moves = (args or {}).get("moves")
    if not isinstance(moves, list) or not moves:
        return {"ok": False, "error": "moves 必须是非空列表：[{{name, lat, lng}}, …]"}
    candidate = copy.deepcopy(prev_state)   # dry-run：真实战局绝不被改写
    index: dict[str, dict] = {}
    for _, unit in _iter_units(candidate):
        name = _unit_name(unit)
        if name:
            index[name] = unit
    applied, unknown = [], []
    for mv in moves:
        name = str(mv.get("name") or "") if isinstance(mv, dict) else ""
        unit = index.get(name)
        lat = to_float(mv.get("lat")) if isinstance(mv, dict) else None
        lng = to_float(mv.get("lng")) if isinstance(mv, dict) else None
        if unit is None or lat is None or lng is None:
            unknown.append(name or str(mv))
            continue
        _set_lat_lng(unit, lat, lng)
        applied.append({"name": name, "lat": lat, "lng": lng})
    # 与事后审计同一对函数：评估时说的越界，提交后审计必然同口径复现
    coord_errors = geo.get_coordinate_errors(candidate)
    violations = adjudicator.check_movement(prev_state, candidate, pre_adj)
    return {"ok": True, "applied": applied, "unknown": unknown,
            "coordinate_errors": coord_errors,
            "movement_violations": violations}


def execute_tool_call(session, war_state, call, pre_adj=None) -> dict:
    if not isinstance(call, dict):
        return {"ok": False, "error": "工具调用必须是对象 {{tool, args}}"}
    tool = str(call.get("tool") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    if tool == "get_observation":
        result = exec_get_observation(war_state, args)
    elif tool == "retrieve_history":
        result = exec_retrieve_history(session, args)
    elif tool == "evaluate_action":
        result = exec_evaluate_action(war_state, args, pre_adj)
    elif tool in ('inspect_trajectory', 'evaluate_counterfactual'):
        records = session.get('trajectory') or []
        selected = next((r for r in records if r['turn'] == args.get('turn')), None)
        if tool == 'inspect_trajectory':
            selected_records = [selected] if selected else records[-3:] if 'turn' not in args else []
            summaries = []
            for record in selected_records:
                summary = causal.record_summary(record)
                # Never recursively feed tool results containing older tool traces back to the model.
                summary.pop('tool_executions', None)
                summary['runs'] = [{k: run.get(k) for k in ('id', 'session_id', 'first_seq', 'last_seq', 'finish_reason')}
                                   for run in summary.get('runs', [])]
                summaries.append(summary)
            result = {'ok': bool(summaries), 'records': summaries}
        elif selected:
            try:
                result = {'ok': True, **causal.geometry_counterfactual(selected, undo=args.get('undo') or [])}
            except ValueError as exc:
                result = {'ok': False, 'error': str(exc)}
        else:
            result = {'ok': False, 'error': '该回合尚无已提交轨迹'}
    else:
        result = {"ok": False, "error": f"未知工具 `{tool}`；可用：get_observation / retrieve_history / evaluate_action"}
    result["tool"] = tool
    return result


# ==========================================
# 🔁 智能体回合主循环
# ==========================================
def run_decision_turn(session, prompt: str, role_id: str,
                      pre_adj=None, cancel_check=None,
                      observer: "harness_engine.RunObserver | None" = None) -> dict:
    """智能体回合：产出一个与常规回合同构的 result dict，交回提交管线。

    循环内任何失败（步数耗尽 / 用户取消）都抛 RuntimeError —— 由 next_turn
    转成 502/409，会话保持调用前原样（原子提交保证）。
    原生运行时插件连接时可取消在途调用；未连接时在步间取消。
    """
    message = prompt + build_tool_protocol()
    on_notification = observer.on_notification if observer is not None else None
    for step in range(MAX_AGENT_STEPS):
        if cancel_check is not None and cancel_check():
            raise RuntimeError(f"用户已取消，智能体回合在第 {step + 1} 步停止，战局未改动")
        result = harness_engine._run_brain(message, role_id, observer=observer,
                                           on_notification=on_notification, cancel_check=cancel_check)
        # _run_brain owns normal and recovered attempt metadata and elapsed time.
        try:
            parsed = harness_engine.parse_json_object(result.final_response)
        except ValueError:
            message = ("【协议错误】你的回复不是合法 JSON 对象。重读本回合的"
                       "「领域工具协议」，只回复 tool_calls 或 submit_orders 两种 JSON 之一。")
            continue

        if parsed.get("action") == "submit_orders":
            orders = parsed.get("orders")
            if not isinstance(orders, dict):
                message = "【协议错误】submit_orders 缺少 orders 对象（updated_state/report/…）。"
                continue
            candidate = orders.get("updated_state", orders)
            # 键名归一化先于坐标校验（与旧反思循环同一顺序铁律）
            state.normalize_state_units(candidate)
            errors = geo.get_coordinate_errors(candidate)
            from gentactix import fire_control
            errors.extend(fire_control.errors(candidate, session.get('war_state')))
            if errors:
                feedback = ("\n\n【系统最高警告：坐标物理规则冲突！】\n"
                            "你提交的 orders 中存在以下致命地理错误：\n")
                for err in errors:
                    feedback += f"- {err}\n"
                feedback += ("请你立刻重新评估战局，重新提交一组合法的 orders。"
                             "必须确保所有海军单位的经纬度都在海洋中！")
                message = feedback
                continue
            if step > 0:
                harness_engine.ENGINE_NOTICES.append(
                    {"level": "success",
                     "message": f"✅ 智能体在第 {step + 1}/{MAX_AGENT_STEPS} 步提交命令"})
            return orders

        calls = parsed.get("tool_calls")
        if isinstance(calls, list) and calls:
            results = [execute_tool_call(session, session.get("war_state"), call, pre_adj)
                       for call in calls[:MAX_TOOL_CALLS_PER_STEP]]
            if observer is not None:
                observer.tool_executions.extend({'source': 'application_json_protocol', 'step': step+1,
                    'call': copy.deepcopy(call), 'result': copy.deepcopy(outcome)}
                    for call, outcome in zip(calls[:MAX_TOOL_CALLS_PER_STEP], results))
            message = ("【工具执行结果】\n"
                       + json.dumps({"tool_results": results}, ensure_ascii=False)
                       + f"\n已用 {step + 1}/{MAX_AGENT_STEPS} 步。请继续查询，或提交 submit_orders。")
            continue

        message = ("【协议错误】既没有 tool_calls 也不是 submit_orders。"
                   "按「领域工具协议」二选一并只回复该 JSON。")
    raise RuntimeError(f"智能体回合 {MAX_AGENT_STEPS} 步内未提交合法命令，本回合失败（战局未改动）")
