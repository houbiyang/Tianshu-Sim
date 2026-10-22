"""Long March data provenance, catalog, scope, anchors and rendering regressions.

Offline contract checks do not prove historical truth or live-model compliance.
"""
import copy
import csv
import io
import json
from pathlib import Path
import unittest

from gentactix import adjudication_lib as lib, anchors, prompts, rag
from gentactix.unit_types import modern_unit_instruction
from tools.render_long_march import render

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data/long_march"
SCENARIOS = json.loads((DATA / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
SOURCES = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]


class LongMarchTests(unittest.TestCase):
    def test_all_editable_sources_have_provenance_and_explicit_unknowns(self):
        self.assertEqual(len(SCENARIOS), 15)
        self.assertEqual(len({s["id"] for s in SCENARIOS}), 15)
        aliases = []
        for s in SCENARIOS:
            with self.subTest(battle=s["name"]):
                aliases.extend(s["aliases"])
                self.assertTrue(s["name"].startswith("红军长征-"))
                self.assertTrue(s["uncertainties"])
                self.assertGreaterEqual(len(s["events"]), 4)
                for row in s["events"] + s["forces"] + s["figures"]:
                    self.assertTrue(row["sources"])
                    self.assertTrue(set(row["sources"]) <= set(s["sources"]))
                    self.assertTrue(set(row["sources"]) <= set(SOURCES))
                    self.assertIn(row["evidence_status"], {"published_account", "source_conflict", "teaching_window", "unverified"})
                for e in s["events"]:
                    self.assertIn(e["precision"], {"小时", "时段", "日", "日期范围"})
                    self.assertTrue(e["time_label"])
                for c in s["figures"]:
                    self.assertTrue(c["scope"] and c["note"])
                    self.assertTrue(c["value"] is None or c["value"] > 0)
        self.assertEqual(len(aliases), len(set(aliases)))
        for source in SOURCES.values():
            self.assertTrue(source["url"].startswith(("https://", "http://")))
            self.assertTrue(source["publisher"] and source["evidence_type"])
            self.assertFalse(source["original_archive_examined"])

    def test_catalog_aliases_and_geometry_contract(self):
        names = lib.list_battles()
        for s in SCENARIOS:
            with self.subTest(battle=s["name"]):
                self.assertIn(s["name"], names)
                resolved = lib.resolve(battle=s["name"])
                self.assertFalse(resolved.errors)
                self.assertTrue(resolved.calibrated)
                self.assertEqual(resolved.validation_status, "teaching_estimate")
                self.assertIn("未经史实验证", resolved.summary_line())
                self.assertNotIn("已标定", resolved.summary_line())
                self.assertEqual(resolved.time_step_hint, s["time_step"])
                for alias in s["aliases"]:
                    self.assertEqual(lib.resolve("推演" + alias).name, s["name"])
                    self.assertNotIn(alias, names)

    def test_exports_are_current_and_keep_unknown_distinct_from_zero(self):
        for path, expected in render().items():
            self.assertEqual(path.read_bytes(), expected.encode("utf-8"), str(path))
        events = list(csv.DictReader(io.StringIO((DATA / "timeline.csv").read_text())))
        self.assertEqual(len(events), sum(len(s["events"]) for s in SCENARIOS))
        figures = list(csv.DictReader(io.StringIO((DATA / "figures.csv").read_text())))
        self.assertTrue(any(row["value"] == "null" for row in figures))
        self.assertFalse(any(row["value"] == "0" for row in figures))
        forces = list(csv.DictReader(io.StringIO((DATA / "forces.csv").read_text())))
        self.assertEqual(len(forces), sum(len(s["forces"]) for s in SCENARIOS))
        gaps = list(csv.DictReader(io.StringIO((DATA / "verification_gaps.csv").read_text())))
        self.assertEqual({g["scenario_id"] for g in gaps}, {s["id"] for s in SCENARIOS})

    def test_anchor_provenance_and_no_automatic_mixing_of_river_crossings(self):
        for s in SCENARIOS:
            nodes = anchors.collect_available_nodes("其他战役", battle=s["name"])
            self.assertTrue(nodes)
            expected = anchors.LONG_MARCH_ANCHORS["campaigns"][s["name"]]["points"]
            self.assertEqual(len(nodes), len(expected))
            for name, (lat, lng) in nodes.items():
                meta = anchors.node_metadata(name)
                self.assertIn("非战术阵地", meta["kind"])
                self.assertGreater(meta["uncertainty_km"], 0)
                self.assertTrue(set(meta["identity_sources"]) <= set(SOURCES))
                self.assertIn(str(meta["geoname_id"]), meta["source_url"])
                self.assertTrue(24 < lat < 39 and 98 < lng < 115)
        old = anchors.collect_available_nodes("1935年巧渡金沙江")
        new = anchors.collect_available_nodes("1936年抢渡金沙江")
        self.assertTrue(old and new)
        self.assertFalse(set(old) & set(new))
        before = copy.deepcopy(old)
        next(iter(old.values()))[0] = 0
        self.assertEqual(anchors.collect_available_nodes("1935年巧渡金沙江"), before)
        self.assertEqual(anchors.collect_available_nodes("红军长征"), {})

    def test_retrieval_is_scoped_before_ranking_in_both_series(self):
        for s in SCENARIOS:
            hits = rag.search("兵力 伤亡 指挥", battle=s["name"], top_k=8)
            self.assertTrue(hits)
            self.assertTrue(all(h["source"] == s["name"] + ".md" or
                                h["source"].startswith("红军长征/") for h in hits))
        selected = lib.resolve("上甘岭").name
        hits = rag.search("湘江战役 四渡赤水 兵力", battle=selected, top_k=10)
        self.assertTrue(hits)
        self.assertTrue(all(h["source"] == selected + ".md" or
                            h["source"].startswith("抗美援朝/") for h in hits))
        selected = lib.resolve("1936年抢渡金沙江").name
        hits = rag.search("1935年巧渡金沙江 皎平渡 37名船工", battle=selected, top_k=10)
        self.assertTrue(hits)
        self.assertTrue(all(h["source"] == selected + ".md" or
                            h["source"].startswith("红军长征/") for h in hits))

    def test_source_clock_and_casualty_scope_regressions(self):
        by_id = {s["id"]: s for s in SCENARIOS}
        wujiang = by_id["LM02"]
        eight = next(f for f in wujiang["figures"] if f["value"] == 8)
        self.assertIn("回龙场", eight["scope"])
        self.assertIn("不能", eight["note"])
        self.assertTrue(any(f["value"] is None and f["metric"] == "战斗伤亡合计"
                            for f in wujiang["figures"]))
        crossing = next(e for e in wujiang["events"] if e["date"] == "1935-01-03" and e["precision"] == "小时")
        self.assertIn("9时", crossing["time_label"])
        self.assertEqual(crossing["sources"], ["S05"])
        self.assertEqual(crossing["evidence_status"], "published_account")
        self.assertIn("1935.11.24", by_id["LM14"]["name"])
        self.assertEqual(by_id["LM11"]["name"], "红军长征-百丈关战役-1935.11")
        self.assertIn("异文", " ".join(by_id["LM11"]["uncertainties"]) + by_id["LM11"]["scope"])

    def test_init_keeps_complete_scope_forces_and_uncertainties(self):
        for s in SCENARIOS:
            with self.subTest(battle=s["name"]):
                hits = rag.search(s["name"], battle=s["name"], phase="init", top_k=8)
                before = copy.deepcopy(hits)
                passages = rag.select_passages(hits, 3200)
                required = [p for p in passages if p.get("required")]
                self.assertEqual({p["section"] for p in required}, {"时间与地点", "双方兵力与指挥", "争议与待考"})
                self.assertTrue(all(not p["excerpted"] for p in required))
                text, used = rag.build_briefing(hits, 3200)
                self.assertEqual(used, len(passages))
                self.assertLessEqual(len(text) - len(rag.BRIEFING_HEADER), 3200)
                self.assertNotIn("500……", text)
                self.assertEqual(hits, before)

    def test_dates_command_chain_and_old_baozuo_alias(self):
        by_id = {s["id"]: s for s in SCENARIOS}
        events = by_id["LM06"]["events"]
        for date in ("1935-06-26", "1935-08-03", "1935-08-20", "1935-09-12"):
            self.assertTrue(any(e["date"] == date and "S26" in e["sources"] for e in events))
        tucheng = next(e for e in by_id["LM03"]["events"] if e["date"] == "1935-01-28")
        self.assertEqual(tucheng["sources"], ["S26"])
        old = "红军长征-包座战役-1935.08.29至1935.08.31"
        new = by_id["LM07"]["name"]
        self.assertNotIn(old, lib.list_battles())
        self.assertEqual(lib.resolve(battle=old).name, new)
        self.assertEqual(anchors.collect_available_nodes("", battle=old), anchors.collect_available_nodes("", battle=new))
        endings = [e for e in by_id["LM07"]["events"] if e.get("variant_group") == "baozuo_end"]
        self.assertEqual({e["date"] for e in endings}, {"1935-08-31", "1935-09-01"})
        self.assertTrue(all(e["evidence_status"] == "source_conflict" for e in endings))

    def test_era_guidance_does_not_change_unrelated_year_gates(self):
        for s in SCENARIOS:
            prompt = prompts.build_init_prompt(s["name"], {})
            self.assertIn("【红军长征兵种与资料边界】", prompt)
            self.assertIn("hp不等于人数", prompt)
        self.assertEqual(modern_unit_instruction("1935年独立欧洲想定"), "")
        self.assertEqual(modern_unit_instruction("长征运载火箭"), "")
        self.assertEqual(modern_unit_instruction("1951年中东独立想定"), "")
        self.assertIn("【近现代兵种", modern_unit_instruction("上甘岭战役"))

    def test_frozen_backend_includes_new_reference_data(self):
        script = (ROOT / "tools/build_linux.py").read_text()
        self.assertIn("'long_march_anchors.json'", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
