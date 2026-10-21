"""Offline engine regressions: SDK responses, fallback, retries, review and chat.

Run with: .venv/bin/python verify_engine.py
Only the API client and geographic lookup are stubbed; no credentials are used.
"""

import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gentactix import engine
from gentactix.config import MODEL_ENGINE, MODEL_FALLBACKS, MODEL_STRATEGIST
from openai import OpenAI, __version__ as OPENAI_VERSION

if int(OPENAI_VERSION.split(".", 1)[0]) >= 3:
    import httpx2 as httpx
else:
    import httpx


def response(content, **fields):
    message = SimpleNamespace(content=content, **fields)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class EngineTests(unittest.TestCase):
    def setUp(self):
        engine._MODEL_DEGRADED.clear()
        self.client = Mock()
        self.create = self.client.chat.completions.create
        self.cache_patch = patch.object(engine, "get_client", return_value=self.client)
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        engine._MODEL_DEGRADED.clear()

    def test_fallback_survives_subsequent_turns(self):
        fallback = MODEL_FALLBACKS[MODEL_ENGINE][0]

        def complete(**kwargs):
            if kwargs["model"] == MODEL_ENGINE:
                raise ValueError("model does not exist")
            return response("ok")

        self.create.side_effect = complete
        with patch.object(engine.st, "warning"):
            for _ in range(3):
                self.assertEqual(
                    engine.chat_completion(self.client, MODEL_ENGINE, []).choices[0].message.content,
                    "ok",
                )
        self.assertEqual(
            [call.kwargs["model"] for call in self.create.call_args_list],
            [MODEL_ENGINE, fallback, fallback, fallback],
        )

    def test_fallback_remembers_last_working_model(self):
        fallback = MODEL_FALLBACKS[MODEL_STRATEGIST][-1]

        def complete(**kwargs):
            if kwargs["model"] != fallback:
                raise ValueError("unknown model")
            return response("ok")

        self.create.side_effect = complete
        with patch.object(engine.st, "warning"):
            for _ in range(2):
                engine.chat_completion(self.client, MODEL_STRATEGIST, [])
        self.assertEqual(
            [call.kwargs["model"] for call in self.create.call_args_list],
            [MODEL_STRATEGIST, *MODEL_FALLBACKS[MODEL_STRATEGIST], fallback],
        )

    def test_authentication_and_network_errors_do_not_fallback(self):
        for message in ["401 invalid API key", "connection timeout", "429 quota exceeded"]:
            with self.subTest(message=message):
                self.create.reset_mock()
                self.create.side_effect = RuntimeError(message)
                with self.assertRaisesRegex(RuntimeError, message):
                    engine.chat_completion(self.client, MODEL_ENGINE, [])
                self.assertEqual(self.create.call_count, 1)

    def test_json_extraction_and_json_mode(self):
        payload = {"red_units": [], "blue_units": [], "current_time": "秋"}
        raw = json.dumps(payload, ensure_ascii=False)
        for text in [raw, f"```json\n{raw}\n```", f"结果：{raw}\n结束"]:
            with self.subTest(text=text):
                self.create.return_value = response(text)
                self.assertEqual(engine.get_ai_response("场景", "test-key"), payload)
                self.assertEqual(
                    self.create.call_args.kwargs["response_format"], {"type": "json_object"}
                )
        for text in ["", "not JSON", '{"broken":}']:
            with self.subTest(text=text):
                self.create.return_value = response(text)
                with self.assertRaises(ValueError):
                    engine.get_ai_response("场景", "test-key")

    def test_coordinate_retry_normalizes_aliases_before_validation(self):
        payload = {"updated_state": {"red_units": [
            {"名称": "测试舰", "类型": "驱逐舰", "纬度": 30, "经度": 120}
        ], "blue_units": []}}
        with patch.object(engine, "get_ai_response", side_effect=[
            copy.deepcopy(payload), copy.deepcopy(payload)
        ]) as call, patch.object(engine, "get_coordinate_errors", side_effect=[
            ["测试舰在陆地"], []
        ]) as validate, patch.object(engine.st, "toast"):
            result = engine.get_ai_response_with_retry("场景", "test-key")
        self.assertEqual(result["updated_state"]["red_units"][0]["type"], "驱逐舰")
        self.assertEqual(validate.call_args_list[0].args[0]["red_units"][0]["name"], "测试舰")
        self.assertIn("测试舰在陆地", call.call_args_list[1].args[0])

    def test_coordinate_retry_limit_is_reported(self):
        payload = {"red_units": [], "blue_units": []}
        with patch.object(engine, "get_ai_response", return_value=payload) as call, \
                patch.object(engine, "get_coordinate_errors", return_value=["invalid position"]), \
                patch.object(engine.st, "toast"), patch.object(engine.st, "error") as error:
            self.assertEqual(engine.get_ai_response_with_retry("场景", "test-key", 3), payload)
        self.assertEqual(call.call_count, 3)
        error.assert_called_once()

    def test_review_and_chat_without_reasoning_field(self):
        self.create.return_value = response("复盘结论")
        self.assertEqual(engine.get_r1_strategic_review(["战报"], {}, "test-key"), ("", "复盘结论"))
        history = [{"role": "user", "content": f"问题{i}"} for i in range(8)]
        self.assertEqual(engine.ask_xuanji(["战报"], {}, "test-key", history), ("", "复盘结论"))
        messages = self.create.call_args.kwargs["messages"]
        self.assertIn("战报", messages[0]["content"])
        self.assertEqual(messages[1:], history[-5:])
        self.create.return_value = response(None, reasoning_content="思考")
        self.assertEqual(engine.ask_xuanji([], {}, "test-key", []), ("思考", "（璇玑未返回内容，请重试）"))


