"""🤖 领域工具协议（backend/agent_tools.py）离线回归。

不打桩执行器本身：坐标审计（geo）、位移审计（adjudicator）真实执行；
只有模型出口 harness_engine.run_role 用脚本序列替换 —— 不联网、不调模型。

覆盖：协议文案、观察白名单、检索可见性（RAG 关必须说出口）、
evaluate_action 的 dry-run 保证（真实战局绝不被改写）、
智能体循环（工具回喂 / 坐标反弹 / 协议违规 / 步数耗尽 / 取消）。

在 conda wargame 环境运行：
    conda run -n wargame python verify_agent_tools.py
缺 fastapi / harness SDK 的环境（如 .venv）自动跳过，verify_all 照常绿。
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from backend import agent_tools, harness_engine
    _HAS_BACKEND = True
except Exception:  # noqa: BLE001
    _HAS_BACKEND = False


def _unit(name, lat, lng, hp=5000, utype="步兵", morale=80, supply=70):
    return {"name": name, "type": utype, "hp": hp, "lat": lat, "lng": lng,
            "morale": morale, "supply": supply, "combat_power": 50, "speed": 5}


def _state():
    return {
        "current_time": "前262年四月",
        "current_terrain": "山地",
        "red_units": [_unit("秦军左翼", 35.700, 112.800)],
        "blue_units": [_unit("赵军粮道", 35.740, 112.860, utype="辎重")],
    }


def _orders(state):
    return {"action": "submit_orders", "orders": {
        "updated_state": state, "report": "两军接触。", "eliminated": [],
    }}


@unittest.skipUnless(_HAS_BACKEND, "当前环境缺 fastapi 或 harness SDK（.venv 属正常）")
class AgentToolsTest(unittest.TestCase):
    def setUp(self):
        harness_engine.ENGINE_NOTICES.clear()
        self.messages = []

    def tearDown(self):
        harness_engine.ENGINE_NOTICES.clear()

    def _fake_run_role(self, replies):
        """脚本化模型出口：按序返回回复，并记录每次收到的消息。"""
        seq = list(replies)

        def fake(role_id, prompt, *, on_notification=None):
            self.messages.append(prompt)
            text = seq.pop(0) if seq else "不是 JSON"
            return SimpleNamespace(final_response=text, finish_reason="completed",
                                   events=[], notifications=[], session_id=role_id)
        return fake

    # ---- 海军判定：字面兵种标签必须命中（历史缺陷：type="海军" 逃过海陆审计）----
    def test_naval_keywords_cover_literal_labels(self):
        from gentactix.config import is_naval
        for label in ("海军", "水军", "舰队", "战船", "ship"):
            self.assertTrue(is_naval(label), label)
        for label in ("步兵", "辎重", "城塞"):
            self.assertFalse(is_naval(label), label)

    # ---- 协议文案 ----
    def test_protocol_text_declares_both_shapes(self):
        text = agent_tools.build_tool_protocol()
        for needle in ("tool_calls", "submit_orders", "get_observation",
                       "retrieve_history", "evaluate_action", "禁止调用 harness 内置工具"):
            self.assertIn(needle, text)

    # ---- get_observation ----
    def test_get_observation_whitelist_and_filter(self):
        view = agent_tools.exec_get_observation(_state(), {})
        self.assertTrue(view["ok"])
        self.assertEqual(len(view["units"]), 2)
        row = next(u for u in view["units"] if u["name"] == "秦军左翼")
        self.assertEqual(row["team"], "red")
        self.assertEqual(row["hp"], 5000)
        self.assertAlmostEqual(row["lat"], 35.7)
        # 白名单之外的派生值不外泄：模型拿不到 speed/combat_power 去编数据
        self.assertNotIn("speed", row)
        self.assertNotIn("combat_power", row)

        only = agent_tools.exec_get_observation(_state(), {"names": ["赵军粮道"]})
        self.assertEqual([u["name"] for u in only["units"]], ["赵军粮道"])

        missing = agent_tools.exec_get_observation(_state(), {"names": ["不存在军"]})
        self.assertFalse(missing["ok"])
        self.assertIn("未找到", missing["error"])

    # ---- retrieve_history：可见性规则 ----
    def test_retrieve_history_rag_off_is_visible(self):
        session = {"use_rag": False, "history": ["【T+1】秦军左翼压迫赵军壁垒。"]}
        hit = agent_tools.exec_retrieve_history(session, {"query": "秦军左翼"})
        self.assertTrue(hit["ok"])
        self.assertEqual(hit["passages_note"], "史料检索未开启")  # 没开就必须说出口
        self.assertEqual(len(hit["history_hits"]), 1)

        miss = agent_tools.exec_retrieve_history(session, {"query": "海军登陆"})
        self.assertFalse(miss["ok"])
        self.assertIn("史料检索未开启", miss["error"])

        self.assertFalse(agent_tools.exec_retrieve_history(session, {})["ok"])

    # ---- evaluate_action：dry-run 铁律 ----
    def test_retrieve_history_preserves_complete_long_evidence(self):
        row = "| 5000余人 | " + "统计范围说明" * 110 + "；不是阵亡。[S11](https://example.org/source) |"
        hit = {"source": "sample.md", "doc_title": "包座", "section": "伤亡", "score": 1,
               "text": "| 数量 | 范围与依据 |\n|---|---|\n" + row}
        with patch.object(agent_tools.rag, "search", return_value=[hit]):
            result = agent_tools.exec_retrieve_history({"use_rag": True, "history": []}, {"query": "伤亡"})
        self.assertTrue(result["ok"])
        self.assertIn(row, result["passages"][0]["text"])

    def test_evaluate_action_is_dry_run(self):
        prev = _state()
        before = copy.deepcopy(prev)
        pre_adj = {"units": [{"name": "秦军左翼", "reach_km": 2.0}]}
        out = agent_tools.exec_evaluate_action(
            prev, {"moves": [{"name": "秦军左翼", "lat": 35.90, "lng": 112.80}]}, pre_adj)
        self.assertTrue(out["ok"])
        self.assertEqual(prev, before)  # 真实战局一个字节都不能变
        self.assertEqual(len(out["movement_violations"]), 1)
        self.assertEqual(out["movement_violations"][0]["name"], "秦军左翼")
        self.assertGreater(out["movement_violations"][0]["over_km"], 0)

    def test_evaluate_action_finds_navy_inland_and_unknown(self):
        prev = _state()
        out = agent_tools.exec_evaluate_action(prev, {"moves": [
            {"name": "赵军粮道", "lat": 30.0, "lng": 115.0},   # 假想海军单位会被面具拦下？粮道非海军 → 看坐标审计
            {"name": "不存在旅", "lat": 35.7, "lng": 112.8},
        ]}, None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["unknown"], ["不存在旅"])
        self.assertEqual(len(out["applied"]), 1)

        navy = _state()
        navy["red_units"] = [_unit("楼船军", 35.70, 112.80, utype="海军")]
        out2 = agent_tools.exec_evaluate_action(navy, {"moves": [
            {"name": "楼船军", "lat": 35.71, "lng": 112.81}]}, None)
        self.assertTrue(out2["coordinate_errors"])  # 海军 inland 必须被抓出

        self.assertFalse(agent_tools.exec_evaluate_action(prev, {}, None)["ok"])

    def test_execute_tool_call_unknown_tool(self):
        out = agent_tools.execute_tool_call({"use_rag": False, "history": []}, _state(),
                                            {"tool": "bash", "args": {}})
        self.assertFalse(out["ok"])
        self.assertIn("bash", out["error"])

    # ---- run_decision_turn 主循环 ----
    def test_happy_path_tools_then_submit(self):
        good = _orders(copy.deepcopy(_state()))
        fake = self._fake_run_role([
            json.dumps({"tool_calls": [{"tool": "get_observation", "args": {}}]}),
            json.dumps(good, ensure_ascii=False),
        ])
        session = {"war_state": _state(), "use_rag": False, "history": []}
        with patch.object(harness_engine, "run_role", fake):
            result = agent_tools.run_decision_turn(session, "回合任务", "gentactix-b1-engine")
        # 返回的是 orders 本体（与常规回合 result 同构，直接进提交管线）
        self.assertEqual(result["report"], "两军接触。")
        self.assertIn("updated_state", result)
        self.assertIn("工具执行结果", self.messages[1])
        self.assertIn("get_observation", self.messages[1])

    def test_coordinate_bounce_back_same_session(self):
        bad = copy.deepcopy(_state())
        bad["red_units"] = [_unit("楼船军", 35.70, 112.80, utype="海军")]  # 海军在内陆
        good = copy.deepcopy(_state())
        fake = self._fake_run_role([
            json.dumps(_orders(bad), ensure_ascii=False),
            json.dumps(_orders(good), ensure_ascii=False),
        ])
        session = {"war_state": _state(), "use_rag": False, "history": []}
        with patch.object(harness_engine, "run_role", fake):
            result = agent_tools.run_decision_turn(session, "任务", "gentactix-b1-engine")
        # 反弹进同一会话的下一条消息（复用旧反思循环的警告文案）
        self.assertIn("坐标物理规则冲突", self.messages[1])
        self.assertEqual(result["updated_state"]["red_units"][0]["name"], "秦军左翼")

    def test_protocol_violation_gets_feedback(self):
        fake = self._fake_run_role([
            "朕直接下令，不玩你的 JSON。",
            json.dumps(_orders(copy.deepcopy(_state())), ensure_ascii=False),
        ])
        session = {"war_state": _state(), "use_rag": False, "history": []}
        with patch.object(harness_engine, "run_role", fake):
            agent_tools.run_decision_turn(session, "任务", "gentactix-b1-engine")
        self.assertIn("协议错误", self.messages[1])

    def test_exhausted_steps_fail_without_touching_state(self):
        fake = self._fake_run_role(["不是 JSON"] * 9)
        session = {"war_state": _state(), "use_rag": False, "history": []}
        before = copy.deepcopy(session["war_state"])
        with patch.object(harness_engine, "run_role", fake):
            with self.assertRaises(RuntimeError) as ctx:
                agent_tools.run_decision_turn(session, "任务", "gentactix-b1-engine")
        self.assertIn("未提交合法命令", str(ctx.exception))
        self.assertEqual(session["war_state"], before)  # 失败 ⇒ 战局原样

    def test_cancel_check_stops_between_steps(self):
        flags = iter([False, True])
        fake = self._fake_run_role([
            json.dumps({"tool_calls": [{"tool": "get_observation", "args": {}}]}),
        ])
        session = {"war_state": _state(), "use_rag": False, "history": []}
        with patch.object(harness_engine, "run_role", fake):
            with self.assertRaises(RuntimeError) as ctx:
                agent_tools.run_decision_turn(session, "任务", "gentactix-b1-engine",
                                              cancel_check=lambda: next(flags))
        self.assertIn("取消", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
