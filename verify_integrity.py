# -*- coding: utf-8 -*-
"""名单守恒 + 数值守恒守卫的离线回归（零 API Key）。

每一组都配了「防恒真」对照：如果一个检查器永远返回"没问题"，
它和"模型永远守规矩"在输出上无法区分。所以这里既测**抓得到问题**，
也测**不误报正常情况**，还测**守卫本身不能吃掉合法歼灭**。
"""

import ast
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gentactix import integrity  # noqa: E402

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}  ← {detail}")


def unit(name, hp, lat=35.9, lng=112.85, typ="步兵"):
    return {"name": name, "type": typ, "lat": lat, "lng": lng,
            "hp": hp, "combat_power": hp // 100, "speed": 5,
            "attack_range": 300, "morale": 80, "supply": 70}


def make_state(red=None, blue=None, **extra):
    state = {
        "center": [35.83, 112.85],
        "current_time": "公元前260年 秋",
        "red_units": red if red is not None else [
            unit("赵军主力", 8000), unit("赵军辎重营", 3000), unit("赵军长平关守备", 2000)],
        "blue_units": blue if blue is not None else [
            unit("秦军主力", 9000), unit("秦军奇兵", 5000)],
        "current_terrain": "丘陵森林",
        "engagements": [],
    }
    state.update(extra)
    return state


def names(state, team="red_units"):
    return [u.get("name", "<无名字段>") for u in state[team]]


# ==========================================
print("=" * 66)
print("一、漏返回回填")
print("=" * 66)

prev = make_state()
# 模拟模型漏掉「赵军辎重营」
new = make_state(red=[unit("赵军主力", 7800), unit("赵军长平关守备", 2000)])
patched, rep = integrity.reconcile_roster(prev, new)

check("缺失的辎重营被回填", "赵军辎重营" in names(patched), names(patched))
check("回填保留上一回合的 hp（3000，不是归零）",
      [u for u in patched["red_units"] if u["name"] == "赵军辎重营"][0]["hp"] == 3000,
      [u for u in patched["red_units"] if u["name"] == "赵军辎重营"][0]["hp"])
check("回填保留上一回合的坐标",
      [u for u in patched["red_units"] if u["name"] == "赵军辎重营"][0]["lat"] == 35.9)
check("报告记录了 1 个回填单位", len(rep["recovered"]) == 1, rep["recovered"])
check("未消失的单位 hp 变化不被覆盖（7800 保持）",
      [u for u in patched["red_units"] if u["name"] == "赵军主力"][0]["hp"] == 7800)

# 防恒真对照：不调 reconcile 时，单位确实会丢
naive = make_state(red=[unit("赵军主力", 7800), unit("赵军长平关守备", 2000)])
check("对照组：不做回填时单位确实永久丢失（证明守卫不是空跑）",
      len(names(naive)) == 2 and len(names(prev)) == 3, names(naive))

# 整条列表缺失
new2 = make_state(red=None)
del new2["red_units"]
patched2, rep2 = integrity.reconcile_roster(prev, new2)
check("整条 red_units 缺失时全部回填", len(names(patched2)) == 3, names(patched2))
check("报告标记结构缺失", len(rep2["structure_missing"]) == 1, rep2["structure_missing"])

# 全部漏光
new3 = make_state(red=[], blue=[])
patched3, rep3 = integrity.reconcile_roster(prev, new3)
check("模型返回空列表时整局被恢复（5 个单位）",
      len(patched3["red_units"]) + len(patched3["blue_units"]) == 5,
      len(patched3["red_units"]) + len(patched3["blue_units"]))

# 幂等
patched_again, rep_again = integrity.reconcile_roster(prev, patched)
check("幂等：对已回填状态再跑一次，不再回填",
      rep_again["recovered"] == [], rep_again["recovered"])
check("幂等：单位数不变", len(names(patched_again)) == 3, names(patched_again))

# ==========================================
print()
print("=" * 66)
print("二、歼灭必须能被确认（守卫不能吃掉合法消失）")
print("=" * 66)

# 通道 1：eliminated 声明
new4 = make_state(red=[unit("赵军主力", 7000), unit("赵军长平关守备", 1800)],
                  blue=[unit("秦军主力", 8500), unit("秦军奇兵", 4800)],
                  eliminated=["赵军辎重营"])
