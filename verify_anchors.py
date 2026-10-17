"""长平锚点库离线校验：结构、范围、点名匹配、端到端吸附。

不联网、不烧 API Key。跑法：
    python verify_anchors.py
"""

import math
import sys

from gentactix import anchors, geo

PASS = FAIL = 0
FAILS = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{label} | {detail}")


# ==========================================
# 1. 结构校验：所有战区的锚点必须是 [lat, lng] 且范围合法
# ==========================================
for region, nodes in anchors.STRATEGIC_NODES.items():
    check(f"{region} 是字典", isinstance(nodes, dict), type(nodes))
    for name, coords in nodes.items():
        ok = (isinstance(coords, (list, tuple)) and len(coords) == 2
              and all(isinstance(v, (int, float)) for v in coords)
              and -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180)
        check(f"{region}/{name} 坐标合法", ok, coords)

# 长平锚点必须带分段前缀：match_anchor 取最后一段做核心短名，命名不规范会串台
for name in anchors.STRATEGIC_NODES["长平"]:
    check(f"长平锚点 {name} 含分段前缀", "-" in name, name)

# 同一战区内核心短名不得重复（否则后一个永远匹配不到）
for region, nodes in anchors.STRATEGIC_NODES.items():
    cores = [n.split("-")[-1] for n in nodes]
    check(f"{region} 核心短名唯一", len(cores) == len(set(cores)), cores)

# ==========================================
# 2. 命中校验：长平之战必须解锁 5 个锚点
# ==========================================
cp = anchors.collect_available_nodes("推演长平之战，赵括代廉颇")
check("长平之战 命中 5 个锚点", len(cp) == 5, list(cp))
check("长平之战 不夹带其他战区", all(k.startswith("长平-") for k in cp), list(cp))
check("不相关输入不解锁", anchors.collect_available_nodes("滑铁卢战役") == {}, "应返回空")

# 高平盆地外包框（文献：北纬 35°39′—35°59′，东经 112°42′—113°09′）
LAT_MIN, LAT_MAX = 35.65, 35.99
LNG_MIN, LNG_MAX = 112.70, 113.15
for name, (lat, lng) in cp.items():
    inside = LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX
    check(f"{name} 落在高平盆地", inside, f"({lat}, {lng})")

# ==========================================
# 3. 互斥校验：锚点两两不得重合，否则吸附失去意义
# ==========================================
def km(a, b):
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


names = list(cp)
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        d = km(cp[names[i]], cp[names[j]])
        check(f"{names[i]} ↔ {names[j]} 间距 > 1km", d > 1.0, f"{d:.2f} km")

# ==========================================
# 4. 点名匹配矩阵：单位名 → 期望锚点（核心短名）
# ==========================================
cases = [
    ("赵军长平关守备营", "长平关"),
    ("长平村前哨", "长平村"),
    ("赵军大粮山辎重营", "大粮山"),
    ("韩王山赵括幕府", "韩王山"),
    ("秦军空仓岭前部", "空仓岭"),
    ("赵军前锋营", None),           # 泛化名，不该被吸到任何点
    ("秦军主力", None),
    ("", None),                     # 空名：曾经的“吸附到第一个锚点”缺陷
    (None, None),
]
for unit_name, expect in cases:
    got_name, _ = geo.match_anchor(unit_name, cp)
    got = got_name.split("-")[-1] if got_name else None
    check(f"吸附「{unit_name}」→ {expect}", got == expect, f"实际 {got}")

# 长平关 与 长平村 不得互相串台（核心短名前缀重叠的经典坑）
got_longpingguan, _ = geo.match_anchor("长平关守军", cp)
check("「长平关守军」不落到长平村", got_longpingguan.endswith("长平关"), got_longpingguan)

# ==========================================
# 5. 端到端：中文键的初始阵型经 sanitize 后落到正确锚点
# ==========================================
init_state = {
    "red_units": [
        {"名称": "赵军长平关守备营", "类型": "步兵", "lat": 0.0, "lng": 0.0,
         "hp": 1000, "combat_power": 60, "speed": 4, "attack_range": 200,
         "morale": 80, "supply": 70},
        {"名称": "赵军大粮山辎重营", "类型": "步兵", "lat": 0.0, "lng": 0.0,
         "hp": 800, "combat_power": 40, "speed": 3, "attack_range": 150,
         "morale": 75, "supply": 95},
        {"名称": "赵军游骑", "类型": "骑兵", "lat": 35.80, "lng": 112.90,
         "hp": 500, "combat_power": 70, "speed": 8, "attack_range": 300,
         "morale": 85, "supply": 80},
    ],
    "blue_units": [
        {"名称": "秦军空仓岭前部", "类型": "步兵", "lat": 0.0, "lng": 0.0,
         "hp": 1200, "combat_power": 75, "speed": 4, "attack_range": 250,
         "morale": 90, "supply": 85},
    ],
}

# st.toast 在 bare 模式下无 ScriptRunContext，打桩即可
import streamlit as st
st.toast = lambda *a, **k: None

geo.sanitize_init_units(init_state, cp)

expect_pos = {
    "赵军长平关守备营": cp["长平-赵军防线-长平关"],
    "赵军大粮山辎重营": cp["长平-赵军后方-大粮山"],
    "秦军空仓岭前部": cp["长平-秦军进攻轴线-空仓岭"],
}
for unit in init_state["red_units"] + init_state["blue_units"]:
    name = unit.get("name")
    if name in expect_pos:
        exp = expect_pos[name]
        got = (unit["lat"], unit["lng"])
        check(f"{name} 吸附到锚点", got == (exp[0], exp[1]), f"{got} vs {exp}")

# 泛化名单位必须保留 AI 原坐标，不得被锚点吞掉
free = [u for u in init_state["red_units"] if u["name"] == "赵军游骑"][0]
check("「赵军游骑」保留原坐标", (free["lat"], free["lng"]) == (35.80, 112.90),
      (free["lat"], free["lng"]))

# ==========================================
# 6. 回归：原有三个战区不得被改动
# ==========================================
check("珍珠港仍 9 个锚点", len(anchors.STRATEGIC_NODES["珍珠港"]) == 9,
      len(anchors.STRATEGIC_NODES["珍珠港"]))
check("波斯湾仍 5 个锚点", len(anchors.STRATEGIC_NODES["波斯湾"]) == 5,
      len(anchors.STRATEGIC_NODES["波斯湾"]))
check("中途岛仍 1 个锚点", len(anchors.STRATEGIC_NODES["中途岛"]) == 1,
      len(anchors.STRATEGIC_NODES["中途岛"]))
check("马六甲仍 2 个锚点", len(anchors.STRATEGIC_NODES["马六甲"]) == 2,
      len(anchors.STRATEGIC_NODES["马六甲"]))
check("珍珠港锚点仍可命中",
      geo.match_anchor("亚利桑那号战列舰", anchors.collect_available_nodes("珍珠港事件"))[0]
      == "福特岛-战列舰大街-亚利桑那号")

print(f"\n{'=' * 46}")
print(f"PASS {PASS} / FAIL {FAIL}")
if FAILS:
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
print("长平锚点库校验全部通过")
