"""Verify the real DeepSeek workflow without saving credentials.

Run: .venv/bin/python live_workflow_probe.py
The key is read from DEEPSEEK_API_KEY or a hidden terminal prompt. Real calls
incur API usage. Results and an exported battle are saved under .cache/qa/.
"""

import argparse
import getpass
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from gentactix import engine

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="长平之战，公元前260年；以军事历史教学为目的展示秦赵双方的部署与地理约束。")
    args = parser.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or getpass.getpass("DeepSeek API key (not saved): ").strip()
    if not key:
        print("No key supplied.")
        return 2

    output = ROOT / ".cache" / "qa"
    output.mkdir(parents=True, exist_ok=True)
    result = {"tested_at": datetime.now(timezone.utc).isoformat(), "live_model_calls": True,
              "scenario": args.scenario, "checks": [], "completions": []}

    def redact(value):
        return str(value).replace(key, "[REDACTED]")

    def check_stage(name, function):
        started = time.monotonic()
        print(f"START {name}", flush=True)
        try:
            detail = function()
            result["checks"].append({"name": name, "passed": True, "detail": detail,
                                     "seconds": round(time.monotonic() - started, 2)})
            print(f"PASS {name}: {json.dumps(detail, ensure_ascii=False)}", flush=True)
        except Exception as exc:
            result["checks"].append({"name": name, "passed": False, "error": redact(exc),
                                     "seconds": round(time.monotonic() - started, 2)})
            raise

    def save_result():
        result["passed"] = (bool(result["checks"]) and not result.get("fatal_error")
                            and all(c["passed"] for c in result["checks"]))
        # Never include widgets/session dumps: the password field contains the key.
        (output / "live-workflow-results.json").write_text(
            redact(json.dumps(result, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")

    client = None
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
    original_completion = engine.chat_completion

    def track_completion(*call_args, **kwargs):
        response = original_completion(*call_args, **kwargs)
        usage = getattr(response, "usage", None)
        result["completions"].append({"model": response.model,
            "usage": usage.model_dump() if usage else None})
        return response

    def run_app():
        app.run()
        assert not list(app.exception), redact([x.value for x in app.exception])
        assert not list(app.error), redact([x.value for x in app.error])

    def click(label):
        next(button for button in app.button if label in button.label).click()
        run_app()

    def validate_battle():
        battle = app.session_state["war_state"]
        assert isinstance(battle, dict), "Missing battle state"
        units = battle.get("red_units", []) + battle.get("blue_units", [])
        assert battle.get("red_units") and battle.get("blue_units"), "Both teams must exist"
        for unit in units:
            lat, lng = float(unit["lat"]), float(unit["lng"])
            assert math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180
        return {"turn": app.session_state["turn_count"], "units": len(units),
                "reports": len(app.session_state["history"])}

    try:
        client = engine.get_client(key)

        def list_models():
            names = sorted(model.id for model in client.models.list().data)
            result["available_models"] = names
            return {"models": names}

        check_stage("authentication_and_models", list_models)
        with patch.object(engine, "chat_completion", side_effect=track_completion), \
                patch.object(st, "download_button", wraps=st.download_button) as download:
            def initialize():
                run_app()
                app.text_input[0].set_value(key)
                app.text_area[0].set_value(args.scenario)
                for toggle in app.toggle:
                    if "启用史料" in toggle.label or "启用确定性" in toggle.label:
                        toggle.set_value(True)
                app.select_slider[0].set_value("1小时")
                run_app()
                click("初始化沙盘")
                assert app.session_state["rag_last"]["injected"] > 0
                assert app.session_state["adj_last"] is not None
                assert download.called, "Export unavailable after initialization"
                return validate_battle()

            check_stage("initialize_with_rag_and_adjudication", initialize)

            def advance():
                click("下一轮推演")
                detail = validate_battle()
                assert detail["turn"] == 1 and detail["reports"] == 2
                detail["movement_violations"] = len(app.session_state["adj_violations"])
                detail["numeric_flags"] = len(app.session_state["numeric_flags"])
                result["adjudication_violations"] = app.session_state["adj_violations"]
                result["numeric_flags"] = app.session_state["numeric_flags"]
                return detail

            check_stage("advance_turn", advance)

            def review():
                click("呼叫总参谋长")
                text = app.session_state["review_text"]
                assert text and len(text) > 40
                return {"characters": len(text), "has_reasoning": bool(app.session_state["review_reasoning"])}

            check_stage("strategic_review", review)

            def chat():
                app.chat_input[0].set_value("请从历史教学角度解释本局地形与补给对态势的影响，简要回答。")
                run_app()
                messages = app.session_state["chat_messages"]
                assert len(messages) == 2 and messages[-1]["role"] == "assistant" and messages[-1]["content"]
                return {"messages": len(messages), "answer_characters": len(messages[-1]["content"])}

            check_stage("contextual_chat", chat)

            def export():
                payload = json.loads(download.call_args.kwargs["data"])
                assert payload["turn_count"] == 1
                assert len(payload["history_logs"]) == 2
                assert payload["review"]["conclusion"] == app.session_state["review_text"]
                assert len(payload["chat_messages"]) == 2
                raw = json.dumps(payload, ensure_ascii=False, indent=2)
                assert key not in raw, "A credential was included in the export"
                (output / "live-battle.json").write_text(raw + "\n", encoding="utf-8")
                return {"file": ".cache/qa/live-battle.json", "contains_current_review_and_chat": True}

            check_stage("current_json_export", export)
    except Exception as exc:
        result["fatal_error"] = redact(exc)
        print(f"FAIL {redact(type(exc).__name__)}: {redact(exc)}", flush=True)
        return 1
    finally:
        if client is not None:
            client.close()
            engine.get_client.clear(key)
        engine._MODEL_DEGRADED.clear()
        save_result()
        print("Results: .cache/qa/live-workflow-results.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