patched4, rep4 = integrity.reconcile_roster(prev, new4)
check("eliminated 声明的单位允许消失", "赵军辎重营" not in names(patched4), names(patched4))
check("确认歼灭计入报告", len(rep4["eliminated"]) == 1, rep4["eliminated"])
check("确认歼灭不算回填", rep4["recovered"] == [], rep4["recovered"])

# 通道 2：上一回合 hp 已归零
prev_zero = make_state(red=[unit("赵军主力", 8000), unit("赵军残部", 0)],
                       blue=[unit("秦军主力", 9000)])
new5 = make_state(red=[unit("赵军主力", 7600)], blue=[unit("秦军主力", 8800)])
patched5, rep5 = integrity.reconcile_roster(prev_zero, new5)
check("上一回合 hp=0 的单位允许消失", "赵军残部" not in names(patched5), names(patched5))
check("确认理由为 hp 归零",
      rep5["eliminated"] and rep5["eliminated"][0]["why"] == "上一回合 hp 已归零",
      rep5["eliminated"])

# eliminated 里点名但单位其实还在 → 不误删
new6 = make_state(red=[unit("赵军主力", 7900), unit("赵军辎重营", 2900)],
                  blue=[unit("秦军主力", 8900), unit("秦军奇兵", 4900)],
                  eliminated=["赵军长平关守备"])
patched6, rep6 = integrity.reconcile_roster(prev, new6)
check("eliminated 点名不影响仍在列表里的单位",
      "赵军主力" in names(patched6) and "赵军辎重营" in names(patched6), names(patched6))
check("被点名且确实消失的才计入歼灭",
      len(rep6["eliminated"]) == 1 and rep6["eliminated"][0]["name"] == "赵军长平关守备",
      rep6["eliminated"])

# ==========================================
print()
print("=" * 66)
print("三、边界：空名 / 重名 / 极低 hp")
print("=" * 66)

prev_named = make_state(red=[unit("赵军主力", 8000), {"type": "步兵", "hp": 100}],
                        blue=[unit("秦军主力", 9000)])
new7 = make_state(red=[unit("赵军主力", 7900), {"type": "步兵", "hp": 100}],
                  blue=[unit("秦军主力", 8900)])
patched7, rep7 = integrity.reconcile_roster(prev_named, new7)
check("空名单位原样保留，不被回填也不被删（既不误回填、也不擅自删数据）",
      len(names(patched7)) == 2, names(patched7))
check("空名单位不触发任何回填", rep7["recovered"] == [], rep7["recovered"])
check("空名单位被计数", rep7["unnamed"] >= 2, rep7["unnamed"])

prev_dup = make_state(red=[unit("赵军主力", 8000), unit("赵军主力", 6000)],
                      blue=[unit("秦军主力", 9000)])
_, rep8 = integrity.reconcile_roster(prev_dup, copy.deepcopy(prev_dup))
check("重名被检出", any("赵军主力" in d for d in rep8["duplicate"]), rep8["duplicate"])

# 极低 hp 的消失 → 回填但提示人眼复核
prev_low = make_state(red=[unit("赵军主力", 8000), unit("赵军残部", 6)],
                      blue=[unit("秦军主力", 9000)])
new9 = make_state(red=[unit("赵军主力", 7600)], blue=[unit("秦军主力", 8700)])
patched9, rep9 = integrity.reconcile_roster(prev_low, new9)
check("hp=6 的消失仍回填（没有声明就不放过）",
      "赵军残部" in names(patched9), names(patched9))
check("但列入 suspected 供人眼复核", len(rep9["suspected"]) == 1, rep9["suspected"])

# ==========================================
print()
print("=" * 66)
print("四、数值守恒（只报告，不修正）")
print("=" * 66)

prev_n = make_state(red=[unit("赵军主力", 8000), unit("赵军辎重营", 3000)],
                    blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])

# hp 大涨
new_gain = make_state(red=[unit("赵军主力", 9000), unit("赵军辎重营", 3000)],
                      blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])
flags = integrity.check_numeric_integrity(prev_n, new_gain)
gain = [f for f in flags if f["kind"] == "hp_gain"]
check("hp 上涨 1000 被抓到", len(gain) == 1 and gain[0]["name"] == "赵军主力", flags)
check("报告带上涨幅比例", gain and abs(gain[0]["ratio"] - 0.125) < 1e-6, gain)
check("hp 没有真的被改（只报告）",
      [u for u in new_gain["red_units"] if u["name"] == "赵军主力"][0]["hp"] == 9000)

