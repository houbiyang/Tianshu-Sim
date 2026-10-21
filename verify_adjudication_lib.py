# -*- coding: utf-8 -*-
"""⚖️ 裁决库（adjudication/）离线守卫 —— 零 API Key、零网络。

跑法：
    <venv>/python.exe verify_adjudication_lib.py

这里重点不是"能读到一个值"，而是三件容易假通过的事：

1. **加第二场战役，老战役的参数不能被悄悄改掉。**
   旧实现遍历目录里所有 md 依次 update，后者覆盖前者 —— 于是两场战役会
   合出一个"长平的接触线 + 诺曼底的包围圈"的混合体。本文件末尾留了
   旧实现的对照，用来证明"不串味"的断言不是恒真。

2. **写错的键/值必须报错。** 一个被安静忽略的拼错键，会让使用者以为参数
   已经生效，然后在错误的几何上推演一整天。

3. **没命中就回落默认，不借用别人的尺度。**
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gentactix import adjudication_lib as L  # noqa: E402
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


def write_battle(directory, name, body):
    path = Path(directory) / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def empty_dir(root, name="_none"):
    """一个不存在的目录，用来关掉某一层来源。"""
    return Path(root) / name


def unit(name, lat, lng, utype="步兵"):
    return {"name": name, "type": utype, "lat": lat, "lng": lng,
            "hp": 1000, "combat_power": 60, "speed": 5}


# ==========================================
print("=" * 60)
print("1. 库文件发现")
print("=" * 60)
names = L.list_battles()
check("真实库里能发现长平之战", "长平之战" in names, names)
check("模板（_ 开头）不被当成战役", "_template" not in names, names)
check("README 不被当成战役", "README" not in names, names)
check("默认参数齐全且是 float",
      all(isinstance(v, float) for v in L.default_params().values())
      and set(L.default_params()) == set(L.PARAM_SCHEMA))
check("default_params 每次都返回新副本",
      L.default_params() is not L.default_params())

# ==========================================
print()
print("=" * 60)
print("2. 匹配与优先级")
print("=" * 60)
spec = L.resolve(scenario_text="长平之战")
check("精确同名命中", spec.name == "长平之战" and spec.matched_by == "exact", spec.matched_by)
check("命中时可以指出来源文件", spec.source is not None and spec.source_kind == "library")

spec = L.resolve(scenario_text="推演长平之战，公元前 260 年")
check("描述里包含战役名也命中", spec.matched_by == "contains", spec.matched_by)

spec = L.resolve(scenario_text="诺曼底登陆")
check("无匹配回落默认", spec.matched_by == "fallback" and spec.explicit == {}, spec.matched_by)
check("回落时参数仍然完整（不会缺键）", set(spec.params) == set(L.PARAM_SCHEMA))
check("回落时界面文案说明未标定", "未标定" in spec.summary_line(), spec.summary_line())

spec = L.resolve(scenario_text="诺曼底登陆", battle="长平之战")
check("显式指定压过自动匹配", spec.name == "长平之战" and spec.matched_by == "explicit")

spec = L.resolve(battle="根本不存在的战役")
check("显式指定一个不存在的战役 → 默认 + 警告",
      spec.matched_by == "miss" and spec.explicit == {} and bool(spec.warnings),
      (spec.matched_by, spec.warnings))

spec = L.resolve(battle="  长平之战  ")
check("显式指定容忍首尾空白", spec.name == "长平之战", spec.name)

with tempfile.TemporaryDirectory() as tmp:
    lib = Path(tmp)
    write_battle(lib, "长平", "---\ncontact_km: 9\n---\n# 长平\n")
    write_battle(lib, "长平之战", "---\ncontact_km: 3\n---\n# 长平之战\n")
    spec = L.resolve(scenario_text="推演长平之战", library_dir=lib,
                     corpus_dir=empty_dir(tmp))
    check("多个名字都命中时取最长（更具体）的那个",
          spec.name == "长平之战" and spec.explicit["contact_km"] == 3.0,
          (spec.name, spec.explicit))

# ==========================================
print()
print("=" * 60)
print("3. 多战役不串味（本库存在的理由）")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    lib = Path(tmp)
    # 刻意让两场战役写**不同的键**：旧实现会把它俩合出一个不存在的混合体
    write_battle(lib, "长平之战", "---\ncontact_km: 3\n---\n")
    write_battle(lib, "诺曼底登陆", "---\nencircle_radius_km: 40\n---\n")

    a = L.resolve(battle="长平之战", library_dir=lib, corpus_dir=empty_dir(tmp))
    b = L.resolve(battle="诺曼底登陆", library_dir=lib, corpus_dir=empty_dir(tmp))

    check("A 只用自己写下的值", a.explicit == {"contact_km": 3.0}, a.explicit)
    check("B 只用自己写下的值", b.explicit == {"encircle_radius_km": 40.0}, b.explicit)
    check("A 里没有 B 独有的键", "encircle_radius_km" not in a.explicit, a.explicit)
    check("B 里没有 A 独有的键", "contact_km" not in b.explicit, b.explicit)
    check("A 的未写键走默认值（不是借用 B 的）",
          a.params["encircle_radius_km"] == L.default_params()["encircle_radius_km"],
          a.params["encircle_radius_km"])

    # ---- 对照：复现旧实现，证明上面这组断言有区分力 ----
    def legacy_merge(directory):
        merged = {}
        for path in sorted(Path(directory).glob("*.md")):
            raw, _ = L.parse_frontmatter(path)
            for key, value in raw.items():
                if key in L.PARAM_SCHEMA and isinstance(value, (int, float)):
                    merged[key] = float(value)
        return merged

    legacy = legacy_merge(lib)
    check("对照：旧实现合出了一个两场战役都不存在的混合体",
          "contact_km" in legacy and "encircle_radius_km" in legacy, legacy)
    check("对照：新实现不会",
          not ("contact_km" in b.explicit and "encircle_radius_km" in a.explicit))

# ==========================================
print()
print("=" * 60)
print("4. 裁决库优先于语料，且语料旧位置仍兼容")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    lib = Path(tmp) / "lib"
    cor = Path(tmp) / "cor"
    lib.mkdir()
    cor.mkdir()
    write_battle(lib, "长平之战", "---\ncontact_km: 3\n---\n")
    write_battle(cor, "长平之战", "---\ncontact_km: 99\n---\n")

    spec = L.resolve(battle="长平之战", library_dir=lib, corpus_dir=cor)
    check("同名两处都有时用裁决库那份", spec.explicit["contact_km"] == 3.0,
          spec.explicit)
    check("被忽略的一方有明确警告",
          any("corpus" in w for w in spec.warnings), spec.warnings)

    spec = L.resolve(battle="长平之战", library_dir=empty_dir(tmp), corpus_dir=cor)
    check("关掉库之后，语料 frontmatter 旧位置仍读得到",
          spec.explicit["contact_km"] == 99.0 and spec.source_kind == "corpus",
          (spec.explicit, spec.source_kind))

    write_battle(cor, "README", "---\ncontact_km: 1\n---\n")
    check("语料里的 README 不会被当成战役",
          "README" not in L.list_battles(library_dir=lib, corpus_dir=cor))

# ==========================================
print()
print("=" * 60)
print("5. 写错会报错（不再静默忽略）")
print("=" * 60)
ok, errs = L.validate_params({"contct_km": 5})
check("拼错键 → 报错", bool(errs) and "contct_km" in errs[0], errs)
check("拼错键 → 不进结果", ok == {}, ok)

ok, errs = L.validate_params({"contact_km": "三公里"})
check("非数字 → 报错且不生效", bool(errs) and ok == {}, (ok, errs))

ok, errs = L.validate_params({"contact_km": -5})
check("负距离 → 报错且不生效", bool(errs) and ok == {}, (ok, errs))

ok, errs = L.validate_params({"contact_km": 99999})
check("离谱的大值 → 报错（防止单位写错成米）", bool(errs) and ok == {}, (ok, errs))

ok, errs = L.validate_params({"sealed_ratio_encircled": 1.5})
check("比例超出 0—1 → 报错", bool(errs) and ok == {}, (ok, errs))

ok, errs = L.validate_params({"sealed_ratio_encircled": 0.40})
check("半包围阈值 ≥ 被围阈值 → 报错", bool(errs), errs)
check("冲突时两个比例一起退回默认",
      "sealed_ratio_encircled" not in ok and "sealed_ratio_partial" not in ok, ok)

ok, errs = L.validate_params({"contact_km": 5, "contct_km": 1})
check("一个笔误不该废掉整场（合法项照常生效）", ok.get("contact_km") == 5.0, ok)
check("同时把笔误报出来", bool(errs), errs)

ok, errs = L.validate_params({"battle_name": "x", "era": "战国", "note": "说明"})
check("元信息键不被当成几何参数", ok == {} and not errs, (ok, errs))

# ==========================================
print()
print("=" * 60)
print("6. 文件格式错误要报出来")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "没有头.md"
    p.write_text("# 只有正文\n\n## 概述\n", encoding="utf-8")
    raw, errs = L.parse_frontmatter(p)
    check("缺开头 --- → 报错", bool(errs) and raw == {}, errs)

    p = Path(tmp) / "没结尾.md"
    p.write_text("---\ncontact_km: 3\n", encoding="utf-8")
    raw, errs = L.parse_frontmatter(p)
    check("缺结尾 --- → 报错", bool(errs), errs)
    check("但已读到的值仍可用（不整体丢弃）", raw.get("contact_km") == 3.0, raw)

    p = Path(tmp) / "有坏行.md"
    p.write_text("---\ncontact_km: 3\n这一行没有冒号\n---\n", encoding="utf-8")
    raw, errs = L.parse_frontmatter(p)
    check("无法解析的行 → 报错", bool(errs), errs)
    check("合法行不受影响", raw.get("contact_km") == 3.0, raw)

    p = Path(tmp) / "带注释.md"
    p.write_text("---\n# 整行注释\ncontact_km: 4  # 行尾注释\n---\n", encoding="utf-8")
    raw, errs = L.parse_frontmatter(p)
    check("注释不会导致报错", not errs and raw.get("contact_km") == 4.0, (raw, errs))

# ==========================================
print()
print("=" * 60)
print("7. 写回与往返")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    target = L.save_battle("诺曼底登陆", {"contact_km": 9, "control_radius_km": 7},
                           note="1944 年 6 月", era="二战", library_dir=tmp)
    check("文件已写出", Path(target).is_file(), target)

    spec = L.resolve(battle="诺曼底登陆", library_dir=tmp, corpus_dir=empty_dir(tmp))
    check("写回后立刻能读到",
          spec.explicit == {"contact_km": 9.0, "control_radius_km": 7.0}, spec.explicit)
    check("写回的文件本身没有解析错误", spec.errors == [], spec.errors)
    text = Path(target).read_text(encoding="utf-8")
    check("元信息被保留在文件里", "二战" in text and "1944 年 6 月" in text)

    L.save_battle("诺曼底登陆", {"contact_km": 12}, library_dir=tmp)
    spec = L.resolve(battle="诺曼底登陆", library_dir=tmp, corpus_dir=empty_dir(tmp))
    check("第二次写回是覆盖而不是追加",
          spec.explicit == {"contact_km": 12.0}, spec.explicit)
    check("覆盖后文件里没有残留旧值",
          Path(target).read_text(encoding="utf-8").count("battle_name:") == 1)

    rejected = []
    for bad in ["", "  ", "../逃逸", "_模板", ".隐藏", "a/b", 'a"b', "README"]:
        try:
            L.save_battle(bad, {"contact_km": 3}, library_dir=tmp)
        except ValueError:
            rejected.append(bad)
    check("拒绝所有非法战役名", len(rejected) == 8, rejected)

    refused_bad_params = False
    try:
        L.save_battle("坏参数", {"contact_km": -1}, library_dir=tmp)
    except ValueError:
        refused_bad_params = True
    check("拒绝非法参数", refused_bad_params)
    check("坏参数没有留下文件", not (Path(tmp) / "坏参数.md").exists())

# ==========================================
print()
print("=" * 60)
print("8. 与内核接线")
print("=" * 60)
plain = adj.load_scenario_params(scenario_text="长平之战")
expected = L.resolve(scenario_text="长平之战").explicit
check("adjudicator 走的是同一个库", plain == expected, (plain, expected))

spec = adj.load_battle_spec(scenario_text="推演长平之战")
check("load_battle_spec 带来源与匹配方式",
      spec.source is not None and spec.source_kind == "library"
      and spec.matched_by in {"contains", "exact"},
      (spec.source_kind, spec.matched_by))

battle = {
    "red_units": [unit("赵军甲", 35.9006, 112.8556),
                  unit("赵军乙", 35.9100, 112.8700)],
    "blue_units": [unit("秦军甲", 35.9600, 112.8800)],
}
tight = adj.adjudicate(battle, "15分钟", params={"contact_km": 0.5})
loose = adj.adjudicate(battle, "15分钟", params={"contact_km": 50.0})
check("参数确实进了计算（接触线放大后接触数上升）",
      loose["summary"]["can_contact"] > tight["summary"]["can_contact"],
      (tight["summary"]["can_contact"], loose["summary"]["can_contact"]))
check("不同参数产生不同指纹（能区分，不是形参）",
      tight["fingerprint"] != loose["fingerprint"])
check("同一参数两次指纹相同（仍可复现）",
      adj.adjudicate(battle, "15分钟", params={"contact_km": 0.5})["fingerprint"]
      == tight["fingerprint"])

rows = L.param_table(L.resolve(battle="长平之战"))
check("界面参数表覆盖全部键", len(rows) == len(L.PARAM_SCHEMA), len(rows))
check("参数表能标出哪些是默认值",
      any(r["来源"] == "默认" for r in rows) or all(r["来源"] == "本战役标定" for r in rows))

# ==========================================
# 🗺️ terrain_factor 标定 + time_step 建议回合跨度
# ==========================================
spec_cp = L.resolve(battle="长平之战")
check("长平已标定 terrain_factor=1.4（河谷丘陵，覆盖模型标签）",
      spec_cp.explicit.get("terrain_factor") == 1.4, spec_cp.explicit)
check("time_step 是元信息：不产生未知参数错误",
      not any("time_step" in e for e in spec_cp.errors), spec_cp.errors)
check("time_step 提示透出（前端预填回合跨度）",
      spec_cp.time_step_hint == "6小时", spec_cp.time_step_hint)
details = {d["name"]: d for d in L.list_battle_details()}
check("list_battle_details 带建议回合跨度",
      details.get("长平之战", {}).get("time_step") == "6小时", details)
cp_row = next(r for r in L.param_table(spec_cp) if r["参数"] == "terrain_factor")
check("参数表把 terrain_factor 标成本战役标定", cp_row["来源"] == "本战役标定", cp_row)

with tempfile.TemporaryDirectory() as td:
    lib = Path(td) / "lib"
    lib.mkdir()
    write_battle(lib, "坏地形", "---\nbattle_name: 坏地形\nterrain_factor: 0.5\n---\n")
    s_bad = L.resolve(battle="坏地形", library_dir=lib, corpus_dir=empty_dir(td))
    check("terrain_factor 越界必须报错且不生效",
          any("terrain_factor" in e for e in s_bad.errors)
          and "terrain_factor" not in s_bad.explicit,
          (s_bad.errors, s_bad.explicit))

# ==========================================
print()
print("=" * 60)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
if FAILURES:
    for item in FAILURES:
        print(f"  - {item}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
