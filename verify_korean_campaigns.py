"""九次战役资料、别名、检索隔离和近现代兵种行为回归；不调用模型。"""

import copy
import json
from pathlib import Path
import unittest

from gentactix import adjudication_lib as lib, adjudicator as adj, prompts, rag
from gentactix.unit_types import UNIT_PROFILES, classify_unit, unit_symbol

ROOT = Path(__file__).resolve().parent


class KoreanCampaignTests(unittest.TestCase):
    def setUp(self):
        self.files = sorted((ROOT / "adjudication").glob("抗美援朝-*.md"))

    def test_nine_complete_paired_archives_and_valid_presets(self):
        self.assertEqual(len(self.files), 9)
        for path in self.files:
            with self.subTest(battle=path.stem):
                spec = lib.resolve(battle=path.stem)
                self.assertEqual(spec.source_kind, "library")
                self.assertFalse(spec.errors)
                self.assertTrue(spec.calibrated)
                self.assertIn(spec.time_step_hint, {"6小时", "1小时", "30分钟", "15分钟"})
                self.assertGreater(adj.parse_time_step(spec.time_step_hint), 0)
                card = ROOT / "corpus" / path.name
                text = card.read_text(encoding="utf-8")
                for heading in ("时间与地点", "双方兵力与指挥", "作战过程要点", "伤亡与结果", "对推演建模的关键约束", "兵种与装备", "资料与出处"):
                    self.assertIn("## " + heading, text)
                self.assertGreaterEqual(text.count("](https://") + text.count("](http://"), 3)
                self.assertGreater(len(text), 1200)

    def test_aliases_resolve_to_canonical_names_without_duplicate_entries(self):
        names = lib.list_battles()
        for path in self.files:
            for alias in lib.battle_aliases(path):
                with self.subTest(alias=alias):
                    self.assertEqual(lib.resolve("推演" + alias).name, path.stem)
                    self.assertEqual(lib.resolve(battle=alias).name, path.stem)
                    self.assertNotIn(alias, names)
        self.assertIn("1950.11.07", lib.resolve("第二次战役").name)
        self.assertIn("1953.05.13", lib.resolve("金城战役").name)

    def test_battle_retrieval_excludes_other_campaign_statistics(self):
        for path in self.files:
            alias = lib.battle_aliases(path)[0]
            with self.subTest(alias=alias):
                hits = rag.search(alias + " 兵力 伤亡", top_k=12)
                battle_hits = [h for h in hits if h["source"].startswith("抗美援朝-")]
                self.assertTrue(battle_hits)
                self.assertEqual({h["source"] for h in battle_hits}, {path.name})
        # 显式指定必须在排名前限定来源，而非先取别场前四段再过滤。
        selected = lib.resolve("上甘岭").name
        hits = rag.search("第五次战役 第180师", battle=selected, top_k=8)
        self.assertTrue(hits)
        self.assertTrue(all(not h["source"].startswith("抗美援朝-") or
                            h["source"] == selected + ".md" for h in hits))
        self.assertEqual(rag.search("中途岛海战"), [])

    def test_modern_types_do_not_match_generic_substrings(self):
        for profile in UNIT_PROFILES:
            self.assertEqual(classify_unit(profile["label"])["id"], profile["id"])
        cases = {"机械化步兵师": "mechanized", "摩托化步兵": "motorized",
                 "机枪分队": "machine_gun", "高射机枪": "air_defense",
                 "自行炮兵营": "sp_artillery", "反坦克炮": "anti_tank",
                 "工兵旅": "engineer", "炮兵指挥所": "command",
                 "汽车运输部队": "transport", "陆战队": "infantry"}
        for text, expected in cases.items():
            self.assertEqual(classify_unit(text)["id"], expected, text)
        self.assertNotEqual(unit_symbol("机枪分队"), "∞")
        self.assertIsNone(classify_unit("骑射"))

    def test_agent_history_tool_uses_current_battle_scope(self):
        try:
            from backend import agent_tools
        except ImportError:
            self.skipTest("Harness SDK未安装；在wargame环境执行此项")
        selected = lib.resolve("上甘岭").name
        result = agent_tools.exec_retrieve_history(
            {"use_rag": True, "scenario_input": "上甘岭战役", "battle_choice": selected,
             "history": []}, {"query": "第五次战役 第180师 伤亡"})
        self.assertTrue(result["passages"])
        self.assertTrue(all(p["doc_title"] == selected or
                            p["doc_title"] == "抗美援朝兵种与统计口径" for p in result["passages"]))

    def test_motor_transport_and_armor_mobility_preserve_legacy(self):
        foot = adj.unit_march_speed_kmh("徒步步兵", 5)
        self.assertGreater(adj.unit_march_speed_kmh("摩托化步兵", 5), foot)
        self.assertGreater(adj.unit_march_speed_kmh("坦克", 5), foot)
        self.assertEqual(adj.unit_march_speed_kmh("骑兵", 5), 7.5)
        self.assertEqual(adj.unit_march_speed_kmh("战车", 5), 2.5)
        self.assertEqual(adj.unit_attack_range_m({"type": "牵引炮兵", "attack_range": 7200}), 7200)
        self.assertEqual(adj.unit_attack_range_m({"type": "牵引炮兵"}), 10000)

    def test_support_range_cannot_create_ground_encirclement(self):
        red = {"name": "步兵", "type": "徒步步兵", "lat": 38.0, "lng": 127.0, "hp": 1000}
        blue = {"name": "炮兵", "type": "牵引炮兵", "lat": 38.01, "lng": 127.0,
                "hp": 1000, "attack_range": 20000}
        state = {"red_units": [red], "blue_units": [blue]}
        result = adj.adjudicate(state)
        row = next(r for r in result["units"] if r["name"] == "步兵")
        self.assertEqual(adj.unit_control_radius_km(blue), 0)
        self.assertEqual(row["encirclement"], "未被围")
        self.assertNotEqual(row["supply"], "已切断")
        # 换成同位置的战斗步兵，既有几何封锁应仍然有效。
        infantry = copy.deepcopy(state)
        infantry["blue_units"][0]["type"] = "徒步步兵"
        self.assertEqual(adj.adjudicate(infantry)["units"][0]["encirclement"], "被围")

    def test_air_and_naval_support_are_not_ground_formations(self):
        state = {"red_units": [{"name": "航空", "type": "航空支援", "lat": 38, "lng": 127},
                               {"name": "舰炮", "type": "驱逐舰", "lat": 38, "lng": 128}],
                 "blue_units": []}
        original = copy.deepcopy(state)
        result = adj.adjudicate(state)
        self.assertEqual(result["units"], [])
        self.assertEqual(len(result["skipped_units"]), 2)
        self.assertEqual(state, original)

    def test_historical_initialization_and_unit_guidance(self):
        prompt = prompts.build_init_prompt("上甘岭战役，1952年10月14日", {})
        self.assertIn("不得把1951—1953年场景倒退到1950年", prompt)
        self.assertIn("摩托化表示汽车输送", prompt)
        self.assertIn("无人机", prompt)
        self.assertNotIn("【近现代兵种与历史适用性】", prompts.build_init_prompt("长平之战", {}))
        self.assertNotIn("【近现代兵种与历史适用性】", prompts.build_init_prompt("1951年中东地区的独立想定", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
