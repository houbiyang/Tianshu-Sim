"""Render editable Long March records into the existing catalog and teaching CSVs.

    python tools/render_long_march.py
    python tools/render_long_march.py --check

No network, model, live state, or AGENTS.md access. Historical data are references,
not scheduled mutations of simulation state. Edit data/long_march/*.json first.
"""
import argparse
import csv
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "long_march"


def render(data_dir=DATA, anchor_file="long_march_anchors.json", series="红军长征",
           era="红军长征（1934—1936）", renderer="render_long_march.py",
           mechanics="渡船容量、架桥进度、疲劳、粮弹收支、指挥失联及跨场景继承",
           quantity_formatter=None):
    dataset = json.loads((data_dir / "scenarios.json").read_text(encoding="utf-8"))
    records = dataset["scenarios"]
    evidence = dataset["evidence_levels"]
    sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))["sources"]
    anchors = json.loads((ROOT / "gentactix" / anchor_file).read_text(encoding="utf-8"))
    outputs = {}
    timeline = io.StringIO(newline="")
    writer = csv.writer(timeline)
    writer.writerow(["scenario_id", "battle", "date", "time_label", "precision", "event", "source_ids", "evidence_status", "variant_group", "variant"])
    losses = io.StringIO(newline="")
    loss_writer = csv.writer(losses)
    loss_writer.writerow(["scenario_id", "side", "metric", "value", "qualifier", "scope", "source_ids", "note", "evidence_status"])
    forces = io.StringIO(newline="")
    force_writer = csv.writer(forces)
    force_writer.writerow(["scenario_id", "side", "unit", "description", "source_ids", "evidence_status"])
    gaps = io.StringIO(newline="")
    gap_writer = csv.writer(gaps)
    gap_writer.writerow(["scenario_id", "category", "scope", "required_evidence"])
    for s in records:
        name = s["name"]
        def cite(ids):
            return "、".join(f'[{key}]({sources[key]["url"]})' for key in ids)
        parts = [f"# {name}", "", "## 时间与地点",
                 f'战线：{s["track"]}。类型：{s["kind"]}。{s["scope"]}',
                 '本卡是史料参考与教学想定，历史结局不是推演必达结果。核读日期：2026-10-22。来源见各条目及文末。',
                 "", "## 双方兵力与指挥"]
        for u in s["forces"]:
            parts.append(f'- **{u["side"]}｜{u["unit"]}**：{u["description"]} {cite(u["sources"])}')
            force_writer.writerow([s["id"], u["side"], u["unit"], u["description"], ";".join(u["sources"]), u["evidence_status"]])
        parts += ["", "## 作战过程与指挥时间线",
                  "时刻沿用原文当地钟点，未校准历史时区；‘夜间’不转换为整点。公开报道中的小时记载可收录，但不升级为原始命令确证。同一来源的转载不算独立印证。",
                  "", "| 日期 | 原文时间精度 | 事件与指挥动作 | 证据状态／异文组 | 依据 |", "|---|---|---|---|---|"]
        for e in s["events"]:
            status = evidence[e["evidence_status"]] + (f'；{e["variant_group"]}' if e.get("variant_group") else "")
            parts.append(f'| {e["date"]} | {e["time_label"]}（{e["precision"]}） | {e["event"]} | {status} | {cite(e["sources"])} |')
            writer.writerow([s["id"], name, e["date"], e["time_label"], e["precision"], e["event"], ";".join(e["sources"]), e["evidence_status"], e.get("variant_group", ""), e.get("variant", "")])
        parts += ["", "## 伤亡、兵力快照与结果",
                  "未知数量记为 null／未核得，不按零伤亡处理。毙伤俘、战斗伤亡、总减员、现有兵力及缴获不能混加。",
                  "", "| 一方 | 指标与数值 | 统计范围、限制 | 依据 |", "|---|---|---|---|"]
        for c in s["figures"]:
            value = "未核得" if c["value"] is None else str(c["value"]) + c["qualifier"]
            if c["value"] is not None and quantity_formatter:
                value = quantity_formatter(c["value"], c["qualifier"])
            parts.append(f'| {c["side"]} | {c["metric"]}：{value} | {c["scope"]}；{c["note"]}；{evidence[c["evidence_status"]]} | {cite(c["sources"])} |')
            loss_writer.writerow([s["id"], c["side"], c["metric"], "null" if c["value"] is None else c["value"], c["qualifier"], c["scope"], ";".join(c["sources"]), c["note"], c["evidence_status"]])
            if c["value"] is None:
                gap_writer.writerow([s["id"], "数量", f'{c["side"]}：{c["metric"]}；{c["scope"]}', "同一统计窗口的部队报告、名册或注明口径的军史"])
        parts += ["", s["outcome"], "", "## 地形、坐标与地图显示",
                  "坐标为现代 WGS84 地名参考，顺序为纬度、经度；误差圈是保守工作估计，不是测量置信区间。不得将城镇中心自动当作渡口、掩体或部队部署点。",
                  "", "| 参考点 | 纬度、经度 | 工作误差圈 km | 地名数据 |", "|---|---|---|---|"]
        for key in anchors["campaigns"][name]["points"]:
            p = anchors["points"][key]
            parts.append(f'| {p["name"]} | {p["coordinates"][0]}, {p["coordinates"][1]} | {p["uncertainty_km"]} | [GeoNames {p["geoname_id"]}]({p["source_url"]}) |')
        parts += ["", s["geography"], "", "## 兵种、装备与补给", s["equipment"],
                  "", "## 推演目标、关键决策与反事实", s["modeling"],
                  "", "## 争议与待考", *["- " + x for x in s["uncertainties"]],
                  "", "## 资料与出处"]
        for key in s["sources"]:
            src = sources[key]
            parts.append(f'- {key} [{src["title"]}]({src["url"]})，{src["publisher"]}；{src["evidence_type"]}。用途：{src["supports"]}')
            if src.get("caveat"):
                parts.append(f'  适用限制：{src["caveat"]}')
        gap_writer.writerow([s["id"], "战术坐标", s["geography"], "历史地图配准、渡口/山道/工事身份核对；纪念设施和现代聚落坐标不能代替"])
        gap_writer.writerow([s["id"], "实力与装备", "逐日部队编制、实到人数、可用装备、粮弹与后送记录", "注明日期与建制边界的实力表；不能用团数乘满编人数"])
        parts += ["", f"编辑源：`{data_dir.relative_to(ROOT)}/scenarios.json`；执行 `python tools/{renderer}` 更新本卡及配套 CSV。"]
        outputs[ROOT / "corpus" / (name + ".md")] = "\n".join(parts) + "\n"
        params = s["geometry"]
        lines = ["---", f"battle_name: {name}", "aliases: " + "|".join(s["aliases"]),
                 f"era: {era}", f'time_step: {s["time_step"]}',
                 f"source_ref: corpus/{name}.md", "updated: 2026-10-22",
                 f'validation_status: {s["parameter_status"]}',
                 dataset.get("preset_note", "note: 教学几何工作估计；非史实伤亡拟合，不实现渡运、疲劳或阶段继承算法")]
        lines += [f"{k}: {v}" for k, v in params.items()]
        lines += ["---", "", f"# {name}：裁决工作参数", "", "## 使用范围",
                  f'建议局部回合 {s["time_step"]}。{s["preset_note"]}',
                  "", "## 计算边界",
                  "contact_km 参与当前 geo-5 的移动可达与接触距离判定；它不是武器射程。terrain_factor 是位移折减的工作估计，不计算山脊遮蔽、河流通行或真实射界。",
                  f"参数仅支持现有几何裁决。{mechanics}须由教师控制或后续算法实现；不能声称已经自动计算。",
                  "", "## 初始化与结果",
                  "从史料卡选定一个具体日期和局部地域初始化。多月转移卡不能把相隔数百公里、不同日期的部队同时投入一张战术态势；历史战损不可作为逐回合扣减表。",
                  "已知兵力与未知人数分别标注；不得把 hp 当人数，未知装备损耗率不得填零。默认裁决开关保持关闭。",
                  "", f"史料：`corpus/{name}.md`。"]
        outputs[ROOT / "adjudication" / (name + ".md")] = "\n".join(lines) + "\n"
    outputs[data_dir / "timeline.csv"] = timeline.getvalue()
    outputs[data_dir / "figures.csv"] = losses.getvalue()
    outputs[data_dir / "forces.csv"] = forces.getvalue()
    outputs[data_dir / "verification_gaps.csv"] = gaps.getvalue()
    index = [f"# {series}场景文件索引", "", "由编辑源生成；入门与数据边界见[README](README.md)。", "",
             "| 编号 | 场景与史料 | 裁决参数 |", "|---|---|---|"]
    for s in records:
        index.append(f'| {s["id"]} | [{s["name"]}](../../corpus/{s["name"]}.md) | [参数](../../adjudication/{s["name"]}.md) |')
    outputs[data_dir / "INDEX.md"] = "\n".join(index) + "\n"
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, text in render().items():
        if args.check:
            if not path.exists() or path.read_bytes() != text.encode("utf-8"):
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
    if stale:
        print("Out of date:\n" + "\n".join(stale))
        return 1
    print("Long March documents checked" if args.check else "Long March documents rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
