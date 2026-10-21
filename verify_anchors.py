"""长平锚点库离线校验：结构、范围、点名匹配、端到端吸附。

不联网、不烧 API Key。跑法：
    python verify_anchors.py
"""

import math
import sys

from unittest.mock import patch

from gentactix import anchors, geo, prompts

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

# 朝鲜战争：九场独立解锁、参数选择/别名一致、可追溯坐标与显式锚定。
catalog = anchors.KOREAN_ANCHORS
check("抗美援朝包含九场", len(catalog["campaigns"]) == 9)
for battle, entry in catalog["campaigns"].items():
    selected = anchors.collect_available_nodes("只需开局", battle=battle)
    check(f"{battle} 显式选择解锁", len(selected) == len(entry["points"]) >= 3)
    check(f"{battle} 全名匹配", anchors.collect_available_nodes(battle) == selected)
    alias = battle.split("-")[1]
    check(f"{alias} 别名匹配", anchors.collect_available_nodes(alias) == selected)
    check(f"{battle} 不混入其他战役", set(selected) == {
        catalog["points"][key]["name"] for key in entry["points"]})

check("泛称抗美援朝不混入九场", anchors.collect_available_nodes("抗美援朝战争") == {})
shang = anchors.collect_available_nodes("上甘岭")
check("显式选择优先于场景中的另一战役", anchors.collect_available_nodes(
    "研究上甘岭", battle="第一次战役") == anchors.collect_available_nodes("第一次战役"))
check("选定朝鲜战役不夹带旧战区", anchors.collect_available_nodes(
    "与珍珠港比较", battle="上甘岭") == shang)
for name, coords in shang.items():
    point = anchors.node_metadata(name)
    prompt = prompts.build_node_instruction(shang)
    check(f"{name} 坐标进入提示", str(coords[0]) in prompt and str(coords[1]) in prompt)
    check(f"{name} 精度与来源进入提示", point["source_url"] in prompt and "uncertainty_km" in prompt)
for key, point in catalog["points"].items():
    check(f"{key} 来源可追溯", point["source"] in catalog["sources"] and
          point["source_url"].startswith("https://") and point["uncertainty_km"] > 0)
    check(f"{key} 地域合法", 37 <= point["coordinates"][0] <= 41 and
          125 <= point["coordinates"][1] <= 129)
triangle = catalog["points"]["triangle"]
sniper = catalog["points"]["sniper"]
check("597.9原始DMS换算", abs(triangle["coordinates"][0] - (38 + 19/60 + 8.43/3600)) < 1e-6)
check("两处高地分列", 1.5 < km(triangle["coordinates"], sniper["coordinates"]) < 2.5 and
      sniper["coordinates"][1] > triangle["coordinates"][1])
check("温井与云山旧城分列", 15 < km(catalog["points"]["onjong"]["coordinates"],
                                  catalog["points"]["unsan"]["coordinates"]) < 20)
second = anchors.collect_available_nodes("第二次战役")
check("东西两线空间分离", km(catalog["points"]["kaechon"]["coordinates"],
                             catalog["points"]["hagaru"]["coordinates"]) > 100 and
      catalog["points"]["kaechon"]["name"] in second and
      catalog["points"]["hagaru"]["name"] in second)
selected_name = triangle["name"]
declared = {"name": "高地守备连", "type": "步兵", "lat": 38.1, "lng": 127.2,
            "node_name": selected_name}
attacker = {"name": "进攻597.9高地的美军连", "type": "步兵", "lat": 38.30, "lng": 127.46}
out_of_scope = {"name": "机动营", "type": "步兵", "lat": 38.31, "lng": 127.47,
                "node_name": catalog["points"]["seoul"]["name"]}
state = {"red_units": [declared], "blue_units": [attacker, out_of_scope]}
with patch.object(geo, "_notify"):
    geo.sanitize_init_units(state, shang)
check("显式node_name实际吸附", [declared["lat"], declared["lng"]] == triangle["coordinates"])
check("名称提到进攻目标不吸附", [attacker["lat"], attacker["lng"]] == [38.30, 127.46])
check("别场锚点不生效", [out_of_scope["lat"], out_of_scope["lng"]] == [38.31, 127.47])
ship = {"name": "舰艇支援", "type": "驱逐舰", "lat": 38.1, "lng": 127.2,
        "node_name": selected_name}
with patch.object(geo, "force_into_water", return_value=(39.0, 129.0)) as water, \
     patch.object(geo, "_notify"):
    geo.sanitize_init_units({"red_units": [ship]}, shang)
    check("陆上锚点不能让舰艇跳过海陆审计", water.call_count == 1 and ship["lng"] == 129.0)
shang[selected_name][0] = 0
check("收集结果不能回写源库", anchors.collect_available_nodes("上甘岭")[selected_name][0] != 0)

print(f"\n{'=' * 46}")
print(f"PASS {PASS} / FAIL {FAIL}")
if FAILS:
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
print("战役锚点库校验全部通过")
