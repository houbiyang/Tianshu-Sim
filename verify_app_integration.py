"""🧪 裁决器与 Streamlit 面板的集成验证（真实 runtime，不烧 API Key）。

跑法：
    <venv>/python.exe verify_app_integration.py

本版裁决器是 **opt-in（默认关闭）**，所以验证顺序也按这个来：

  0. 默认状态：开关必须是关的，面板不出现、提示语说明「两次结果不同」；
  1. 打开开关后，裁决面板真的渲染出来（标题 / 摘要 / 表格行数）；
  2. 再关回去，面板消失 —— 开关不是装饰；
  3. 点击「下一轮推演」时裁决器先于模型执行，且模型调用失败不会把整页炸掉。

第 0 组是本版新增的关键断言：默认值一旦被谁改回 True，
「不改用户现有体验」这个承诺就无声失效了，必须由测试钉住。
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
    "current_time": "公元前260年秋 某日黎明",
    "current_terrain": "丘陵森林",
    "red_units": [
        {"name": "赵军主力", "type": "步兵", "lat": 35.9006, "lng": 112.8556,
         "hp": 8000, "combat_power": 60, "speed": 5, "attack_range": 200,
         "morale": 80, "supply": 80, "supply_node_id": "zhao-rear"},
        {"name": "赵军辎重营", "type": "步兵", "lat": 35.7876, "lng": 112.9790,
         "hp": 3000, "combat_power": 40, "speed": 5, "attack_range": 200,
         "morale": 70, "supply": 90},
    ],
    "blue_units": [
        {"name": "秦军奇兵", "type": "骑兵", "lat": 35.8400, "lng": 112.9200,
         "hp": 5000, "combat_power": 70, "speed": 8, "attack_range": 300,
         "morale": 85, "supply": 70},
    ],
    "engagements": [],
    # The supply source is explicit: an arbitrary friendly unit is not a depot.
    "supply_nodes": [{"id": "zhao-rear", "team": "red", "lat": 35.7876,
                      "lng": 112.9790, "source": "测试设定"}],
    "situation_report": "两军在丹河两岸对峙。",
    "source_note": "考据：兵力数字系推演。",
}


def make_app():
    """建一个带固定战局的 AppTest，并跑第一次（拿到可操作的 element tree）。"""
    at = AppTest.from_file("app.py", default_timeout=180)
    at.session_state["war_state"] = FIXTURE
    at.session_state["scenario_input"] = "推演长平之战"
    at.session_state["turn_count"] = 1
    at.session_state["history"] = ["【T+1】测试"]
    at.run()
    return at


def toggle_index(at_obj):
    labels = [t.label for t in at_obj.sidebar.toggle]
    try:
        return next(i for i, label in enumerate(labels) if "确定性裁决" in label)
    except StopIteration:
        return None


def set_adjudicator(at_obj, want):
    idx = toggle_index(at_obj)
    if idx is None:
        return at_obj
    at_obj.sidebar.toggle[idx].set_value(want)
    at_obj.run()
    return at_obj


at0 = make_app()
at0.run()

print("=" * 60)
print("0. 默认状态：开关关闭，且界面说实话")
print("=" * 60)
check("默认状态无异常", not at0.exception, at0.exception)
idx0 = toggle_index(at0)
check("侧栏存在裁决开关", idx0 is not None, [t.label for t in at0.sidebar.toggle])
check("开关默认值为 False（opt-in）",
      idx0 is not None and at0.sidebar.toggle[idx0].value is False,
      at0.sidebar.toggle[idx0].value if idx0 is not None else "无开关")
md0 = "\n".join(m.value for m in at0.markdown)
cap0 = "\n".join(c.value for c in at0.caption)
check("默认状态不出现裁决面板", "确定性裁决" not in md0, md0[:200])
check("默认状态说明「两次结果不同」", "两次运行结果不同" in cap0, cap0[:200])

print("=" * 60)
print("1. 打开开关 → 裁决面板渲染")
print("=" * 60)
at = set_adjudicator(make_app(), True)
check("无异常", not at.exception, at.exception)
md = "\n".join(m.value for m in at.markdown)
cap = "\n".join(c.value for c in at.caption)
check("面板标题出现", "确定性裁决" in md, md[:200])
check("摘要行出现", "粮道被断" in cap, cap[:200])
check("表格已渲染", len(at.dataframe) >= 1, len(at.dataframe))
# 按列名找单位表，而不是取第 0 张 —— 面板以后再加别的表（例如「几何尺度明细」），
# 取索引会张冠李戴，而且失败信息会指向错误的地方。
_unit_tables = [d.value for d in at.dataframe if {"单位", "补给"}.issubset(d.value.columns)]
check("单位表已渲染", bool(_unit_tables), [list(d.value.columns) for d in at.dataframe])
check("人员战损表已渲染", any("战斗减员率" in d.value.columns for d in at.dataframe))
if _unit_tables:
    df = _unit_tables[0]
    check("表格行数 = 3（三支部队）", len(df) == 3, len(df))
    check("表格含补给列", "补给" in list(df.columns), list(df.columns))
    check("赵军主力粮道标为已切断",
          df[df["单位"] == "赵军主力"]["补给"].iloc[0] == "已切断",
          df[df["单位"] == "赵军主力"]["补给"].iloc[0])
check("面板显示本次生效的裁决参数来源", "参数：" in cap, cap[-300:])
check("侧栏出现「战场裁决库」选择器",
      any("战场裁决库" in getattr(s, "label", "") for s in at.sidebar.selectbox),
      [getattr(s, "label", "") for s in at.sidebar.selectbox])
check("裁决口径折叠面板存在", any("裁决口径" in e.label for e in at.expander),
      [e.label for e in at.expander])

print("=" * 60)
print("2. 关回开关 → 面板消失")
print("=" * 60)
at2 = set_adjudicator(make_app(), True)
set_adjudicator(at2, False).run()
md2 = "\n".join(m.value for m in at2.markdown)
cap2 = "\n".join(c.value for c in at2.caption)
check("无异常", not at2.exception, at2.exception)
check("面板已消失", "确定性裁决" not in md2, md2[:200])
check("提示语说明不可复现", "两次运行结果不同" in cap2, cap2[:200])

print("=" * 60)
print("3. 点击「下一轮推演」：裁决器先跑，模型失败不炸页")
print("=" * 60)
at3 = set_adjudicator(make_app(), True)
# 填入一个格式合法但无效的 Key：模型调用会失败，但裁决器在它之前执行
at3.sidebar.text_input[0].set_value("sk-invalid-for-offline-test")
btn = next(b for b in at3.sidebar.button if "下一轮" in b.label)
btn.click().run()
check("点击后无未捕获异常", not at3.exception, at3.exception)
errors = [e.value for e in at3.error]
warns = [w.value for w in at3.warning]
check("模型失败被捕获为界面错误而非崩溃",
      bool(errors) or bool(warns), (errors[:1], warns[:1]))
md3 = "\n".join(m.value for m in at3.markdown)
check("裁决面板仍在地图下方", "确定性裁决" in md3)

print()
print("=" * 60)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
for item in FAILURES:
    print(f"  ✗ {item}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