class ClientConnectionTests(unittest.TestCase):
    def tearDown(self):
        engine.get_client.clear()

    def test_https_proxy_ignores_unrelated_invalid_all_proxy(self):
        env = {"HTTPS_PROXY": "http://127.0.0.1:7897", "ALL_PROXY": "socks://127.0.0.1:7897"}
        with patch.dict(os.environ, env, clear=True):
            client = engine.get_client("offline-proxy-test")
            try:
                self.assertEqual(client.max_retries, 2)
                self.assertEqual(client.timeout.connect, 10)
                self.assertEqual(client.timeout.read, 180)
            finally:
                client.close()

    def test_no_proxy_selects_direct_connection(self):
        env = {"HTTPS_PROXY": "http://127.0.0.1:7897", "ALL_PROXY": "socks://127.0.0.1:7897",
               "NO_PROXY": "api.deepseek.com"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(engine, "DefaultHttpxClient", wraps=engine.DefaultHttpxClient) as transport:
            client = engine.get_client("offline-direct-test")
            try:
                self.assertIsNone(transport.call_args.kwargs["proxy"])
                self.assertFalse(transport.call_args.kwargs["trust_env"])
            finally:
                client.close()

    def test_sdk_retries_transient_connection_failure_on_same_model(self):
        calls = []

        def respond(request):
            calls.append(json.loads(request.content)["model"])
            if len(calls) < 3:
                raise httpx.ConnectError("test disconnection", request=request)
            return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
                "model": MODEL_ENGINE, "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": '{"red_units": [], "blue_units": []}'}}]})

        with OpenAI(api_key="offline-test-key", base_url="https://deepseek.invalid/v1", max_retries=2,
                    http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client, \
                patch.object(engine, "get_client", return_value=client), \
                patch("openai._base_client.time.sleep"):
            self.assertEqual(engine.get_ai_response("test", "offline-test-key"), {"red_units": [], "blue_units": []})
        self.assertEqual(calls, [MODEL_ENGINE] * 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
