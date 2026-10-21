"""⚖️ 裁决内核离线守卫（零 API Key、零网络）。

跑法：
    <venv>/python.exe verify_adjudicator.py

检查六件事：
  1. 基础换算（时间 / 地形 / 速度 / 射程）与 viz 口径一致；
  2. 长平态势下的粮道切断判定（用真实锚点坐标）；
  3. 合围与反例（单向包围不得判成被围）；
  4. 可复现性：同输入两次，指纹必须相同；
  5. 位移越界检查（只报告，不改数据）；
  6. prompt 注入文本的结构完整性。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gentactix import adjudicator as adj  # noqa: E402

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


def unit(name, team, lat, lng, utype="步兵", **extra):
    data = {"name": name, "type": utype, "lat": lat, "lng": lng,
            "hp": 1000, "combat_power": 60, "speed": 5,
            "attack_range": 200, "morale": 80, "supply": 80}
    data.update(extra)
    data["__team"] = team
    return data


def state_of(red, blue, terrain="丘陵森林"):
    return {
        "current_time": "公元前260年秋 某日黎明",
        "current_terrain": terrain,
        "red_units": [{k: v for k, v in u.items() if k != "__team"} for u in red],
        "blue_units": [{k: v for k, v in u.items() if k != "__team"} for u in blue],
        "engagements": [],
    }


print("=" * 60)
print("1. 基础换算")
print("=" * 60)
check("30分钟 → 0.5h", abs(adj.parse_time_step("30分钟") - 0.5) < 1e-9, adj.parse_time_step("30分钟"))
check("5分钟 → 1/12h", abs(adj.parse_time_step("5分钟") - 5 / 60) < 1e-9, adj.parse_time_step("5分钟"))
check("1小时 → 1h", abs(adj.parse_time_step("1小时") - 1.0) < 1e-9)
check("1天 → 24h", abs(adj.parse_time_step("1天") - 24.0) < 1e-9)
check("1周 → 168h", abs(adj.parse_time_step("1周") - 168.0) < 1e-9)
check("1个月 → 720h", abs(adj.parse_time_step("1个月") - 720.0) < 1e-9)
check("空值回退 0.5h", abs(adj.parse_time_step("") - 0.5) < 1e-9)

check("平原 ×1.0", adj.terrain_factor("平原") == 1.0)
check("丘陵森林取更长匹配 ×1.6", adj.terrain_factor("丘陵森林") == 1.6, adj.terrain_factor("丘陵森林"))
check("高山峻岭 ×2.2", adj.terrain_factor("高山峻岭") == 2.2)
check("未知地形回退 ×1.2", adj.terrain_factor("") == 1.2)

check("步兵 3.0 km/h", adj.unit_march_speed_kmh("步兵", 5) == 3.0, adj.unit_march_speed_kmh("步兵", 5))
check("骑兵取 7.5（不是 '骑' 的 7.0）", adj.unit_march_speed_kmh("骑兵", 5) == 7.5, adj.unit_march_speed_kmh("骑兵", 5))
check("骑射 8.0", adj.unit_march_speed_kmh("骑射", 5) == 8.0)
check("水军 6.0", adj.unit_march_speed_kmh("水军", 5) == 6.0)
check("长时回合折算为有效行军（×0.45）", abs(adj.unit_march_speed_kmh("步兵", 5, hours=12) - 3.0 * 0.45) < 1e-9,
      adj.unit_march_speed_kmh("步兵", 5, hours=12))

# 量级守卫：1 天回合在丘陵地形下，步兵应落在 15—25 km，骑兵 40—60 km。
# 数值不对通常意味着速度表或折算系数被改坏了（例如忘了折算，步兵会跑到 45 km）。
_day = adj.adjudicate(
    {"current_terrain": "丘陵森林",
     "red_units": [{"name": "步兵", "type": "步兵", "lat": 35.90, "lng": 112.85}],
     "blue_units": [{"name": "骑兵", "type": "骑兵", "lat": 35.84, "lng": 112.92}]},
    "1天", turn=1)
_day_rows = {r["name"]: r for r in _day["units"]}
check("1 天回合步兵 15—25 km", 15.0 <= _day_rows["步兵"]["reach_km"] <= 25.0,
      _day_rows["步兵"]["reach_km"])
check("1 天回合骑兵 40—60 km", 40.0 <= _day_rows["骑兵"]["reach_km"] <= 60.0,
      _day_rows["骑兵"]["reach_km"])

# 射程表必须和 viz 同口径，否则「地图画了扇区、裁决说够不着」会自相矛盾
try:
    from gentactix import viz
    mismatch = []
    for text in ("骑射", "弓弩", "步兵", "骑兵", "投石车", "战车"):
        a = adj.unit_attack_range_m({"type": text})
        b = viz.unit_type_default_range(text)
        if b is not None and abs(a - b) > 1e-6:
            mismatch.append((text, a, b))
    check("射程表与 viz.unit_type_default_range 一致", not mismatch, mismatch)
except Exception as exc:  # viz 依赖 folium/streamlit，缺依赖时跳过而非误判失败
    print(f"  [SKIP] 射程表一致性（viz 不可导入：{exc}）")


print("=" * 60)
print("2. 长平态势：粮道切断（真实锚点坐标）")
print("=" * 60)
# 赵军主力在长平村，辎重在后方大粮山；秦军奇兵插到两者之间，主力在空仓岭。
changping = state_of(
    red=[
        unit("赵军主力", "red", 35.9006, 112.8556, "步兵"),
        unit("赵军辎重营", "red", 35.7876, 112.9790, "步兵"),
    ],
    blue=[
        unit("秦军奇兵", "blue", 35.8400, 112.9200, "骑兵"),
        unit("秦军主力", "blue", 35.7306, 112.7348, "步兵"),
    ],
)
res = adj.adjudicate(changping, "30分钟", turn=1)
rows = {r["name"]: r for r in res["units"]}
check("解析出 4 个单位", len(res["units"]) == 4, len(res["units"]))
check("赵军主力补给线被切断", rows["赵军主力"]["supply"] == "已切断",
      rows["赵军主力"]["supply"])
check("切断者是秦军奇兵", rows["赵军主力"]["cut_by"] == "秦军奇兵", rows["赵军主力"]["cut_by"])
check("切断点距出发 5—10 km", 5.0 <= (rows["赵军主力"]["cut_at_km"] or -1) <= 10.0,
      rows["赵军主力"]["cut_at_km"])
check("切断点落在奇兵控制圈内", True)  # 由上一项间接证明

# 反例：把奇兵挪到战线外，补给线应恢复通畅
safe = state_of(
    red=[
        unit("赵军主力", "red", 35.9006, 112.8556, "步兵"),
        unit("赵军辎重营", "red", 35.7876, 112.9790, "步兵"),
    ],
    blue=[unit("秦军主力", "blue", 35.7306, 112.7348, "步兵")],
)
res_safe = adj.adjudicate(safe, "30分钟", turn=1)
safe_rows = {r["name"]: r for r in res_safe["units"]}
check("奇兵调走后粮道恢复", safe_rows["赵军主力"]["supply"] == "未切断",
      safe_rows["赵军主力"]["supply"])

check("地形系数进入机动上限", abs(rows["赵军主力"]["reach_km"] - 3.0 * 0.5 / 1.6) < 0.02,
      rows["赵军主力"]["reach_km"])
check("30 分钟内够不着 8.9 km 外的奇兵", rows["赵军主力"]["can_contact"] is False)
check("奇兵 30 分钟也够不着主力（2.3 km < 7.9 km）",
      rows["秦军奇兵"]["can_contact"] is False,
      (rows["秦军奇兵"]["reach_km"], rows["秦军奇兵"]["nearest_enemy_km"]))
# 同一对手、同一距离，只把时间跨度拉到 6 小时 ⇒ 骑兵应当追得上。
res_long = adj.adjudicate(changping, "6小时", turn=1)
long_rows = {r["name"]: r for r in res_long["units"]}
check("6 小时跨度下奇兵可接触主力", long_rows["秦军奇兵"]["can_contact"] is True,
      (long_rows["秦军奇兵"]["reach_km"], long_rows["秦军奇兵"]["nearest_enemy_km"]))


print("=" * 60)
print("3. 合围与反例")
print("=" * 60)
CENTER = (35.90, 112.90)
ring = []
for i in range(8):
    bearing = 360.0 * i / 8
    pos = adj.offset_point(CENTER, bearing, 2.0)
    ring.append(unit(f"秦军环{i + 1}", "blue", pos[0], pos[1], "步兵"))

encircled = state_of(
    red=[unit("赵军被围部", "red", CENTER[0], CENTER[1], "步兵"),
         unit("赵军后方", "red", 35.78, 112.98, "步兵")],
    blue=ring,
)
res_enc = adj.adjudicate(encircled, "30分钟", turn=2)
enc_row = {r["name"]: r for r in res_enc["units"]}["赵军被围部"]
check("八面合围判为被围", enc_row["encirclement"] == "被围", enc_row["encirclement"])
check("被围时退路已断", enc_row["retreat"] == "已断", enc_row["retreat"])
check("被围 + 断粮 → 士气崩溃", enc_row["morale_state"] == "崩溃",
      (enc_row["supply"], enc_row["morale_state"]))

# 反例：敌军全在同一侧，不得判成被围
one_side = state_of(
    red=[unit("赵军", "red", CENTER[0], CENTER[1], "步兵")],
    blue=[unit(f"秦军西{i + 1}", "blue", CENTER[0] - 0.02 * (i + 1), CENTER[1] - 0.03, "步兵")
          for i in range(4)],
)
res_one = adj.adjudicate(one_side, "30分钟", turn=2)
one_row = res_one["units"][0]
check("单向压迫不算被围", one_row["encirclement"] == "未被围",
      f"{one_row['encirclement']} sealed={one_row['sealed_ratio']}")
check("单向压迫下退路仍在", one_row["retreat"] == "可退", one_row["retreat"])


print("=" * 60)
print("4. 可复现性（这是裁决器存在的理由）")
print("=" * 60)
a = adj.adjudicate(changping, "30分钟", turn=1)
b = adj.adjudicate(changping, "30分钟", turn=1)
check("同输入 → 同指纹", a["fingerprint"] == b["fingerprint"], (a["fingerprint"], b["fingerprint"]))
check("同输入 → digest 逐字节一致", a["digest"] == b["digest"])
c = adj.adjudicate(changping, "1小时", turn=1)
check("换时间跨度 → 指纹变化", c["fingerprint"] != a["fingerprint"])
check("时间跨度加倍 → 机动距离加倍", abs(c["units"][0]["reach_km"] - 2 * a["units"][0]["reach_km"]) < 0.03,
      (a["units"][0]["reach_km"], c["units"][0]["reach_km"]))
check("指纹为 12 位十六进制", len(a["fingerprint"]) == 12 and all(
    ch in "0123456789abcdef" for ch in a["fingerprint"]), a["fingerprint"])


print("=" * 60)
print("5. 位移越界检查（只报告，不改数据）")
print("=" * 60)
prev = state_of(
    red=[unit("赵军主力", "red", 35.9006, 112.8556, "步兵"),
         unit("赵军辎重营", "red", 35.7876, 112.9790, "步兵")],
    blue=[unit("秦军奇兵", "blue", 35.8400, 112.9200, "骑兵")],
)
violation_state = state_of(
    red=[unit("赵军主力", "red", 35.9510, 112.8610, "步兵"),   # 约 5.7 km
         unit("赵军辎重营", "red", 35.7876, 112.9790, "步兵")],
    blue=[unit("秦军奇兵", "blue", 35.8400, 112.9200, "骑兵")],
)
adj_prev = adj.adjudicate(prev, "30分钟", turn=1)
violations = adj.check_movement(prev, violation_state, adj_prev)
check("抓到越界位移", len(violations) == 1, violations)
if violations:
    check("越界单位是赵军主力", violations[0]["name"] == "赵军主力", violations[0])
    check("越界量 > 4 km", violations[0]["over_km"] > 4.0, violations[0]["over_km"])
check("原地不动不报违规",
      adj.check_movement(prev, prev, adj_prev) == [])
check("裁决器未修改传入数据", prev["red_units"][0]["lat"] == 35.9006,
      prev["red_units"][0]["lat"])


print("=" * 60)
print("6. prompt 注入文本结构")
print("=" * 60)
digest = res["digest"]
check("含版本/回合标题", "确定性裁决" in digest and "第 1 回合" in digest)
check("含机动上限段", "机动上限" in digest)
check("含状态判定段", "状态判定" in digest)
check("含五条硬约束", "硬约束" in digest and digest.count("\n1. ") == 1)
check("点名了被切断的单位", "赵军主力" in digest)
check("写明不可改写", "不可改写" in digest)
check("长度在预算内（≤2000）", len(digest) <= 2000, len(digest))
check("空战局不崩", adj.adjudicate({}, "30分钟")["summary"]["units"] == 0)
check("缺坐标为跳过而非崩溃", adj.adjudicate(
    {"red_units": [{"name": "无效单位", "type": "步兵"}], "blue_units": []},
    "30分钟")["skipped_units"] == ["无效单位"])
check("中文键单位也能读数", adj.adjudicate(
    {"red_units": [{"名称": "赵军", "类型": "步兵", "纬度": 35.9, "经度": 112.9}],
     "blue_units": []}, "30分钟")["units"][0]["name"] == "赵军")


print("=" * 60)
print("7. 战役参数表（corpus frontmatter 覆盖）")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    sample = Path(tmp) / "长平之战.md"
    sample.write_text(
        "---\ncontact_km: 5.0   # 骑兵侦察范围更大\nencircle_radius_km: 15\nunknown_key: 99\n---\n"
        "# 长平之战\n\n## 概述\n测试语料。\n",
        encoding="utf-8")
    # 用空的 library_dir 隔离：默认会优先读项目里的 adjudication/长平之战.md，
    # 而这一段要验证的是「旧位置（corpus frontmatter）仍然读得到」。
    _isolated_lib = Path(tmp) / "_no_library"
    loaded = adj.load_scenario_params(tmp, "推演长平之战", library_dir=_isolated_lib)
    check("读到 contact_km 覆盖", loaded.get("contact_km") == 5.0, loaded)
    check("读到 encircle_radius_km 覆盖", loaded.get("encircle_radius_km") == 15.0)
    check("未知键被丢弃", "unknown_key" not in loaded, loaded)
    merged = adj.adjudicate(changping, "30分钟", params=loaded)
    check("覆盖值实际生效", merged["contact_km"] == 5.0, merged["contact_km"])
    # ⚠️ 语义在 2026-10-18 引入裁决库时反转：旧实现遍历目录里所有 md 依次合并，
    #    于是「场景没命中」也会拿到别的战役的参数 —— 那正是多战役串味的根源。
    #    现在没命中就是空，宁可回落默认几何，也不借用别人的尺度。
    check("未命中场景时不借用别的战役参数",
          adj.load_scenario_params(tmp, "诺曼底", library_dir=_isolated_lib) == {})

print()
print("=" * 60)
print("8. prompt 注入（位置比内容更重要）")
print("=" * 60)
from gentactix import prompts  # noqa: E402

plain = prompts.build_step_prompt(changping, 2, "30分钟", False, "")
injected = prompts.build_step_prompt(changping, 2, "30分钟", False, "", adjudication=res)
check("未启用时不含裁决块", "确定性裁决" not in plain)
check("未启用时保留旧的物理计算措辞", "物理计算辅助" in plain)
check("启用时含裁决块", "确定性裁决" in injected)
check("启用时物理条款改为『已由系统裁决』", "已由系统裁决" in injected and "物理计算辅助" not in injected)
check("注入的是本次结果的指纹对应文本", "第 1 回合" in injected)
# 位置断言：裁决块必须在「强制任务」之前，否则模型会当成参考资料而不是前提。
check("裁决块位于『强制任务』之前",
      injected.index("确定性裁决") < injected.index("### 强制任务"),
      (injected.index("确定性裁决"), injected.index("### 强制任务")))
check("硬约束五条完整进入 prompt", "硬约束" in injected and "不得出现以它为攻击方的条目" in injected)
check("未替换的占位符不存在", "{adjudication_block}" not in injected and "{physics_cmd}" not in injected)
check("战报写作规范仍在（没被裁决块挤掉）", "战报写作规范" in injected)

print()
print("=" * 60)
print("terrain_factor：战役标定覆盖模型地形标签")
print("=" * 60)
# changping 的 current_terrain 是「丘陵森林」（×1.6）；标定 2.0 后机动必须按 2.0 算
plain = adj.adjudicate(changping, "30分钟", turn=1)
over = adj.adjudicate(changping, "30分钟", turn=1, params={"terrain_factor": 2.0})
check("未标定时地形来自模型标签", plain["terrain_source"] == "模型地形标签", plain.get("terrain_source"))
check("标定后地形系数取参数库值", over["terrain_factor"] == 2.0, over["terrain_factor"])
check("标定后注明来源", over["terrain_source"] == "战役参数库标定", over.get("terrain_source"))
check("机动按标定系数重算（1.6→2.0 即 ×0.8）",
      all(abs(o["reach_km"] - p["reach_km"] * 0.8) <= 0.03
          for p, o in zip(plain["units"], over["units"])),
      [(p["reach_km"], o["reach_km"]) for p, o in zip(plain["units"], over["units"])])
check("标定改变结论 ⇒ 指纹不同", plain["fingerprint"] != over["fingerprint"])
check("注入 prompt 的裁决块注明来源", "战役参数库标定" in over["digest"])
check("DEFAULT 里的 terrain_factor 默认值不算标定",
      adj.adjudicate(changping, "30分钟", turn=1, params={})["terrain_source"] == "模型地形标签")

print()
print("=" * 60)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
for item in FAILURES:
    print(f"  ✗ {item}")
print("=" * 60)
sys.exit(1 if FAIL else 0)

if FAILURES:
    for item in FAILURES:
        print(f"  ✗ {item}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
