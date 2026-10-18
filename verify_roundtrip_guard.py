"""🔁 端到端：真实回合流程里模型漏返回时，单位是否真的被保住。

前面的 `verify_integrity.py` 验纯函数、`verify_integrity_panel.py` 验渲染，
都绕过了「点下一轮」这条真实路径。而这个 bug 恰恰出在路径上：
`war_state = updated_data` 是整体替换，守卫必须插在**赋值之前**才有用。
放在渲染之后、或者放在下一回合开头，都救不回来。

做法：把 `engine.get_ai_response_with_retry` 打桩成返回一个「漏了一个单位、
还把另一个单位 hp 写高」的假响应，然后点真正的「下一轮推演」按钮。
全程不联网、不烧 Key。

跑法：
    <venv>/python.exe verify_roundtrip_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

from gentactix import engine  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        FAILURES.append(f"{label} :: {detail}")
        print(f"  [FAIL] {label}  <-- {detail}")


FIXTURE = {
    "center": [35.83, 112.85],
    "current_time": "公元前260年秋 某日黎明",
    "current_terrain": "丘陵森林",
    "red_units": [
        {"name": "赵军主力", "type": "步兵", "lat": 35.9006, "lng": 112.8556,
         "hp": 8000, "combat_power": 60, "speed": 5, "attack_range": 200,
         "morale": 80, "supply": 80},
        {"name": "赵军辎重营", "type": "步兵", "lat": 35.7876, "lng": 112.9790,
         "hp": 3000, "combat_power": 40, "speed": 5, "attack_range": 200,
         "morale": 70, "supply": 90},
        {"name": "赵军长平关守备", "type": "步兵", "lat": 35.9694, "lng": 112.7806,
         "hp": 2000, "combat_power": 30, "speed": 5, "attack_range": 200,
         "morale": 75, "supply": 40},
    ],
    "blue_units": [
        {"name": "秦军奇兵", "type": "骑兵", "lat": 35.8400, "lng": 112.9200,
         "hp": 5000, "combat_power": 70, "speed": 8, "attack_range": 300,
         "morale": 85, "supply": 85},
    ],
    "engagements": [],
}

# 假响应故意犯两个错：
#   1. 漏掉「赵军辎重营」—— 整体替换会把它永久抹掉；
#   2. 把「赵军主力」从 8000 写成 9000 —— 兵力凭空上涨。
FAKE_RESPONSE = {
    "updated_state": {
        "center": [35.83, 112.85],
        "current_time": "公元前260年秋 次日",
        "era": "战国",
        "red_units": [
            {"name": "赵军主力", "type": "步兵", "lat": 35.9006, "lng": 112.8556,
             "hp": 9000, "combat_power": 60, "speed": 5, "attack_range": 200,
             "morale": 80, "supply": 70},
            {"name": "赵军长平关守备", "type": "步兵", "lat": 35.9694, "lng": 112.7806,
             "hp": 1900, "combat_power": 30, "speed": 5, "attack_range": 200,
             "morale": 75, "supply": 40},
        ],
        "blue_units": [
            {"name": "秦军奇兵", "type": "骑兵", "lat": 35.8400, "lng": 112.9200,
             "hp": 4900, "combat_power": 70, "speed": 8, "attack_range": 300,
             "morale": 85, "supply": 85},
        ],
        "current_terrain": "丘陵森林",
        "engagements": [],
    },
    "report": "【T+2 | 秋】秦军奇兵自西侧插入，赵军辎重与主力之间的联系被压窄，"
              "关内粮秣转运已不如前日顺畅。赵括仍在催促前军东进，尚未察觉退路的风险。",
    "source_note": "兵力数字无出处，系推演。",
}

_original = engine.get_ai_response_with_retry
CALLED = {"n": 0}


def fake_response(prompt, api_key, max_retries=None):
    CALLED["n"] += 1
    CALLED["prompt"] = prompt
    return FAKE_RESPONSE


engine.get_ai_response_with_retry = fake_response

try:
    print("=" * 62)
    print("端到端：点「下一轮推演」，模型漏返回一个单位")
    print("=" * 62)

    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["war_state"] = {k: (v if not isinstance(v, list) else list(v))
                                     for k, v in FIXTURE.items()}
    at.session_state["scenario_input"] = "推演长平之战"
    at.session_state["turn_count"] = 1
    at.session_state["history"] = ["【T+1】双方对峙"]
    at.run()

    at.sidebar.text_input[0].set_value("sk-stub-offline")
    btn = next(b for b in at.sidebar.button if "下一轮" in b.label)
    btn.click().run()

    check("模型桩被调用了一次", CALLED["n"] == 1, CALLED["n"])
    check("页面无未捕获异常", not at.exception, at.exception)

    war = at.session_state["war_state"]
    red_names = [u.get("name") for u in war.get("red_units", [])]
    blue_names = [u.get("name") for u in war.get("blue_units", [])]

    check("漏返回的辎重营仍在战局里（没有被整体替换抹掉）",
          "赵军辎重营" in red_names, red_names)
    check("红方仍是 3 个单位", len(red_names) == 3, red_names)
    check("蓝方没有被动过", blue_names == ["秦军奇兵"], blue_names)

    recovered = war["red_units"][-1]
    check("回填的是上一回合的快照（hp 3000，不是 0）",
          recovered.get("hp") == 3000, recovered.get("hp"))
    check("回填保留原坐标", recovered.get("lat") == 35.7876, recovered.get("lat"))

    roster = at.session_state["roster_last"] or {}
    check("roster_last 记录了这次回填",
          any(e["name"] == "赵军辎重营" for e in roster.get("recovered", [])),
          roster.get("recovered"))

    flags = at.session_state["numeric_flags"] or []
    check("hp 上涨被标记",
          any(f["kind"] == "hp_gain" and f["name"] == "赵军主力" for f in flags), flags)
    check("hp 没有被自动改回 8000（只报告不修正）",
          [u for u in war["red_units"] if u["name"] == "赵军主力"][0]["hp"] == 9000,
          [u for u in war["red_units"] if u["name"] == "赵军主力"][0]["hp"])

    # 面板
    texts = []
    for collection in (at.markdown, at.warning, at.caption, at.info):
        try:
            texts += [str(el.value) for el in collection]
        except Exception:  # noqa: BLE001
            continue
    blob = "\n".join(texts)
    check("界面出现回填警告", "凭空消失" in blob, blob[:300])
    check("界面点名辎重营", "赵军辎重营" in blob, blob[:300])
    check("界面出现数值守恒提示", "数值守恒提示" in blob, blob[:400])

    # 下一回合的 prompt 必须带上被回填的单位 —— 这是「救回来了」的最终判据。
    # 注意不要写 `... or True` 这种恒真兜底：那样的断言永远通过，等于没写。
    from gentactix import prompts  # noqa: E402
    nxt = prompts.build_step_prompt(war, 3, "30分钟", False, "")
    check("回填单位会进入下一回合 prompt（不再永久消失）",
          "赵军辎重营" in nxt, "prompt 中缺失")

finally:
    engine.get_ai_response_with_retry = _original

print()
print("=" * 62)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
for item in FAILURES:
    print("  -", item)
print("=" * 62)
sys.exit(1 if FAIL else 0)
