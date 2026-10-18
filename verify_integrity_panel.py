"""🧪 名单/数值守恒面板的渲染验证（真实 Streamlit runtime，不烧 API Key）。

为什么要单独测渲染：`verify_integrity.py` 验的是纯函数（回填算得对不对），
`app.py` 里那几行接线只做了源码字符串检查 —— 那证明不了「面板真的会画出来」，
更证明不了「没有异常时它是安静的」。后者尤其重要：一个永远显示的警告
和一个永远不显示的警告，在人眼看来都很快被忽略。

跑法：
    <venv>/python.exe verify_integrity_panel.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

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
    ],
    "blue_units": [
        {"name": "秦军奇兵", "type": "骑兵", "lat": 35.8400, "lng": 112.9200,
         "hp": 5000, "combat_power": 70, "speed": 8, "attack_range": 300,
         "morale": 85, "supply": 85},
    ],
    "engagements": [],
}

RECOVERED_REPORT = {
    "recovered": [{"team": "red_units", "name": "赵军辎重营", "hp": 3000}],
    "eliminated": [],
    "suspected": [],
    "unnamed": 0,
    "duplicate": [],
    "prev_total": 3,
    "new_total": 3,
    "structure_missing": [],
    "note": "",
}


def build(**overrides):
    at = AppTest.from_file("app.py", default_timeout=120)
    at.session_state["war_state"] = {k: (v if not isinstance(v, list) else list(v))
                                     for k, v in FIXTURE.items()}
    at.session_state["turn_count"] = 3
    at.session_state["history"] = ["【T+3】双方在丹河两岸对峙。"]
    at.session_state["scenario_input"] = "推演长平之战"
    for key, value in overrides.items():
        at.session_state[key] = value
    at.run()
    return at


def bullets(at):
    """把所有 markdown / warning / caption 的文本拼起来，用于关键词断言。"""
    parts = []
    for collection in (at.markdown, at.warning, at.caption, at.info, at.error):
        try:
            parts += [el.value for el in collection]
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(str(p) for p in parts)


# ==========================================
print("=" * 62)
print("一、有回填时面板必须出现")
print("=" * 62)

at = build(roster_last=RECOVERED_REPORT, numeric_flags=[])
text = bullets(at)
check("无异常", not at.exception, at.exception)
check("出现回填警告", "凭空消失" in text, text[:300])
check("点名被回填的单位", "赵军辎重营" in text, text[:300])
check("解释「整体替换」这个原因", "整体替换" in text, text[:300])
check("提示歼灭要走 eliminated 声明", "eliminated" in text, text[:300])

# ==========================================
print()
print("=" * 62)
print("二、没有异常时必须安静（防「永远显示的警告」）")
print("=" * 62)

quiet = build(roster_last={"recovered": [], "eliminated": [], "suspected": [],
                           "unnamed": 0, "duplicate": [], "prev_total": 3,
                           "new_total": 3, "structure_missing": [], "note": ""},
              numeric_flags=[])
quiet_text = bullets(quiet)
check("无异常", not quiet.exception, quiet.exception)
check("不显示回填警告", "凭空消失" not in quiet_text, quiet_text[:300])
check("不显示数值提示", "数值守恒提示" not in quiet_text, quiet_text[:300])

empty = build(roster_last=None, numeric_flags=[])
empty_text = bullets(empty)
check("roster_last 为空也不崩", not empty.exception, empty.exception)
check("空状态同样不显示警告", "凭空消失" not in empty_text, empty_text[:300])

# ==========================================
print()
print("=" * 62)
print("三、数值异常与歼灭确认")
print("=" * 62)

at_gain = build(roster_last=RECOVERED_REPORT,
                numeric_flags=[{"kind": "hp_gain", "team": "red_units",
                                "name": "赵军主力", "prev": 8000.0, "new": 9000.0,
                                "delta": 1000.0, "ratio": 0.125}])
gain_text = bullets(at_gain)
check("hp 上涨提示出现", "数值守恒提示" in gain_text, gain_text[:300])
check("显示上涨前后数值", "8000" in gain_text and "9000" in gain_text, gain_text[:300])
check("明确说明没有自动修正", "没有自动改" in gain_text, gain_text[:300])

at_zero = build(roster_last=None,
                numeric_flags=[{"kind": "hp_zero_alive", "team": "red_units",
                                "name": "赵军辎重营", "prev": 3000.0, "new": 0}])
zero_text = bullets(at_zero)
check("hp 归零未移除被提示", "归零" in zero_text, zero_text[:300])

at_elim = build(roster_last={**RECOVERED_REPORT,
                             "recovered": [],
                             "eliminated": [{"team": "red_units",
                                             "name": "赵军辎重营",
                                             "why": "eliminated 列表内声明"}]},
                numeric_flags=[])
elim_text = bullets(at_elim)
check("确认歼灭用 caption 呈现", "确认歼灭" in elim_text, elim_text[:300])
check("确认歼灭不触发回填警告", "凭空消失" not in elim_text, elim_text[:300])

print()
print("=" * 62)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
if FAILURES:
    for line in FAILURES:
        print("  -", line)
print("=" * 62)
