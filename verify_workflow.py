"""Exercise the complete Streamlit flow with a local OpenAI SDK transport.

Initialization, turns, retrieval, adjudication, review, chat, exports and failures
use real application code. Only DeepSeek HTTP responses are fixed test data.
"""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from openai import OpenAI, __version__ as OPENAI_VERSION
from streamlit.testing.v1 import AppTest

if int(OPENAI_VERSION.split(".", 1)[0]) >= 3:
    import httpx2 as httpx
else:
    import httpx

from gentactix import engine
from verify_features import battle


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stage = "init"
        self.step_minutes = 30
        self.requests = []
        self.fixture = battle()
        self.fixture["situation_report"] = "两军在丹河两岸对峙。"
        self.fixture["source_note"] = "本次为固定响应测试。"
        transport = httpx.MockTransport(self.respond)
        self.client = OpenAI(
            api_key="offline-test-key", base_url="https://deepseek.invalid/v1",
            http_client=httpx.Client(transport=transport), max_retries=0,
        )
        self.client_patch = patch.object(engine, "get_client", return_value=self.client)
        self.client_factory = self.client_patch.start()
        self.export_patch = patch.object(st, "download_button", wraps=st.download_button)
        self.export = self.export_patch.start()
        engine._MODEL_DEGRADED.clear()
        self.app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=60)

    def tearDown(self):
        self.client_patch.stop()
        self.export_patch.stop()
        self.client.close()
        engine._MODEL_DEGRADED.clear()

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.stage == "failure":
            return httpx.Response(401, json={"error": {"message": "test invalid API key", "type": "authentication_error"}})
        if self.stage == "disconnected":
            raise httpx.ConnectError("test disconnection", request=request)
        if self.stage == "timeout":
            raise httpx.ReadTimeout("test read timeout", request=request)
        if self.stage == "init":
            content = json.dumps(self.fixture, ensure_ascii=False)
        elif self.stage == "step":
            updated = copy.deepcopy(self.fixture)
            updated["current_time"] = f"秋 · T+{self.step_minutes}分钟"
            updated["red_units"][0]["lat"] += 0.001
            updated["red_units"][0]["hp"] = 950
            updated["engagements"] = [
                {"lat": 35.84, "lng": 112.86, "attacker": "赵军", "attacker_pos": [35.831, 112.85],
                 "description": "测试交战扇区"}
            ]
            content = json.dumps({"updated_state": updated, "report": "前军推进，双方开始接触。",
                                  "source_note": "测试数据，非真实推演。"}, ensure_ascii=False)
        else:
            content = "测试复盘：补给影响机动。" if self.stage == "review" else "测试答复：注意粮道与地形。"
        return httpx.Response(200, json={
            "id": "offline-test", "object": "chat.completion", "created": 0,
            "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": content}}],
        })

    def run_app(self):
        self.export.reset_mock()
        self.app.run()
        self.assertFalse(list(self.app.exception), str(list(self.app.exception)))
        if self.export.called:
            return json.loads(self.export.call_args.kwargs["data"])
        return None

    def click(self, label):
        next(button for button in self.app.button if label in button.label).click()
        return self.run_app()

    def configure(self):
        self.run_app()
        self.app.text_input[0].set_value("offline-test-key")
        self.app.text_area[0].set_value("长平之战")
        self.run_app()

    def test_full_workflow_and_current_exports(self):
        self.configure()
        for toggle in self.app.toggle:
            if "启用史料" in toggle.label or "启用确定性" in toggle.label:
                toggle.set_value(True)
        self.app.select_slider[0].set_value("1小时")
        self.step_minutes = 60
        self.run_app()
        initial_export = self.click("初始化沙盘")
        self.assertEqual(self.app.session_state["turn_count"], 0)
        self.assertTrue(self.app.session_state["history"])
        self.assertGreater(self.app.session_state["rag_last"]["injected"], 0)
        self.assertIsNotNone(self.app.session_state["adj_last"])
        self.assertIn("史料摘录", self.requests[-1]["messages"][-1]["content"])

        next(toggle for toggle in self.app.toggle if "实时情报" in toggle.label).set_value(True)
        self.run_app()
        self.stage = "step"
        turn_export = self.click("下一轮推演")
        self.assertEqual(self.app.session_state["turn_count"], 1)
        self.assertEqual(self.app.session_state["war_state"]["red_units"][0]["hp"], 950)
        self.assertEqual(self.app.session_state["last_war_state"]["red_units"][0]["hp"], 1000)
        self.assertIn("测试数据", self.app.session_state["history"][0])
        self.assertIn("裁决", self.requests[-1]["messages"][-1]["content"])
        self.assertIn("黑天鹅事件", self.requests[-1]["messages"][-1]["content"])

        self.stage = "review"
        review_export = self.click("呼叫总参谋长")
        self.assertIn("测试复盘", self.app.session_state["review_text"])
        self.stage = "chat"
        self.app.chat_input[0].set_value("请解释粮道的作用")
        chat_export = self.run_app()
        self.assertEqual(len(self.app.session_state["chat_messages"]), 2)
        self.assertIn("测试答复", self.app.session_state["chat_messages"][-1]["content"])

        for label, snapshot in [("initial", initial_export), ("turn", turn_export),
                                ("review", review_export), ("chat", chat_export)]:
            with self.subTest(export=label):
                self.assertIsNotNone(snapshot, "Export must be available immediately after initialization")
                self.assertEqual(snapshot["turn_count"], 0 if label == "initial" else 1)
                if label in ("review", "chat"):
                    self.assertIn("测试复盘", snapshot["review"]["conclusion"] or "")
                if label == "chat":
                    self.assertEqual(len(snapshot["chat_messages"]), 2)

        before = copy.deepcopy(self.app.session_state["war_state"])
        next(toggle for toggle in self.app.toggle if "示例交战" in toggle.label).set_value(True)
        self.run_app()
        self.assertEqual(before, self.app.session_state["war_state"])

        self.stage = "init"
        self.click("初始化沙盘")
        self.assertEqual(self.app.session_state["turn_count"], 0)
        self.assertEqual(len(self.app.session_state["history"]), 1)
        self.assertEqual(self.app.session_state["chat_messages"], [])
        self.assertIsNone(self.app.session_state["review_text"])

    def test_missing_inputs_are_reported_without_requests(self):
        self.run_app()
        self.click("初始化沙盘")
        self.assertTrue(list(self.app.warning))
        self.click("下一轮推演")
        self.assertTrue(list(self.app.warning))
        self.click("呼叫总参谋长")
        self.assertTrue(list(self.app.error))
        self.assertEqual(self.requests, [])

    def test_api_failure_preserves_existing_battle(self):
        self.configure()
        self.click("初始化沙盘")
        before = copy.deepcopy(self.app.session_state["war_state"])
        history = list(self.app.session_state["history"])
        self.stage = "failure"
        self.click("下一轮推演")
        self.assertEqual(self.app.session_state["war_state"], before)
        self.assertEqual(self.app.session_state["history"], history)
        self.assertEqual(self.app.session_state["turn_count"], 0)
        self.assertTrue(list(self.app.error))

    def test_connection_failure_preserves_battle_and_next_click_recovers(self):
        self.configure()
        self.click("初始化沙盘")
        before = copy.deepcopy(self.app.session_state["war_state"])
        self.stage = "disconnected"
        self.click("下一轮推演")
        self.assertEqual(self.app.session_state["war_state"], before)
        self.assertEqual(self.app.session_state["turn_count"], 0)
        self.assertEqual(len(self.app.session_state["history"]), 1)
        message = " ".join(error.value for error in self.app.error)
        self.assertIn("连接", message)
        self.assertNotIn("AI 生成数据错误", message)
        self.client_factory.clear.assert_called_once_with("offline-test-key")
        self.stage = "step"
        snapshot = self.click("下一轮推演")
        self.assertFalse(list(self.app.error))
        self.assertEqual(self.app.session_state["turn_count"], 1)
        self.assertEqual(snapshot["turn_count"], 1)

    def test_timeout_is_distinguished_from_invalid_model_data(self):
        self.configure()
        self.click("初始化沙盘")
        self.stage = "timeout"
        self.click("下一轮推演")
        message = " ".join(error.value for error in self.app.error)
        self.assertIn("超时", message)
        self.assertNotIn("AI 生成数据错误", message)
        self.assertEqual(self.app.session_state["turn_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
