"""Offline provenance and catalog contracts, not proof of historical truth."""
import copy
import csv
import io
import json
from pathlib import Path
import unittest

from gentactix import adjudication_lib as lib, anchors, prompts, rag
from gentactix.unit_types import modern_unit_instruction
from tools.render_anti_japanese_war import render

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data/anti_japanese_war"
SCENARIOS = json.loads((DATA / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
SOURCES = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]
BY_ID = {s["id"]: s for s in SCENARIOS}


class AntiJapaneseWarTests(unittest.TestCase):
    def test_every_record_has_provenance_and_qualified_unknowns(self):
        self.assertEqual(len(SCENARIOS), 20)
        self.assertEqual(len(BY_ID), 20)
        aliases = []
        for s in SCENARIOS:
            with self.subTest(scenario=s["id"]):
                aliases.extend(s["aliases"])
                self.assertTrue(s["name"].startswith("抗日战争-"))
                self.assertTrue(s["uncertainties"] and s["scope"])
                self.assertGreaterEqual(len(s["events"]), 4)
                for row in s["events"] + s["forces"] + s["figures"]:
                    self.assertTrue(row["sources"])
                    self.assertTrue(set(row["sources"]) <= set(s["sources"]) <= set(SOURCES))
                    self.assertEqual(set(row["source_locator"]), set(row["sources"]))
                    self.assertIn(row["evidence_status"], {"published_account", "source_conflict", "unverified", "teaching_window"})
                for e in s["events"]:
                    self.assertIn(e["precision"], {"小时", "时段", "日", "日期范围"})
                    self.assertTrue(e["time_label"])
                for f in s["figures"]:
                    self.assertTrue(f["scope"] and f["note"])
                    if f["value"] is None:
                        self.assertEqual(f["evidence_status"], "unverified")
                    else:
                        self.assertGreater(f["value"], 0)
        self.assertEqual(len(aliases), len(set(aliases)))
        for source in SOURCES.values():
            self.assertTrue(source["url"].startswith(("https://", "http://")))
            self.assertTrue(source["publisher"] and source["evidence_type"] and source["caveat"])
            self.assertFalse(source["original_archive_examined"])

    def test_matrix_preserves_parent_scope_and_parallel_campaigns(self):
        rows = list(csv.DictReader(io.StringIO((DATA / "matrix.csv").read_text(encoding="utf-8"))))
        self.assertEqual({r["scenario_id"] for r in rows}, set(BY_ID))
        for row in rows:
            self.assertEqual(row["rule_status"], "teaching_only")
            self.assertEqual(row["parent_id"], BY_ID[row["scenario_id"]]["parent_id"])
            visited = {row["scenario_id"]}
            parent = row["parent_id"]
            while parent:
                self.assertIn(parent, BY_ID)
                self.assertNotIn(parent, visited)
                visited.add(parent)
                parent = BY_ID[parent]["parent_id"]
        expected = {"AJ03": "AJ02", "AJ05": "AJ04", "AJ07": "AJ06", "AJ13": "AJ12", "AJ17": "AJ16"}
        self.assertEqual({s["id"]: s["parent_id"] for s in SCENARIOS if s["parent_id"]}, expected)
        self.assertEqual(BY_ID["AJ12"]["phase"], BY_ID["AJ16"]["phase"])
        self.assertNotEqual(BY_ID["AJ12"]["local_posture"], BY_ID["AJ16"]["local_posture"])

    def test_catalog_aliases_and_estimate_labels(self):
        names = lib.list_battles()
        for s in SCENARIOS:
            with self.subTest(scenario=s["id"]):
                self.assertIn(s["name"], names)
                spec = lib.resolve(battle=s["name"])
                self.assertFalse(spec.errors)
                self.assertEqual(spec.validation_status, "teaching_estimate")
                self.assertIn("未经史实验证", spec.summary_line())
                self.assertEqual(spec.time_step_hint, s["time_step"])
                for alias in s["aliases"]:
                    self.assertEqual(lib.resolve("推演" + alias).name, s["name"])
                    self.assertNotIn(alias, names)
        # An explicit child selection wins even if the query mentions its parent.
        self.assertEqual(lib.resolve("徐州会战", battle=BY_ID["AJ05"]["name"]).name, BY_ID["AJ05"]["name"])

    def test_generated_exports_are_current(self):
        for path, expected in render().items():
            self.assertEqual(path.read_bytes(), expected.encode("utf-8"), str(path))
        for filename, field in (("timeline.csv", "events"), ("figures.csv", "figures"), ("forces.csv", "forces")):
            rows = list(csv.DictReader(io.StringIO((DATA / filename).read_text(encoding="utf-8"))))
            self.assertEqual(len(rows), sum(len(s[field]) for s in SCENARIOS))
            if field == "figures":
                self.assertTrue(any(r["value"] == "null" for r in rows))
                self.assertFalse(any(r["value"] == "0" for r in rows))
        gaps = list(csv.DictReader(io.StringIO((DATA / "verification_gaps.csv").read_text(encoding="utf-8"))))
        self.assertEqual({r["scenario_id"] for r in gaps}, set(BY_ID))

    def test_place_identity_uncertainty_and_selection(self):
        dataset = anchors.ANTI_JAPANESE_WAR_ANCHORS
        self.assertEqual(len(dataset["points"]), 28)
        # Homonymous places in other provinces must not leak into this series.
        for key, gid in {"changsha": 1815577, "cheqiao": 1815045, "huaian": 1797873, "hengshui": 1808392}.items():
            self.assertEqual(dataset["points"][key]["geoname_id"], gid)
        for s in SCENARIOS:
            nodes = anchors.collect_available_nodes("上甘岭和四渡赤水", battle=s["name"])
            self.assertEqual(len(nodes), len(s["points"]))
            for name, (lat, lng) in nodes.items():
                meta = anchors.node_metadata(name)
                self.assertIn("非战术阵地", meta["kind"])
                self.assertGreater(meta["uncertainty_km"], 0)
                self.assertTrue(21 < lat < 42 and 97 < lng < 123)
                self.assertIn(str(meta["geoname_id"]), meta["source_url"])
                self.assertTrue(set(meta["identity_sources"]) <= set(SOURCES))
            before = copy.deepcopy(nodes)
            next(iter(nodes.values()))[0] = 0
            self.assertEqual(anchors.collect_available_nodes("", battle=s["name"]), before)
        self.assertEqual(anchors.collect_available_nodes("抗日战争"), {})

    def test_retrieval_excludes_other_campaigns_and_other_series(self):
        for s in SCENARIOS:
            hits = rag.search("第三次长沙会战 长征 湘江 伤亡 指挥", battle=s["name"], top_k=12)
            self.assertTrue(hits)
            self.assertTrue(all(h["source"] == s["name"] + ".md" or h["source"].startswith("抗日战争/") for h in hits))
        for alias, prefix in (("上甘岭", "抗美援朝/"), ("湘江战役", "红军长征/")):
            selected = lib.resolve(alias).name
            hits = rag.search("抗日战争 百团大战 伤亡", battle=selected, top_k=12)
            self.assertTrue(hits)
            self.assertTrue(all(h["source"] == selected + ".md" or h["source"].startswith(prefix) for h in hits))

    def test_init_keeps_complete_scope_forces_and_uncertainties(self):
        for s in SCENARIOS:
            with self.subTest(scenario=s["id"]):
                hits = rag.search(s["name"], battle=s["name"], phase="init", top_k=8)
                before = copy.deepcopy(hits)
                passages = rag.select_passages(hits, 3200)
                required = [p for p in passages if p.get("required")]
                self.assertEqual({p["section"] for p in required}, {"时间与地点", "双方兵力与指挥", "争议与待考"})
                self.assertTrue(all(not p["excerpted"] for p in required))
                briefing, used = rag.build_briefing(hits, 3200)
                self.assertEqual(used, len(passages))
                self.assertLessEqual(len(briefing) - len(rag.BRIEFING_HEADER), 3200)
                self.assertEqual(hits, before)

    def test_conflicting_counts_and_clocks_are_not_silently_repaired(self):
        self.assertFalse(any(f["value"] in (197439, 19743) for f in BY_ID["AJ06"]["figures"]))
        self.assertTrue(any(f["value"] is None for f in BY_ID["AJ06"]["figures"]))
        shanghai_end = [e for e in BY_ID["AJ01"]["events"] if e.get("variant_group") == "shanghai_end"]
        self.assertEqual({e["date"] for e in shanghai_end}, {"1937-11-11", "1937-11-12"})
        self.assertTrue(all(e["evidence_status"] == "source_conflict" for e in shanghai_end))
        cheqiao = BY_ID["AJ17"]
        contradictory = [f for f in cheqiao["figures"] if f["value"] == 465]
        self.assertEqual(len(contradictory), 2)
        self.assertTrue(all(f["evidence_status"] == "source_conflict" for f in contradictory))
        clock = next(e for e in cheqiao["events"] if e["precision"] == "小时")
        self.assertEqual(clock["time_label"], "1时50分")
        self.assertTrue(any(e["date"] == "1944-03-13" and "扩展" in e["event"] for e in cheqiao["events"]))
        self.assertFalse(any(e["precision"] == "小时" for e in BY_ID["AJ05"]["events"]))

    def test_commanders_and_statistical_windows_are_separate(self):
        first = " ".join(f["description"] for f in BY_ID["AJ08"]["forces"])
        self.assertIn("冈村宁次", first)
        second = " ".join(f["description"] for f in BY_ID["AJ09"]["forces"])
        self.assertIn("阿南惟几", second)
        hundred = BY_ID["AJ14"]
        for f in hundred["figures"]:
            if f["value"] in (20645, 5155, 281, 18407):
                self.assertIn("12月5日", f["scope"])
        self.assertTrue(any(f["value"] == 67000 for f in BY_ID["AJ19"]["figures"]))
        self.assertFalse(any(f["value"] == 67000 for f in BY_ID["AJ12"]["figures"]))

    def test_era_guidance_covers_aliases_without_bare_year_trigger(self):
        for s in SCENARIOS:
            self.assertIn("【抗日战争兵种与资料边界】", prompts.build_init_prompt(s["name"], {}))
        for alias in ("平型关大捷", "台儿庄大捷", "桂南会战", "滇西缅北反攻"):
            self.assertIn("hp不等于人数", modern_unit_instruction(alias))
        self.assertEqual(modern_unit_instruction("1937年欧洲独立想定"), "")
        self.assertEqual(modern_unit_instruction("1944年欧洲独立想定"), "")
        self.assertIn("【红军长征", modern_unit_instruction("湘江战役"))
        self.assertIn("【近现代兵种", modern_unit_instruction("上甘岭"))

    def test_frozen_backend_has_all_scoped_anchor_files(self):
        script = (ROOT / "tools/build_linux.py").read_text(encoding="utf-8")
        for filename in ("korean_anchors.json", "long_march_anchors.json", "anti_japanese_war_anchors.json"):
            self.assertIn(repr(filename), script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
