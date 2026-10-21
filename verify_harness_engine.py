"""Offline regressions for backend/harness_engine.py (dsh harness 调用层).

Run with: conda run -n wargame python verify_harness_engine.py
DeepSeekHarness 子进程与 SRTM 坐标校验都被替身接管；不联网、不读 ~/.dsh。
覆盖：JSON 抽取、坐标反思重试循环、键名归一化先于坐标校验、
harness 隔离（dsh_home 必须落在项目内）、复盘/问答上下文、提示排空。
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gentactix import geo
from gentactix.config import MAX_API_RETRIES

try:  # deepseek_harness 只装在 conda wargame 环境；旧 .venv 里跳过而非报错
    from backend import harness_engine
    _HAS_DSH = True
except ImportError:
    _HAS_DSH = False


class FakeSession:
    """替身角色会话：run 转发回 FakeHarness，session_id 固定为角色 id。"""

    def __init__(self, harness, session_id):
        self.harness = harness
        self.id = session_id

    def run(self, prompt, *, on_notification=None):
        return self.harness.run(prompt, session_id=self.id,
                                on_notification=on_notification)


class FakeHarness:
    """替身：按脚本顺序返回 final_response，并记录每次收到的 prompt。

    responses 里的条目可以是纯字符串（text，finish_reason=completed），
    也可以是 SimpleNamespace(text=…, reasoning=…, finish_reason=…)
    以精确控制思维链与结束原因。events 按真实 SDK 形状构造：
    assistant/message 的 content 里 reasoning 块在前、text 块在后。
    start_session/resume_session 复刻真实 SDK 语义：resume 对 fail_resume
    名单里的 id 抛错（缺失/占用），其余正常恢复。
    """

    def __init__(self, responses, fail_resume=()):
        self.responses = list(responses)
        self.prompts = []
        self.session_ids = []
        self.started = []
        self.resumed = []
        self._fail_resume = set(fail_resume)

    def start_session(self, session_id=None):
        sid = session_id or "session-fake"
        self.started.append(sid)
        return FakeSession(self, sid)

    def resume_session(self, session_id):
        if session_id in self._fail_resume:
            raise RuntimeError(f"session {session_id} missing or occupied")
        self.resumed.append(session_id)
        return FakeSession(self, session_id)

    def run(self, prompt, session_id="", on_notification=None):
        self.prompts.append(prompt)
        self.session_ids.append(session_id)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, str):
            text, reasoning, finish = item, "", "completed"
        else:
            text = item.text
            reasoning = getattr(item, "reasoning", "")
            finish = getattr(item, "finish_reason", "completed")
        content = []
        if reasoning:
            content.append({"type": "reasoning", "text": reasoning})
        content.append({"type": "text", "text": text})
        events = [{"type": "assistant/message",
                   "data": {"message": {"role": "assistant", "content": content}}}]
        return SimpleNamespace(final_response=text, finish_reason=finish,
                               events=events, notifications=[],
                               session_id=session_id)


def state_json(navy_lat=30.0, navy_lng=115.0):
    return {
        "updated_state": {
            "red_units": [{"name": "楼船军", "type": "海军", "lat": navy_lat, "lng": navy_lng, "hp": 100}],
            "blue_units": [],
        }
    }


@unittest.skipUnless(_HAS_DSH, "deepseek_harness 仅在 conda wargame 环境中安装")
class HarnessEngineTests(unittest.TestCase):
    def setUp(self):
        harness_engine.RUNTIME_STATUS['connected'] = False
        harness_engine.ENGINE_NOTICES.clear()
        harness_engine._role_sessions.clear()
        harness_engine._role_amnesia.clear()
        geo.GEO_NOTICES.clear()

    def tearDown(self):
        harness_engine.RUNTIME_STATUS['connected'] = False
        harness_engine.ENGINE_NOTICES.clear()
        harness_engine._role_sessions.clear()
        harness_engine._role_amnesia.clear()
        geo.GEO_NOTICES.clear()

    # ---- complete() ----
    def test_complete_returns_final_response(self):
        fake = FakeHarness(["前线平稳。"])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            self.assertEqual(harness_engine.complete("报告战况"), "前线平稳。")

    def test_complete_raises_on_empty_response(self):
        fake = FakeHarness(["   "])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            with self.assertRaises(RuntimeError):
                harness_engine.complete("报告战况")

    def test_complete_rejects_non_completed_finish_reason(self):
        # 故障注入：非空文本 + 坏结束原因 → 必须拒收（半截 JSON 比可见失败更危险）
        for bad in ("error", "max-tokens", None):
            fake = FakeHarness([SimpleNamespace(text='{"a": 1}', finish_reason=bad)])
            with patch.object(harness_engine, "get_harness", return_value=fake):
                with self.assertRaises(RuntimeError):
                    harness_engine.complete("给出 JSON")

    def test_completed_finish_reason_is_accepted(self):
        fake = FakeHarness([SimpleNamespace(text="前线平稳。", finish_reason="completed")])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            self.assertEqual(harness_engine.complete("报告战况"), "前线平稳。")

    def test_extract_reasoning_from_event_blocks(self):
        fake = FakeHarness([SimpleNamespace(text="答", reasoning="先想")])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            result = harness_engine.complete_ex("问")
        self.assertEqual(harness_engine.extract_reasoning(result.events), "先想")
        self.assertEqual(result.final_response, "答")

    # ---- get_ai_response：JSON 抽取 ----
    def assert_extracts(self, raw):
        fake = FakeHarness([raw])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            self.assertEqual(harness_engine.get_ai_response("p"), {"a": 1})

    def test_json_extraction_plain_fenced_and_prose(self):
        self.assert_extracts('{"a": 1}')
        self.assert_extracts('```json\n{"a": 1}\n```')
        self.assert_extracts('好的，推演结果如下：\n{"a": 1}\n以上。')

    def test_json_extraction_failures(self):
        for raw in ['{"a": ', '完全没有大括号']:
            fake = FakeHarness([raw])
            with patch.object(harness_engine, "get_harness", return_value=fake):
                with self.assertRaises(ValueError):
                    harness_engine.get_ai_response("p")

    # ---- get_ai_response_with_retry：坐标反思循环 ----
    def test_retry_loop_recovers_after_feedback(self):
        fake = FakeHarness([
            json.dumps(state_json()),   # 第一次：海军在陆地
            json.dumps(state_json(navy_lat=20.0, navy_lng=120.0)),  # 第二次：合法
        ])
        verdicts = [ ["海军上岸错误"], [] ]
        with patch.object(harness_engine, "get_harness", return_value=fake), \
             patch.object(harness_engine, "get_coordinate_errors",
                          side_effect=lambda s: verdicts.pop(0) if verdicts else []):
            result = harness_engine.get_ai_response_with_retry("初始态势")
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("坐标物理规则冲突", fake.prompts[1])
        self.assertIn("自我修正", json.dumps(harness_engine.ENGINE_NOTICES, ensure_ascii=False))

    def test_retry_exhausted_forces_pass(self):
        fake = FakeHarness([json.dumps(state_json())] * MAX_API_RETRIES)
        with patch.object(harness_engine, "get_harness", return_value=fake), \
             patch.object(harness_engine, "get_coordinate_errors",
                          return_value=["海军上岸错误"]):
            result = harness_engine.get_ai_response_with_retry("初始态势")
        self.assertEqual(len(fake.prompts), MAX_API_RETRIES)
        self.assertIn("强制放行", json.dumps(harness_engine.ENGINE_NOTICES, ensure_ascii=False))
        self.assertIn("updated_state", result)

    def test_normalize_runs_before_coordinate_check(self):
        # 中文键战局：归一化必须发生在坐标校验之前，否则 is_naval 判不出海军
        bad = {"updated_state": {"red_units": [
            {"单位名称": "楼船军", "类型": "海军", "纬度": 30.0, "经度": 115.0, "兵力": 100}]}}
        captured = {}

        def spy(state):
            captured.update(state)
            return []

        fake = FakeHarness([json.dumps(bad)])
        with patch.object(harness_engine, "get_harness", return_value=fake), \
             patch.object(harness_engine, "get_coordinate_errors", side_effect=spy):
            harness_engine.get_ai_response_with_retry("p")
        # 校验收到的是 updated_state 内层（与实现里的 .get("updated_state", …) 一致）
        unit = captured["red_units"][0]
        self.assertEqual(unit.get("type"), "海军")
        self.assertEqual(unit.get("lat"), 30.0)

    # ---- harness 隔离：绝不触碰 ~/.dsh ----
    def test_harness_launches_isolated(self):
        mock_cls = Mock()
        with patch.object(harness_engine, "_harness", None), \
             patch.object(harness_engine, "DeepSeekHarness", mock_cls), \
             patch.object(harness_engine.runtime_bridge, 'connect', return_value={'plugin': 'test-native'}):
            harness_engine.get_harness()
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["profile"], "sdk-minimal")
        self.assertTrue(Path(kwargs["dsh_home"]).is_absolute())
        # 隔离保证：不是 dsh 默认全局 home（~/.dsh），且落在仓库内
        self.assertNotEqual(Path(kwargs["dsh_home"]), Path.home() / ".dsh")
        self.assertTrue(kwargs["dsh_home"].startswith(str(harness_engine.ROOT)))
        self.assertTrue(kwargs["dsh_home"].endswith(".dsh-wargame-home"))
        self.assertEqual(kwargs["base_url"], harness_engine.LLM_BASE_URL)
        self.assertEqual(kwargs["model"], harness_engine.LLM_MODEL)
        self.assertEqual(kwargs["env"]["DSH_CONTEXT_WINDOW"], "262144")
        # 预算：单次调用超时默认 900s（实测大生成 ~10 分钟，超时 ⇒ 可见失败）
        self.assertEqual(harness_engine.REQUEST_TIMEOUT_SECONDS, 900.0)
        self.assertEqual(kwargs["request_timeout_seconds"], 900.0)
        mock_cls.return_value.start.assert_called_once()

    # ---- 👁️ 观测器：事件流 → 可见提示 + 元数据 ----
    @staticmethod
    def _notif(event):
        return SimpleNamespace(method="session.event",
                               payload={"sessionId": "s", "event": event})

    def test_observer_notices_and_throttle(self):
        obs = harness_engine.RunObserver("推演 T+1")
        obs.on_notification(self._notif({"type": "turn/start"}))
        for _ in range(7):
            obs.on_notification(self._notif({"type": "step/start"}))
        msgs = [n["message"] for n in harness_engine.ENGINE_NOTICES]
        self.assertTrue(any("模型开始思考" in m for m in msgs))
        # step/start 节流：7 步只透第 1、6 步两条，不刷屏
        self.assertEqual(len([m for m in msgs if "推理进行中" in m]), 2)
        self.assertEqual(obs.turns, 1)
        self.assertEqual(obs.steps, 7)
        self.assertEqual(obs.progress["steps"], 7)  # 进度镜像同步

    def test_observer_tokens_and_context_warning(self):
        obs = harness_engine.RunObserver("推演")
        obs.on_notification(self._notif({
            "type": "assistant/message",
            "data": {"usage": {"totalTokens": 120000}}}))
        self.assertEqual(obs.total_tokens, 120000)
        obs.on_notification(self._notif({
            "type": "assistant/message",
            "data": {"usage": {"totalTokens": 200000}}}))  # > 70% of 262144
        warns = [n for n in harness_engine.ENGINE_NOTICES if n["level"] == "warning"]
        self.assertEqual(len(warns), 1)
        self.assertIn("截断", warns[0]["message"])
        # 只警告一次，不每步重复
        obs.on_notification(self._notif({
            "type": "assistant/message",
            "data": {"usage": {"totalTokens": 250000}}}))
        self.assertEqual(len([n for n in harness_engine.ENGINE_NOTICES
                              if n["level"] == "warning"]), 1)
        self.assertEqual(obs.total_tokens, 250000)

    def test_observer_meta_shape(self):
        obs = harness_engine.RunObserver("推演")
        obs.last_finish = "completed"
        obs.turns, obs.steps, obs.total_tokens, obs.elapsed_ms = 1, 3, 999, 1234.5
        meta = obs.meta()
        self.assertEqual(meta["finish_reason"], "completed")
        self.assertEqual(meta["turns"], 1)
        self.assertEqual(meta["steps"], 3)
        self.assertEqual(meta["tokens"], 999)
        self.assertEqual(meta["elapsed_ms"], 1234)

    # ---- 复盘 / 问答 ----
    def test_strategic_review_shape(self):
        fake = FakeHarness(["复盘正文"])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            reasoning, conclusion = harness_engine.get_strategic_review(["战报1"], {"red_units": []})
        self.assertEqual(reasoning, "")  # 无 reasoning 块 → 空串，而不是伪报错
        self.assertEqual(conclusion, "复盘正文")
        self.assertIn("战报1", fake.prompts[0])

    def test_strategic_review_returns_real_reasoning(self):
        # 旧实现硬编码返回 ""；模型确实产出思维块时必须透传（旧码此测必挂）
        fake = FakeHarness([SimpleNamespace(text="复盘正文", reasoning="先评估粮道")])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            reasoning, conclusion = harness_engine.get_strategic_review(["战报1"], {"red_units": []})
        self.assertEqual(reasoning, "先评估粮道")
        self.assertEqual(conclusion, "复盘正文")

    def test_ask_xuanji_returns_real_reasoning(self):
        fake = FakeHarness([SimpleNamespace(text="答", reasoning="权衡主攻方向")])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            reasoning, answer = harness_engine.ask_xuanji(["h"], {"red_units": []}, [], "主攻方向？")
        self.assertEqual(reasoning, "权衡主攻方向")
        self.assertEqual(answer, "答")

    def test_cold_chat_updates_the_supplied_observer(self):
        fake = FakeHarness(["答"])
        observer = harness_engine.RunObserver("问答验收")
        with patch.object(harness_engine, "get_harness", return_value=fake):
            harness_engine.ask_xuanji([], {"red_units": []}, [], "检查", observer=observer)
        self.assertEqual(observer.last_finish, "completed")
        self.assertIn("没有确定性伤亡", fake.prompts[0])

    def test_ask_xuanji_keeps_last_five_turns(self):
        chats = [{"role": "user", "content": f"问{i}"} for i in range(8)]
        fake = FakeHarness(["答"])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            _, answer = harness_engine.ask_xuanji(["h"], {"red_units": []}, chats, "当前主攻方向？")
        self.assertEqual(answer, "答")
        prompt = fake.prompts[0]
        self.assertNotIn("问2", prompt)
        self.assertIn("问3", prompt)
        self.assertIn("当前主攻方向？", prompt)

    # ---- 🧬 持续角色会话 ----
    def test_persist_roles_default_off(self):
        # 默认关：旧单发路径（每次新会话）行为与全部既有测试保持原样
        self.assertFalse(harness_engine.PERSIST_ROLES)

    def test_role_session_reused_across_turns(self):
        fake = FakeHarness(["回合1答", "回合2答"])
        rid = harness_engine.role_session_id("b1", "engine")
        with patch.object(harness_engine, "get_harness", return_value=fake):
            harness_engine.run_role(rid, "回合1")
            harness_engine.run_role(rid, "回合2")
        # 只从磁盘恢复一次，之后进程内复用同一 Session 对象
        self.assertEqual(fake.resumed, [rid])
        self.assertEqual(fake.started, [])
        self.assertEqual(fake.session_ids, [rid, rid])
        self.assertEqual(fake.prompts, ["回合1", "回合2"])

    def test_role_sessions_isolated_per_battle(self):
        fake = FakeHarness(["甲", "乙"])
        rid_a = harness_engine.role_session_id("battle-a", "engine")
        rid_b = harness_engine.role_session_id("battle-b", "engine")
        with patch.object(harness_engine, "get_harness", return_value=fake):
            harness_engine.run_role(rid_a, "甲局")
            harness_engine.run_role(rid_b, "乙局")
        self.assertEqual(fake.session_ids, [rid_a, rid_b])

    def test_role_resume_failure_falls_back_with_notice(self):
        # 陈旧 session.lock / 会话缺失：绝不静默失忆 —— 可见警告 + 失忆标记
        rid = harness_engine.role_session_id("b1", "chat")
        fake = FakeHarness(["答"], fail_resume={rid})
        with patch.object(harness_engine, "get_harness", return_value=fake):
            result = harness_engine.run_role(rid, "问")
        self.assertEqual(result.final_response, "答")
        self.assertEqual(fake.resumed, [])
        self.assertEqual(fake.started, [rid])
        notices = json.dumps(harness_engine.ENGINE_NOTICES, ensure_ascii=False)
        self.assertIn("无法恢复", notices)
        self.assertTrue(harness_engine.consume_role_amnesia(rid))
        self.assertFalse(harness_engine.consume_role_amnesia(rid))  # 只消费一次

    def test_run_role_gates_finish_reason(self):
        # 角色会话同样吃 _gate：坏结束原因必须拒收
        fake = FakeHarness([SimpleNamespace(text="半截", finish_reason="error")])
        with patch.object(harness_engine, "get_harness", return_value=fake):
            with self.assertRaises(RuntimeError):
                harness_engine.run_role("gentactix-b1-engine", "p")

    def test_ask_xuanji_warm_session_sends_only_question(self):
        fake = FakeHarness(["答1", "答2"])
        rid = harness_engine.role_session_id("b1", "chat")
        with patch.object(harness_engine, "get_harness", return_value=fake):
            harness_engine.ask_xuanji(["战报A"], {"red_units": []}, [], "第一问", role_id=rid)
            harness_engine.ask_xuanji(["战报A"], {"red_units": []},
                                      [{"role": "user", "content": "第一问"}],
                                      "第二问", role_id=rid)
        # 冷会话（首轮）自带全部上下文；热会话 harness 已记得，只发问题
        self.assertIn("开战至今的全部战报", fake.prompts[0])
        self.assertNotIn("战报A", fake.prompts[1])
        self.assertIn("第二问", fake.prompts[1])
        self.assertEqual(fake.session_ids, [rid, rid])

    def test_forget_role_sessions_drops_battle_registry(self):
        fake = FakeHarness(["答"])
        rid = harness_engine.role_session_id("b1e0", "engine")
        with patch.object(harness_engine, "get_harness", return_value=fake):
            harness_engine.run_role(rid, "p")
        self.assertIn(rid, harness_engine._role_sessions)
        harness_engine.forget_role_sessions("b1e")
        self.assertNotIn(rid, harness_engine._role_sessions)

    # ---- 提示排空 ----
    def test_drain_notices_merges_and_clears(self):
        harness_engine.ENGINE_NOTICES.append({"level": "warning", "message": "引擎提示"})
        geo.GEO_NOTICES.append({"level": "error", "message": "地理提示"})
        drained = harness_engine.drain_notices()
        self.assertEqual([n["message"] for n in drained], ["引擎提示", "地理提示"])
        self.assertEqual(harness_engine.drain_notices(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
