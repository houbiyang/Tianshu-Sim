"""战场展示契约、跨回合保留与实际渲染回归；不调用模型。"""

import copy
import json
import unittest

from gentactix import adjudicator, battlefield, prompts


def scenario():
    return {"center": [38, 127], "current_time": "1952年秋", "current_terrain": "丘陵森林",
            "red_units": [{"name": "炮兵", "type": "牵引炮兵", "lat": 38, "lng": 127,
                           "hp": 9999, "attack_range": 12000, "min_attack_range": 1000,
                           "fire_sector": {"bearing_deg": 180, "arc_deg": 90},
                           "personnel": {"committed": 1000, "current": 900, "casualties": 80, "noncombat_losses": 20, "source": "测试记录"},
                           "equipment": [{"name": "榴弹炮", "unit": "门", "committed": 12, "operational": 9, "damaged": 1, "destroyed": 2}]}],
            "blue_units": [{"name": "步兵", "type": "徒步步兵", "lat": 38.1, "lng": 127.1, "hp": 1000}],
            "terrain_features": [{"name": "测试山脊", "kind": "ridge", "geometry": "line", "coordinates": [[38, 127], [38.01, 127.02]], "source": "验收夹具"}],
            "engagements": []}


class BattlefieldTests(unittest.TestCase):
    def test_hp_is_never_headcount_and_missing_data_stays_unknown(self):
        view = battlefield.build_battlefield_view({"red_units": [{"name": "旧单位", "hp": 1000}], "blue_units": []})
        self.assertIsNone(view["losses"]["units"][0]["personnel"])
        self.assertIsNone(view["losses"]["teams"][0]["personnel"])
        self.assertEqual(view["losses"]["teams"][0]["recorded_units"], 0)
        self.assertEqual(view["supply_lines"], [])

    def test_personnel_and_equipment_rates_use_separate_recorded_denominators(self):
        view = battlefield.build_battlefield_view(scenario())
        row = view["losses"]["units"][0]
        self.assertEqual(row["personnel"]["loss_rate"], .08)
        self.assertEqual(row["personnel"]["secondary_rate"], .02)
        self.assertEqual(row["equipment"][0]["loss_rate"], 2/12)
        self.assertEqual(row["equipment"][0]["readiness_rate"], 9/12)
        self.assertEqual(view["losses"]["teams"][0]["recorded_units"], 1)
        self.assertIsNone(view["losses"]["teams"][1]["personnel"])

    def test_invalid_counts_and_zero_denominator_do_not_produce_fake_percentages(self):
        for changed in ({"committed": 0, "current": 0, "casualties": 0, "noncombat_losses": 0},
                        {"casualties": -1}, {"current": 9999}, {"current": 1.5},
                        {"casualties": float("nan")}, {"casualties": True}, {"casualties": None}):
            state = scenario()
            state["red_units"][0]["personnel"].update(changed)
            metric = battlefield.build_battlefield_view(state)["losses"]["units"][0]["personnel"]
            self.assertIsNone(metric["loss_rate"], changed)
        self.assertIsNone(battlefield.build_battlefield_view({"red_units": [{"personnel": {}}]})["losses"]["teams"][0]["personnel"])

    def test_weighted_totals_and_equipment_categories_are_not_mixed(self):
        state = scenario()
        other = copy.deepcopy(state["red_units"][0])
        other.update(name="增援", personnel={"committed": 100, "current": 50, "casualties": 50, "noncombat_losses": 0},
                     equipment=[{"name": "坦克", "unit": "辆", "committed": 10, "operational": 5, "damaged": 2, "destroyed": 3}])
        state["red_units"].append(other)
        team = battlefield.build_battlefield_view(state)["losses"]["teams"][0]
        self.assertAlmostEqual(team["personnel"]["loss_rate"], 130/1100)
        self.assertEqual({e["name"] for e in team["equipment"]}, {"坦克", "榴弹炮"})

    def test_active_and_archived_duplicates_cannot_inflate_totals(self):
        state = scenario()
        state["red_units"].append(copy.deepcopy(state["red_units"][0]))
        archive = dict(copy.deepcopy(state["red_units"][0]), team="red", status="已退场")
        state["loss_records"] = [archive, copy.deepcopy(archive)]
        view = battlefield.build_battlefield_view(state)
        team = view["losses"]["teams"][0]
        self.assertEqual(team["personnel"]["committed"], 1000)
        self.assertEqual(team["total_units"], 1)
        self.assertEqual(len(view["warnings"]), 3)

    def test_archive_status_text_cannot_turn_retired_people_into_available_strength(self):
        state = scenario()
        record = dict(copy.deepcopy(state["red_units"][0]), team="red", name="退场炮兵", status="当前编组")
        state["loss_records"] = [record]
        team = battlefield.build_battlefield_view(state)["losses"]["teams"][0]
        self.assertEqual(team["active_current"], 900)
        self.assertEqual(team["archived_units"], 1)

    def test_cumulative_denominator_reset_is_rejected_without_changing_previous_state(self):
        for field in ("personnel", "equipment"):
            old = scenario()
            untouched = copy.deepcopy(old)
            new = copy.deepcopy(old)
            target = new["red_units"][0][field]
            if field == "equipment": target = target[0]
            target["committed"] = 1
            with self.assertRaisesRegex(ValueError, "累计投入"):
                battlefield.carry_battlefield_state(old, new)
            self.assertEqual(old, untouched)

    def test_equipment_omitting_unit_does_not_duplicate_a_known_category(self):
        old = scenario()
        new = copy.deepcopy(old)
        del new["red_units"][0]["equipment"][0]["unit"]
        battlefield.carry_battlefield_state(old, new)
        gear = new["red_units"][0]["equipment"]
        self.assertEqual(len(gear), 1)
        self.assertEqual(gear[0]["unit"], "门")

    def test_directional_range_uses_geodesic_annular_sector_not_target_blast(self):
        state = scenario()
        view = battlefield.build_battlefield_view(state)
        r, s = view["fire_ranges"][0], view["fields_of_fire"][0]
        self.assertFalse(r["estimated"])
        self.assertEqual(r["max_range_m"], 12000)
        self.assertEqual(s["min_range_m"], 1000)
        for point in s["coordinates"]:
            distance = adjudicator.distance_km((38, 127), point)
            self.assertTrue(.99 < distance < 12.01, distance)
        self.assertTrue(all(p[0] < 38 for p in s["coordinates"]))
        del state["red_units"][0]["fire_sector"]
        del state["red_units"][0]["attack_range"]
        view = battlefield.build_battlefield_view(state)
        self.assertEqual(view["fields_of_fire"], [])
        self.assertTrue(view["fire_ranges"][0]["estimated"])

    def test_invalid_geometry_is_rejected_without_inventing_features(self):
        state = scenario()
        state["terrain_features"][0]["coordinates"][0] = [999, 127]
        state["red_units"][0]["fire_sector"]["bearing_deg"] = "nan"
        view = battlefield.build_battlefield_view(state)
        self.assertEqual(view["terrain"]["features"], [])
        self.assertEqual(view["fields_of_fire"], [])
        self.assertEqual(len(view["warnings"]), 2)
        self.assertEqual(battlefield.build_battlefield_view({"current_terrain": "高山"})["terrain"]["features"], [])

    def test_supply_prefers_recorded_route_then_node_then_geometry(self):
        state = scenario()
        adj = adjudicator.adjudicate(state)
        self.assertTrue(adj["units"][0]["supply_path"])
        geometry = battlefield.build_battlefield_view(state, adj)["supply_lines"][0]
        self.assertTrue(geometry["estimated"])
        self.assertIn("非真实道路", geometry["source"])
        state["supply_nodes"] = [{"id": "r1", "name": "仓库", "team": "red", "lat": 37.9, "lng": 127}]
        state["red_units"][0]["supply_node_id"] = "r1"
        line = battlefield.build_battlefield_view(state, adj)["supply_lines"][0]
        self.assertEqual(line["coordinates"][0], [37.9, 127])
        state["supply_routes"] = [{"team": "red", "unit_name": "炮兵", "waypoints": [[37.9, 127], [37.95, 127.01], [38, 127]], "status": "cut", "source": "测试路线"}]
        lines = battlefield.build_battlefield_view(state, adj)["supply_lines"]
        self.assertFalse(lines[0]["estimated"])
        self.assertEqual(lines[0]["status"], "cut")
        self.assertEqual(len(lines), 2)

    def test_nested_partial_updates_preserve_baselines_zeroes_and_equipment_types(self):
        prev = scenario()
        prev["red_units"][0]["equipment"].append({"name": "卡车", "unit": "辆", "committed": 3, "operational": 3, "damaged": 0, "destroyed": 0})
        original = copy.deepcopy(prev)
        current = {"red_units": [{"name": "炮兵", "personnel": {"current": 800, "casualties": 180, "noncombat_losses": 20},
                                  "equipment": [{"name": "榴弹炮", "unit": "门", "operational": 0, "destroyed": 11}]}], "blue_units": []}
        battlefield.carry_battlefield_state(prev, current)
        u = current["red_units"][0]
        self.assertEqual(u["personnel"]["committed"], 1000)
        self.assertEqual(u["equipment"][0]["operational"], 0)
        self.assertEqual(u["equipment"][0]["committed"], 12)
        self.assertEqual(len(u["equipment"]), 2)
        self.assertEqual(current["terrain_features"], prev["terrain_features"])
        self.assertEqual(prev, original)
        battlefield.carry_battlefield_state(prev, current)
        self.assertEqual(len(u["equipment"]), 2)

    def test_removed_units_remain_in_loss_ledger_across_multiple_turns(self):
        prev = scenario()
        current = {"red_units": [], "blue_units": [], "eliminated": ["炮兵"]}
        battlefield.carry_battlefield_state(prev, current)
        self.assertEqual(current["loss_records"][0]["personnel"]["current"], 900)
        next_state = {"red_units": [], "blue_units": []}
        battlefield.carry_battlefield_state(current, next_state)
        view = battlefield.build_battlefield_view(next_state)
        self.assertEqual(view["losses"]["teams"][0]["personnel"]["loss_rate"], .08)
        self.assertEqual(view["losses"]["teams"][0]["active_current"], 0)
        self.assertIn("未推定全员伤亡", view["losses"]["units"][0]["status"])
        self.assertIn("loss_records", json.loads(json.dumps(next_state)))
        returned = {"red_units": [{"name": "炮兵", "personnel": {"current": 850}}], "blue_units": []}
        battlefield.carry_battlefield_state(next_state, returned)
        self.assertEqual(returned["red_units"][0]["personnel"]["committed"], 1000)
        self.assertEqual(battlefield.build_battlefield_view(returned)["losses"]["teams"][0]["archived_units"], 0)

    def test_view_is_readonly_and_json_serializable(self):
        state = scenario()
        original = copy.deepcopy(state)
        view = battlefield.build_battlefield_view(state, adjudicator.adjudicate(state))
        self.assertEqual(state, original)
        json.dumps(view, allow_nan=False)
        self.assertEqual(view, battlefield.build_battlefield_view(state, adjudicator.adjudicate(state)))

    def test_folium_has_optional_layers_and_escapes_source_text(self):
        try:
            from gentactix import viz
        except ModuleNotFoundError as exc:
            if exc.name in ("streamlit", "folium"):
                self.skipTest("Streamlit地图在.venv环境验收")
            raise
        state = scenario()
        state["terrain_features"][0]["name"] = "<script>bad()</script>"
        view = battlefield.build_battlefield_view(state, adjudicator.adjudicate(state))
        html = viz.build_tactical_map(state, None, 0, view).get_root().render()
        for label in ("地形要素", "火力射程", "已知射界", "补给节点与路线"):
            self.assertTrue(label in html or json.dumps(label)[1:-1] in html)
        self.assertNotIn("<script>bad()</script>", html)
        self.assertIn("非真实道路", html)

    def test_prompt_declares_data_contract_without_claiming_los_calculation(self):
        prompt = prompts.build_init_prompt("上甘岭战役", {})
        for field in ("personnel", "equipment", "supply_routes", "fire_sector", "terrain_features", "loss_records"):
            self.assertIn(field, prompt)
        self.assertIn("不得拿hp换算人数", prompt)
        self.assertIn("committed不重置", prompts.build_step_prompt(scenario(), 1, "1小时", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
