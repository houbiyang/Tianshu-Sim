"""🔬 裁决约束实测探针：模型到底有没有守规矩？

跑法（Key 只走环境变量，不落盘、不进命令行历史）：

    set DEEPSEEK_API_KEY=sk-xxx
    <venv>/python.exe live_adjudication_probe.py

    # 干跑：不调模型，用一份故意违规的假响应验证检测器本身是否有效
    <venv>/python.exe live_adjudication_probe.py --dry-run

它回答四个可以用数字说清的问题：

  Q1 位移守住了吗？   —— AI 实际移动距离 vs 裁决给出的机动上限
  Q2 交战越界了吗？   —— 判为「够不着」的单位，是否仍被写成攻击方
  Q3 状态认账了吗？   —— 判为「粮道已切断」，报告里是否仍写「畅通 / 补给充足」
  Q4 裁决稳定吗？     —— 同一输入连算两次，指纹是否逐字节一致

注意 Q3 是**启发式**：靠关键词匹配，可能漏掉反讽式表述（「并非粮道畅通」）。
它给的是线索，不是判决 —— 这也是为什么结论要人看一眼原文再下。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gentactix import adjudicator, prompts  # noqa: E402

# ==========================================
# 🗺️ 固定想定：长平之战，真实锚点坐标
# ==========================================
# 赵军主力在长平村壁垒，辎重在大粮山；秦军奇兵插在两者之间，主力在空仓岭。
# 这是史料明确记载的态势（「秦奇兵二万五千人绝赵军后」），不是为演示编的。
SCENARIO = {
    "current_time": "公元前260年秋 某日黎明",
    "current_terrain": "丘陵森林",
    "red_units": [
        {"name": "赵军主力", "type": "步兵", "lat": 35.9006, "lng": 112.8556,
         "hp": 12000, "combat_power": 60, "speed": 5, "attack_range": 200,
         "morale": 75, "supply": 80},
        {"name": "赵军辎重营", "type": "步兵", "lat": 35.7876, "lng": 112.9790,
         "hp": 3000, "combat_power": 35, "speed": 5, "attack_range": 150,
         "morale": 70, "supply": 90},
        {"name": "赵军长平关守备", "type": "步兵", "lat": 35.9694, "lng": 112.7806,
         "hp": 2000, "combat_power": 45, "speed": 5, "attack_range": 300,
         "morale": 80, "supply": 85},
    ],
    "blue_units": [
        {"name": "秦军奇兵", "type": "骑兵", "lat": 35.8400, "lng": 112.9200,
         "hp": 5000, "combat_power": 70, "speed": 8, "attack_range": 300,
         "morale": 85, "supply": 75},
        {"name": "秦军主力", "type": "步兵", "lat": 35.7306, "lng": 112.7348,
         "hp": 14000, "combat_power": 75, "speed": 5, "attack_range": 200,
         "morale": 85, "supply": 80},
    ],
    "engagements": [],
}

# 与「补给已切断」直接矛盾的措辞。只收明确断言类，避免把「不得不断粮」误判成违规。
SUPPLY_CONTRADICTIONS = ("粮道畅通", "补给充足", "粮草无虞", "补给无虞",
                         "粮道无阻", "源源不断", "后勤稳固", "粮食充裕")


# ==========================================
# 🧠 模型调用（不 import engine，避免 st.cache_resource 在裸 Python 里的告警）
# ==========================================
def call_model(api_key, model, prompt):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",
             "content": "你是一个高度严密的战棋推演引擎，严格按要求输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"未取到 JSON：{raw[:200]}")
    return json.loads(raw[start:end + 1]), raw


# ==========================================
# 🕵️ 四项检测
# ==========================================
def check_movement_ceiling(prev_state, new_state, adj):
    """Q1：实际位移 vs 机动上限。"""
    return adjudicator.check_movement(prev_state, new_state, adj)


def check_engagement_grade(new_state, adj):
    """Q2：判为「够不着」的单位，是否仍被写成攻击方。"""
    reachable = {u["name"]: u["can_contact"] for u in adj["units"]}
    offenders = []
    for battle in (new_state.get("engagements") or []):
        if not isinstance(battle, dict):
            continue
        attacker = str(battle.get("attacker") or "").strip()
        if attacker and reachable.get(attacker) is False:
            offenders.append({
                "attacker": attacker,
                "reason": "裁决判为本回合够不着，却发起交战",
                "description": str(battle.get("description") or "")[:60],
            })
    return offenders


def check_supply_narrative(report_text, adj):
    """Q3：判为断粮的一方，报告里是否仍写「畅通 / 充足」。

    句级匹配：只有**同一句话**里同时出现「断粮单位名」和矛盾措辞才算。
    先前按全文匹配会误报 —— 报告写「赵军主力粮道畅通」时，被切断的其实是
    辎重营，主语根本不是一支断粮部队。宁可漏报，也不要制造假阳性：
    一个会误报的检查器，用户看两次就不看了。
    """
    cut_units = [u["name"] for u in adj["units"] if u["supply"] == "已切断"]
    hits = []
    if not cut_units or not report_text:
        return hits

    # 按句切开：中文句号、分号、换行。逗号不切 —— 一句话里主语和谓语可能隔着一个逗号。
    for sentence in report_text.replace("\n", "。").split("。"):
        for segment in sentence.split("；"):
            named = [name for name in cut_units if name in segment]
            if not named:
                continue
            phrases = [p for p in SUPPLY_CONTRADICTIONS if p in segment]
            if phrases:
                # 同一句命中多个措辞只报一条：否则「粮道畅通、源源不断」会算成两次违规，
                # 让统计看起来比实际严重，属于自欺。
                hits.append({
                    "phrases": phrases,
                    "units": named,
                    "sentence": segment.strip()[:60],
                })
    return hits


def check_reproducibility(state, time_step, turn):
    """Q4：同输入两次裁决，指纹必须一致。"""
    a = adjudicator.adjudicate(state, time_step, turn)
    b = adjudicator.adjudicate(state, time_step, turn)
    return a["fingerprint"], b["fingerprint"], a["fingerprint"] == b["fingerprint"]


# ==========================================
# 🧪 干跑用的假响应：故意违反三条约束
# ==========================================
def mock_response(state, adj):
    """构造一份「模型不守规矩」的响应，用来验证检测器不是恒真。

    故意做三件事：
      1. 让赵军主力走约 6 km（上限不到 1 km）；
      2. 让一个判为够不着的单位当攻击方；
      3. 报告里写「粮道畅通」。
    """
    units = json.loads(json.dumps(state, ensure_ascii=False))
    for unit in units["red_units"]:
        if unit["name"] == "赵军主力":
            unit["lat"] = float(unit["lat"]) + 0.055   # ≈ 6.1 km
    units["current_time"] = "公元前260年秋 某日午后"
    units["engagements"] = [{
        "lat": 35.84, "lng": 112.92,
        "description": "赵军主力强攻秦军奇兵，将其击退",
        "attacker": "赵军主力",
        "attacker_pos": [35.9556, 112.8556],
    }]
    return {
        "updated_state": units,
        "report": "赵军主力疾进六里，击退秦军奇兵。赵军辎重营粮道畅通，"
                  "源源不断向长平关转运粮草，全军士气大振。",
        "source_note": "干跑样本，无史料出处。",
    }, json.dumps({"mock": True}, ensure_ascii=False)


# ==========================================
# 🚀 主流程
# ==========================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不调模型，用假响应验证检测器")
    ap.add_argument("--model", default="deepseek-chat", help="模型名（默认 deepseek-chat）")
    ap.add_argument("--time-step", default="30分钟", help="回合跨度，默认 30分钟")
    ap.add_argument("--turn", type=int, default=1)
    ap.add_argument("--out", default="probe_result.json", help="结果存档（不含 Key）")
    args = ap.parse_args()

    adj = adjudicator.adjudicate(SCENARIO, args.time_step, turn=args.turn)
    prompt = prompts.build_step_prompt(
        SCENARIO, args.turn, args.time_step, False, "", adjudication=adj)

    print("=" * 66)
    print(f"裁决指纹 {adj['fingerprint']} · 跨度 {args.time_step} · 地形 {adj['terrain']}")
    print("=" * 66)
    for row in adj["units"]:
        print(f"  {row['name']:<10} 可机动 {row['reach_km']:>5.2f} km | "
              f"最近敌 {row['nearest_enemy_km'] if row['nearest_enemy_km'] is not None else float('nan'):>5.2f} km | "
              f"补给 {row['supply']} | {row['encirclement']} | 退路 {row['retreat']} | "
              f"士气 {row['morale_state']}")

    if args.dry_run:
        print("\n[干跑] 使用故意违规的假响应，验证检测器有效性")
        result, raw = mock_response(SCENARIO, adj)
    else:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            print("\n✗ 未找到 DEEPSEEK_API_KEY。请先设置环境变量，或加 --dry-run 干跑。")
            return 2
        print(f"\n调用模型 {args.model} …")
        result, raw = call_model(api_key, args.model, prompt)

    new_state = result.get("updated_state", result)
    report = str(result.get("report") or "")

    violations = check_movement_ceiling(SCENARIO, new_state, adj)
    overreach = check_engagement_grade(new_state, adj)
    supply_hits = check_supply_narrative(report, adj)
    fp_a, fp_b, stable = check_reproducibility(SCENARIO, args.time_step, args.turn)

    print("\n" + "-" * 66)
    print("Q1 位移守住了吗")
    if violations:
        for v in violations:
            print(f"  ✗ {v['name']}：实走 {v['moved_km']} km，上限 {v['allowed_km']} km，"
                  f"超 {v['over_km']} km")
    else:
        print("  ✓ 全部单位在机动上限之内")

    print("Q2 交战越界了吗")
    if overreach:
        for o in overreach:
            print(f"  ✗ {o['attacker']}：{o['reason']}（「{o['description']}」）")
    else:
        print("  ✓ 没有「够不着却开打」的单位")

    print("Q3 状态认账了吗（启发式：句级匹配，可能漏报）")
    if supply_hits:
        for h in supply_hits:
            print(f"  ✗ 「{h['sentence']}」—— 报告称 {'、'.join(h['units'])} 补给"
                  f"「{'、'.join(h['phrases'])}」，但裁决判定该部已断粮")
    else:
        print("  ✓ 未发现「断粮单位 + 畅通措辞」同句出现的矛盾")

    print("Q4 裁决稳定吗")
    print(f"  {'✓' if stable else '✗'} 指纹 {fp_a} / {fp_b}")

    print("\n" + "-" * 66)
    print("战报正文（人眼复核 Q3 用）：")
    print("  " + (report.replace("\n", "\n  ") or "（空）"))

    payload = {
        "model": "dry-run" if args.dry_run else args.model,
        "time_step": args.time_step,
        "adjudicator": {k: adj[k] for k in ("version", "fingerprint", "turn", "terrain")},
        "checks": {
            "movement_violations": violations,
            "engagement_overreach": overreach,
            "supply_narrative_hits": supply_hits,
            "fingerprint_stable": stable,
        },
        "report": report,
        "units_after": new_state.get("red_units", []) + new_state.get("blue_units", []),
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已存档：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
