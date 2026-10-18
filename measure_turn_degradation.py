# -*- coding: utf-8 -*-
"""回合数与上下文/状态保真度的离线实测（零 API Key）。

回答三个问题，各用一条可数的证据：

Q1 单回合推演的 prompt 会随回合数变长吗？
   → 直接比较 T=1 / 10 / 50 / 100 的 build_step_prompt 字符数。

Q2 复盘 / 问答的 prompt 会随回合数变长吗？长到什么时候危险？
   → 用真实战报长度模拟 history 累积，画增长曲线。

Q3 回合推移会让哪些东西丢？模型漏返回一个单位会怎样？
   → 模拟「每回合漏掉 3% 单位」，看 20 / 50 回合后还剩几个；
     再检查代码里是否存在「上回合单位必须全在」的守卫。
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gentactix import adjudicator, prompts, state  # noqa: E402

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}  ← {detail}")


# ==========================================
# 构造一份真实体量的长平战局（12 单位，与初始化产出同规模）
# ==========================================
ANCHORS = [
    ("赵军主力", "步兵", 35.9006, 112.8556, 8000),
    ("赵军前军", "步兵", 35.9200, 112.8300, 6000),
    ("赵军左翼", "骑兵", 35.9300, 112.8900, 4000),
    ("赵军右翼", "骑兵", 35.8900, 112.9000, 4000),
    ("赵军辎重营", "步兵", 35.7876, 112.9790, 3000),
    ("赵军长平关守备", "步兵", 35.9694, 112.7806, 2000),
    ("秦军主力", "步兵", 35.7306, 112.7348, 9000),
    ("秦军奇兵", "骑兵", 35.8400, 112.9200, 5000),
    ("秦军左军", "步兵", 35.7600, 112.8000, 6000),
    ("秦军右军", "步兵", 35.7500, 112.8800, 6000),
    ("秦军弩兵", "弓弩", 35.7400, 112.8400, 3000),
    ("秦军斥候", "骑兵", 35.8000, 112.8600, 1000),
]


def build_state(turn, drop=()):
    red, blue = [], []
    for name, typ, lat, lng, hp in ANCHORS:
        if name in drop:
            continue
        unit = {
            "name": name, "type": typ, "lat": lat, "lng": lng,
            "hp": hp, "combat_power": round(hp / 100), "speed": 5,
            "attack_range": 300, "morale": 80, "supply": 70,
        }
        (red if name.startswith("赵") else blue).append(unit)
    return {
        "center": [35.83, 112.85],
        "current_time": f"第 {turn} 回合 06:00",
        "era": "战国",
        "red_units": red,
        "blue_units": blue,
        "current_terrain": "丘陵森林",
        "engagements": [],
    }


def brief(text, n):
    """模拟一条战报：正文 150—200 字 + 考据 30—60 字。"""
    body = (text * 40)[:175]
    return state.compose_report_entry(body, "《史记·白起王翦列传》载长平之事，兵力数字学界有争议。")


# ==========================================
print("=" * 66)
print("Q1 单回合推演 prompt 是否随回合数变长")
print("=" * 66)
rows = []
for turn in (1, 10, 50, 100, 300):
    war = build_state(turn)
    adj = adjudicator.adjudicate(war, "30分钟", turn=turn)
    p = prompts.build_step_prompt(war, turn, "30分钟", False, "史料摘录占位", adjudication=adj)
    rows.append((turn, len(p), len(adj["digest"])))
    print(f"  第 {turn:>3} 回合：prompt {len(p):>6} 字符（其中裁决块 {len(adj['digest']):>4}）")

sizes = [r[1] for r in rows]
spread = max(sizes) - min(sizes)
check("单回合 prompt 长度几乎恒定（波动 < 200 字符）", spread < 200, f"波动 {spread} 字符")
check("第 300 回合的 prompt 仍只占 64K 的一成左右（<15%）",
      sizes[-1] < 64 * 1024 * 0.15, f"{sizes[-1] / (64 * 1024) * 100:.1f}%")

# ==========================================
print()
print("=" * 66)
print("Q2 复盘 / 问答 prompt 随回合数增长")
print("=" * 66)
print(f"  {'回合':>5} {'history 字符':>12} {'复盘 prompt':>12} {'占 64K':>8}")
for turns in (5, 10, 20, 30, 50, 100):
    hist = [brief(f"T+{i} 赵军与秦军在丹河两岸对峙，", 10) for i in range(turns)]
    war = build_state(turns)
    # 复盘的拼装方式与 engine.get_r1_strategic_review 一致
    review_prompt = (json.dumps(hist, ensure_ascii=False, indent=2)
                     + json.dumps(war, ensure_ascii=False, indent=2))
    pct = len(review_prompt) / (64 * 1024) * 100
    print(f"  {turns:>5} {len(json.dumps(hist, ensure_ascii=False)):>12} "
          f"{len(review_prompt):>12} {pct:>7.1f}%")

# 找越界点：按每回合约 240 字符的战报累积，算多少回合撑满 64K
per_turn = len(json.dumps([brief("x" * 10, 10)], ensure_ascii=False))
limit_turn = int(64 * 1024 / per_turn)
print(f"\n  单条战报约 {per_turn} 字符 ⇒ 复盘约第 {limit_turn} 回合撞满 64K 上下文")
check("复盘在第 50 回合仍有余量（占 64K < 40%）",
      len(json.dumps([brief("x" * 10, 10) for _ in range(50)], ensure_ascii=False)
          + json.dumps(build_state(50), ensure_ascii=False)) < 64 * 1024 * 0.4,
      "已超 40%")

# ==========================================
print()
print("=" * 66)
print("Q3 模型漏返回单位 ⇒ 是否永久丢失")
print("=" * 66)


def simulate_turns(total_turns, drop_rate, seed=7):
    """模拟：模型每回合以 drop_rate 概率漏掉某个仍存活的单位。

    关键：漏掉之后**永久消失**，不再复活 —— 因为 app.py 里是
    `st.session_state.war_state = updated_data`，整体替换，
    下一回合的 prompt 里根本不会出现这个名字，模型也就没有机会补回来。
    """
    import random
    rng = random.Random(seed)
    alive = {name for name, *_ in ANCHORS}
    lost_turn = {}
    for t in range(1, total_turns + 1):
        missing = {n for n in alive if rng.random() < drop_rate}
        for n in missing:
            lost_turn[n] = t
        alive -= missing
    return alive, lost_turn


for rate in (0.02, 0.05):
    for turns in (10, 20, 50):
        alive, lost = simulate_turns(turns, rate)
        first = min(lost.values()) if lost else None
        print(f"  漏返回率 {rate:.0%} × {turns:>2} 回合 ⇒ 存活 {len(alive):>2}/12 单位，"
              f"累计丢失 {12 - len(alive):>2}"
              + (f"，首次丢失在第 {first} 回合" if first else ""))

alive20, _ = simulate_turns(20, 0.02)
check("20 回合 × 2% 漏返回会真的丢单位（不是理论假设）", len(alive20) < 12, len(alive20))

src = Path("app.py").read_text(encoding="utf-8")
src += Path("gentactix/geo.py").read_text(encoding="utf-8")
src += Path("gentactix/state.py").read_text(encoding="utf-8")
src += Path("gentactix/integrity.py").read_text(encoding="utf-8")
src += Path("gentactix/prompts.py").read_text(encoding="utf-8")
guards = {
    "名单守恒守卫 reconcile_roster": "reconcile_roster" in src,
    "数值守恒检查 check_numeric_integrity": "check_numeric_integrity" in src,
    "prompt 要求歼灭走 eliminated 声明": "【单位名单守恒规则】" in src,
}
for label, present in guards.items():
    print(f"  {'有' if present else '无'}：{label}")
# 断言方向说明：守卫落地**之前**这两条是「确认缺口存在」（not present 才算通过）；
# 落地之后翻转为「确认守卫已接线」。方向变了必须写清楚，
# 否则下一个人看到 not 变 present 会以为这里被改坏了。
check("名单守恒守卫已接线（落地前此断言方向相反：确认无守卫）",
      guards["名单守恒守卫 reconcile_roster"], guards)
check("数值守恒检查已接线（落地前此断言方向相反：确认无守卫）",
      guards["数值守恒检查 check_numeric_integrity"], guards)

print()
print("=" * 66)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 66)
