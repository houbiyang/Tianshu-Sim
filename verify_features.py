"""Offline regressions for retrieval, session isolation, geography and map rendering."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gentactix import geo, prompts, rag, state, viz


def battle():
    return {
        "center": [35.83, 112.85], "current_time": "秋", "current_terrain": "丘陵",
        "red_units": [{"name": "赵军", "type": "步兵", "lat": 35.83, "lng": 112.85,
                       "hp": 1000, "attack_range": 300, "speed": 5}],
        "blue_units": [{"name": "秦军", "type": "步兵", "lat": 35.84, "lng": 112.86,
                        "hp": 1000, "attack_range": 300, "speed": 5}],
        "engagements": [],
    }


class RetrievalTests(unittest.TestCase):
    def tearDown(self):
        rag.clear_cache()

    def test_real_corpus_and_unrelated_queries(self):
        stats = rag.corpus_stats()
        self.assertGreater(stats["chunks"], 0)
        hits = rag.search("长平之战 赵括 白起")
        self.assertTrue(hits)
        self.assertTrue(all("长平" in hit["doc_title"] for hit in hits))
        self.assertEqual(rag.search("中途岛海战"), [])
        briefing, used = rag.build_briefing(hits)
        self.assertGreater(used, 0)
        self.assertIn("来源：", briefing)
        self.assertIn(briefing, prompts.build_init_prompt("长平之战", {}, briefing))
        self.assertIn(briefing, prompts.build_step_prompt(battle(), 1, "30分钟", False, briefing))

    def test_markdown_chunks_frontmatter_exclusions_and_cache_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "battle.md"
            card.write_text("---\ncontact_km: 3\n---\n# 测试战役\n## 粮道\n运输受阻。\n", encoding="utf-8")
            for name in ["README.md", "_draft.md", ".hidden.md"]:
                (root / name).write_text("# 排除\n## 段落\n不该被检索。", encoding="utf-8")
            chunks = rag.load_corpus(root)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]["doc_title"], "测试战役")
            self.assertEqual(chunks[0]["section"], "粮道")
            self.assertNotIn("contact_km", chunks[0]["text"])
            card.write_text("# 测试战役\n## 粮道\n运输恢复，援军抵达。", encoding="utf-8")
            self.assertIn("援军", rag.load_corpus(root)[0]["text"])
            self.assertEqual(rag.load_corpus(root / "absent"), [])
        self.assertEqual(rag.build_briefing([]), ("", 0))
        self.assertEqual(rag.search("", corpus_dir=tmp), [])


class StateTests(unittest.TestCase):
    def test_session_defaults_are_independent_and_idempotent(self):
        sessions = [{}, {}]
        for session in sessions:
            with patch.object(state.st, "session_state", session):
                state.ensure_session_state()
        sessions[0]["history"].append("仅属于第一个会话")
        sessions[0]["chat_messages"].append({"role": "user", "content": "私有问题"})
        try:
            self.assertEqual(sessions[1]["history"], [])
            self.assertEqual(sessions[1]["chat_messages"], [])
            with patch.object(state.st, "session_state", sessions[0]):
                state.ensure_session_state()
            self.assertEqual(len(sessions[0]["history"]), 1)
        finally:
            # Also keep this regression safe on the pre-fix implementation.
            for key in ["history", "chat_messages"]:
                state.DEFAULT_SESSION_STATE[key].clear()

    def test_export_includes_battle_reports_review_and_chat(self):
        session = SimpleNamespace(
            turn_count=2, war_state=battle(), history=["中文战报"],
            review_reasoning="推理", review_text="复盘", chat_messages=[{"role": "user", "content": "问题"}],
        )
        with patch.object(state.st, "session_state", session):
            raw = state.generate_json_export()
        exported = json.loads(raw)
        self.assertIn("中文战报", raw)
        self.assertEqual(exported["war_state"], session.war_state)
        self.assertEqual(exported["chat_messages"], session.chat_messages)
        self.assertEqual(exported["review"]["conclusion"], "复盘")
        self.assertEqual(exported["turn_count"], 2)


class MapTests(unittest.TestCase):
    def test_sector_ring_invalid_entry_and_non_mutating_selftest(self):
        fixture = battle()
        fixture["engagements"] = [
            {"纬度": "35.84", "经度": "112.86", "描述": "测试交战", "攻击方": "赵军",
             "攻击者位置": {"纬度": 35.83, "经度": 112.85}},
            {"lat": 35.85, "lng": 112.87, "description": "方向未知"},
            {"description": "缺失坐标"},
        ]
        state.normalize_state_units(fixture)
        stats = viz.summarize_engagements(fixture)
        self.assertEqual((stats["valid"], stats["sector"], stats["ring"], stats["skipped"]), (2, 1, 1, 1))
        previous = copy.deepcopy(fixture)
        previous["red_units"][0]["lat"] -= 0.01
        html = viz.build_tactical_map(fixture, previous, 1).get_root().render()
        for marker in ["L.polygon", "L.circle", "L.polyline", "测试交战", "赵军", "World_Imagery"]:
            self.assertIn(marker, html)
        self.assertNotIn("webrd01.is.autonavi.com", html)
        before = copy.deepcopy(fixture)
        demo = viz.with_demo_engagement(fixture)
        self.assertEqual(fixture, before)
        self.assertEqual(len(demo["engagements"]), len(fixture["engagements"]) + 1)

    def test_missing_center_renders_with_warning(self):
        fixture = battle()
        fixture.pop("center")
        with patch.object(viz.st, "warning") as warning:
            self.assertIn("L.map", viz.build_tactical_map(fixture, None, 0).get_root().render())
        warning.assert_called_once()


class GeographyTests(unittest.TestCase):
    def test_real_land_mask_without_elevation_network(self):
        with patch.object(geo, "elevation_data", None):
            self.assertTrue(geo.is_water_precise(0, -140))
            self.assertFalse(geo.is_water_precise(39.9, 116.4))
            self.assertFalse(geo.is_water_precise(None, 0))

    def test_elevation_failure_and_naval_relocation(self):
        radar = Mock()
        radar.get_elevation.side_effect = OSError("offline")
        with patch.object(geo, "elevation_data", radar):
            self.assertFalse(geo.is_water_precise(39.9, 116.4))
        fixture = {"red_units": [{"名称": "测试舰", "类型": "驱逐舰", "纬度": 30, "经度": 120}],
                   "blue_units": []}
        with patch.object(geo, "force_into_water", return_value=(30.1, 120.1)) as relocate, \
                patch.object(geo.st, "toast"):
            geo.sanitize_step_units(fixture)
        relocate.assert_called_once_with(30, 120)
        self.assertEqual(fixture["red_units"][0]["lat"], 30.1)
        with patch.object(geo, "is_water_precise", side_effect=lambda lat, lng: lat > 30):
            lat, lng = geo.force_into_water(30, 120)
        self.assertGreater(lat, 30)
        self.assertEqual(geo.force_into_water(None, None), (None, None))

    def test_missing_elevation_keeps_land_mask_verdict(self):
        radar = Mock()
        radar.get_elevation.return_value = None
        with patch.object(geo, "elevation_data", radar):
            self.assertFalse(geo.is_water_precise(39.9, 116.4))
            radar.get_elevation.return_value = 0
            self.assertTrue(geo.is_water_precise(39.9, 116.4))


if __name__ == "__main__":
    unittest.main(verbosity=2)