# 噪音阈值
new_noise = make_state(red=[unit("赵军主力", 8001), unit("赵军辎重营", 3000)],
                       blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])
flags_noise = integrity.check_numeric_integrity(prev_n, new_noise)
check("hp 上涨 1（0.0125%）不报，避免四舍五入噪音",
      [f for f in flags_noise if f["kind"] == "hp_gain"] == [], flags_noise)

# hp 下降不报
new_drop = make_state(red=[unit("赵军主力", 6000), unit("赵军辎重营", 2900)],
                      blue=[unit("秦军主力", 8500), unit("秦军奇兵", 5000)])
flags_drop = integrity.check_numeric_integrity(prev_n, new_drop)
check("hp 下降不误报", flags_drop == [], flags_drop)

# hp 归零仍在列表
new_zero = make_state(red=[unit("赵军主力", 0), unit("赵军辎重营", 3000)],
                      blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])
flags_zero = integrity.check_numeric_integrity(prev_n, new_zero)
check("hp 归零但仍在列表被抓到",
      any(f["kind"] == "hp_zero_alive" for f in flags_zero), flags_zero)

# hp 为负
new_neg = make_state(red=[unit("赵军主力", -500), unit("赵军辎重营", 3000)],
                     blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])
flags_neg = integrity.check_numeric_integrity(prev_n, new_neg)
check("hp 为负被抓到", any(f["kind"] == "hp_negative" for f in flags_neg), flags_neg)

# 新出现的单位不参与 hp 对比（无基准）
new_extra = make_state(red=[unit("赵军主力", 8000), unit("赵军辎重营", 3000),
                            unit("赵军援军", 4000)],
                       blue=[unit("秦军主力", 9000), unit("秦军奇兵", 5000)])
flags_extra = integrity.check_numeric_integrity(prev_n, new_extra)
check("新单位不误报 hp 上涨", flags_extra == [], flags_extra)

# ==========================================
print()
print("=" * 66)
print("五、接线与依赖")
print("=" * 66)

src = Path("gentactix/integrity.py").read_text(encoding="utf-8")
tree = ast.parse(src)
imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports += [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        imports.append(node.module or "")
check("integrity.py 不依赖 streamlit（可裸 Python 跑）",
      not any("streamlit" in (i or "") for i in imports), imports)
check("integrity.py 不依赖 folium", not any("folium" in (i or "") for i in imports), imports)

app_src = Path("app.py").read_text(encoding="utf-8")
check("app.py 已接入 reconcile_roster", "integrity.reconcile_roster" in app_src)
check("app.py 已接入 check_numeric_integrity", "integrity.check_numeric_integrity" in app_src)
# 用 AST 取 import 名字集合，而不是切文本的某一行 ——
# app.py 的 `from gentactix import (...)` 现在跨了两行，切第一行会漏掉 integrity。
_gentactix_imports = set()
for _node in ast.walk(ast.parse(app_src)):
    if isinstance(_node, ast.ImportFrom) and _node.module == "gentactix":
        _gentactix_imports |= {_alias.name for _alias in _node.names}
check("app.py 引入 integrity 模块", "integrity" in _gentactix_imports,
      sorted(_gentactix_imports))

prompt_src = Path("gentactix/prompts.py").read_text(encoding="utf-8")
check("prompt 已加入名单守恒规则（用唯一标记，避免「一个都不能少」这类误命中）",
      "【单位名单守恒规则】" in prompt_src)
check("prompt 说明漏返回会被系统回填", "回填" in prompt_src)
check("prompt 要求歼灭走 eliminated 声明", "eliminated" in prompt_src)

state_src = Path("gentactix/state.py").read_text(encoding="utf-8")
check("会话状态新增 roster_last", "roster_last" in state_src)
check("会话状态新增 numeric_flags", "numeric_flags" in state_src)

# 端到端：回填后的单位会出现在下一回合 prompt 里
from gentactix import prompts  # noqa: E402

prev_e2e = make_state()
new_e2e = make_state(red=[unit("赵军主力", 7800), unit("赵军长平关守备", 2000)])
patched_e2e, _ = integrity.reconcile_roster(prev_e2e, new_e2e)
prompt = prompts.build_step_prompt(patched_e2e, 2, "30分钟", False, "")
check("端到端：回填的单位出现在下一回合 prompt 中",
      "赵军辎重营" in prompt, "prompt 里找不到被回填的单位")

print()
print("=" * 66)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 66)
