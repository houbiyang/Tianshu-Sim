"""🛡️ GenTactix 桌面后端（backend/main.py）离线端到端回归。

不打桩编排层：锚点吸附、坐标审计、名单守恒、裁决内核、RAG 元数据、
快照与导出全部真实执行；只有 harness_engine.complete（模型出口）用脚本化
返回值替换，因此不需要密钥、不联网调模型。

覆盖旧 Streamlit 验收里「应用完整流程 / 会话与导出」在桌面端的等价物：
初始化→推进→复盘→问答→导出、API 失败保留原战局、面板补算兜底、
会话隔离、问答限长、导出文件名。

在 conda wargame 环境运行：
    conda run -n wargame python verify_desktop_backend.py
缺少 fastapi / harness SDK 的环境（如 .venv）自动跳过，verify_all 照常绿。
"""

import copy
import json
import sys
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from backend import harness_engine, main as backend_main
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    from gentactix import adjudicator
    _HAS_BACKEND = True
except Exception:  # noqa: BLE001  缺 fastapi / deepseek_harness 时整类跳过
    _HAS_BACKEND = False


def _unit(name, lat, lng, hp=5000, utype="步兵"):
    return {"name": name, "type": utype, "hp": hp, "lat": lat, "lng": lng}


def _init_state(minutes=0):
    # 长平一带陆地坐标：坐标审计（海陆面具）必须真实通过
    return {
        "current_time": f"前262年四月 · T+{minutes}分钟" if minutes else "前262年四月",
        "current_terrain": "山地",
        "situation_report": "秦赵两军沿丹水对峙，壁垒相望。",
        "source_note": "《史记·白起王翦列传》",
        "red_units": [
            _unit("秦军左翼", 35.700, 112.800),
            _unit("秦军辎重队", 35.680, 112.760, hp=3000, utype="辎重"),
        ],
        "blue_units": [
            _unit("赵军壁垒", 35.720, 112.830),
            _unit("赵军粮道", 35.740, 112.860, hp=2000, utype="辎重"),
        ],
    }


@unittest.skipUnless(_HAS_BACKEND, "当前环境缺 fastapi 或 harness SDK（.venv 属正常）")
class DesktopBackendTest(unittest.TestCase):
    def setUp(self):
        self.session_directory = tempfile.TemporaryDirectory()
        self.session_env = patch.dict(os.environ, {'GENTACTIX_SESSION_DIR': self.session_directory.name})
        self.session_env.start()
        backend_main._SESSIONS.clear()
        harness_engine.ENGINE_NOTICES.clear()
        harness_engine._role_sessions.clear()
        harness_engine._role_amnesia.clear()
        from gentactix import geo
        geo.GEO_NOTICES.clear()
        self.prompts = []
        self.responses = []
        # 打桩点必须是 complete_ex：三脑全部经它出门（complete 只是它的文本壳）。
        # 打 complete 的旧桩会漏过去 → 真 harness 被拉起、真连局域网 vLLM。
        self._real_complete_ex = harness_engine.complete_ex
        harness_engine.complete_ex = self._fake_complete_ex
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        self.session_env.stop()
        self.session_directory.cleanup()
        harness_engine.complete_ex = self._real_complete_ex
        backend_main._SESSIONS.clear()
        harness_engine._role_sessions.clear()
        harness_engine._role_amnesia.clear()

    def _fake_complete_ex(self, prompt, *, session_id=None, on_notification=None):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("脚本化模型回复已用尽")
        text = self.responses.pop(0)
        return SimpleNamespace(final_response=text, finish_reason="completed",
                               events=[], notifications=[],
                               session_id=session_id or "")

    def _create(self, adjudicator_on=True, rag_on=False, agent_on=False):
        res = self.client.post("/api/sessions", json={
            "scenario": "长平之战，前262年，秦赵上党对峙",
            "time_step": "30分钟",
            "use_adjudicator": adjudicator_on,
            "battle": None,
            "use_rag": rag_on,
            "use_live_intel": False,
            "use_agent_tools": agent_on,
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["session_id"]

    def _init(self, sid):
        self.responses.append(json.dumps(_init_state(), ensure_ascii=False))
        res = self.client.post(f"/api/sessions/{sid}/init")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    # ---- 健康与守卫 ----
    def test_empty_recovery_commits_exactly_one_turn_and_failure_preserves_checkpoint(self):
        from verify_harness_engine import FakeHarness
        from backend import session_store
        for outputs, expected in ((['', json.dumps({'updated_state': _init_state(30), 'report': '推进完毕'})], 200),
                                  (['', ''], 502), (['', 'broken JSON'], 502)):
            sid = self._create(); self._init(sid)
            before = copy.deepcopy(backend_main._SESSIONS[sid])
            fake = FakeHarness(outputs)
            with patch.object(harness_engine, 'complete_ex', self._real_complete_ex), \
                 patch.object(harness_engine, 'get_harness', return_value=fake):
                response = self.client.post(f'/api/sessions/{sid}/next')
            self.assertEqual(response.status_code, expected, response.text)
            after = backend_main._SESSIONS[sid]
            self.assertEqual(len(fake.prompts), 2)
            if expected == 200:
                self.assertEqual(after['turn_count'], before['turn_count'] + 1)
                self.assertEqual(len(after['history']), len(before['history']) + 1)
                self.assertEqual(len(after['trajectory']), len(before['trajectory']) + 1)
                self.assertEqual(after['war_state']['current_time'], '前262年四月 · T+30分钟')
                self.assertEqual(after['engine_last_meta']['empty_recoveries'], 1)
            else:
                for field in ('war_state', 'last_war_state', 'turn_count', 'history', 'trajectory', 'decision_log'):
                    self.assertEqual(after[field], before[field], field)
                    self.assertEqual(session_store.load_all()[0][sid][field], before[field], field)
                self.assertEqual(len(after['failed_operations']), len(before.get('failed_operations', [])) + 1)

    def test_cancel_during_empty_result_does_not_commit_or_retry(self):
        from verify_harness_engine import FakeHarness
        sid = self._create(); self._init(sid)
        before = copy.deepcopy(backend_main._SESSIONS[sid])
        fake = FakeHarness([''])
        original = fake.run
        def cancel_after_run(*args, **kwargs):
            result = original(*args, **kwargs)
            backend_main._SESSIONS[sid]['cancel_requested'] = True
            return result
        fake.run = cancel_after_run
        with patch.object(harness_engine, 'complete_ex', self._real_complete_ex), \
             patch.object(harness_engine, 'get_harness', return_value=fake):
            response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(fake.prompts), 1)
        for field in ('war_state', 'turn_count', 'history', 'trajectory'):
            self.assertEqual(backend_main._SESSIONS[sid][field], before[field])

    def test_health_exposes_versions(self):
        h = self.client.get("/api/health").json()
        self.assertEqual(h["engine"], "deepseek-harness")
        self.assertEqual(h["adjudicator_version"], "geo-5")

    def test_delete_removes_only_selected_checkpoint_and_role_handles(self):
        from backend import session_store
        sid, other = self._create(), self._create()
        self._init(sid); self._init(other)
        before = copy.deepcopy(backend_main._SESSIONS[other])
        role, other_role = f'gentactix-{sid}e0-engine', f'gentactix-{other}e0-engine'
        harness_engine._role_sessions.update({role: object(), other_role: object()})
        response = self.client.delete(f'/api/sessions/{sid}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['audit_retained'])
        self.assertNotIn(role, harness_engine._role_sessions)
        self.assertIn(other_role, harness_engine._role_sessions)
        self.assertEqual(self.client.get(f'/api/sessions/{sid}').status_code, 404)
        restored, errors = session_store.load_all()
        self.assertFalse(errors)
        self.assertNotIn(sid, restored)
        self.assertEqual(restored[other]['war_state'], before['war_state'])
        self.assertEqual(backend_main._SESSIONS[other], before)

    def test_delete_disk_failure_preserves_battle(self):
        from backend import session_store
        sid = self._create(); self._init(sid)
        before = copy.deepcopy(backend_main._SESSIONS[sid])
        with patch.object(session_store, 'remove', side_effect=PermissionError('fixture')):
            response = self.client.delete(f'/api/sessions/{sid}')
        self.assertEqual(response.status_code, 500)
        self.assertIn('战局已保留', response.json()['detail'])
        self.assertEqual(backend_main._SESSIONS[sid], before)
        self.assertIn(sid, session_store.load_all()[0])

    def test_delete_rejects_active_operation_foreign_origin_and_unknown_id(self):
        sid = self._create()
        backend_main._OP_LOCK.acquire()
        try:
            self.assertEqual(self.client.delete(f'/api/sessions/{sid}').status_code, 409)
        finally:
            backend_main._OP_LOCK.release()
        self.assertEqual(self.client.delete(f'/api/sessions/{sid}', headers={'Origin': 'https://example.org'}).status_code, 403)
        self.assertEqual(self.client.delete('/api/sessions/not-a-saved-id').status_code, 404)
        self.assertIn(sid, backend_main._SESSIONS)

    def test_battlefield_snapshot_carries_nested_metrics_and_archived_losses(self):
        sid = self._create()
        initial = _init_state()
        name = initial["red_units"][0]["name"]
        initial["red_units"][0].update(
            attack_range=8000, min_attack_range=1000,
            fire_sector={"bearing_deg": 90, "arc_deg": 80},
            personnel={"committed": 1000, "current": 900, "casualties": 80, "noncombat_losses": 20},
            equipment=[{"name": "火炮", "unit": "门", "committed": 10, "operational": 8, "damaged": 1, "destroyed": 1}])
        self.responses.append(json.dumps(initial))
        response = self.client.post(f"/api/sessions/{sid}/init")
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()["battlefield"]
        self.assertEqual(view["losses"]["units"][0]["personnel"]["loss_rate"], .08)
        self.assertEqual(view["fields_of_fire"][0]["min_range_m"], 1000)
        self.assertTrue(view["supply_lines"])

        updated = _init_state(30)
        updated["red_units"][0]["personnel"] = {"current": 800, "casualties": 180}
        self.responses.append(json.dumps({"updated_state": updated, "report": "测试减员"}))
        response = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(response.status_code, 200, response.text)
        unit = response.json()["war_state"]["red_units"][0]
        self.assertEqual(unit["personnel"]["committed"], 1000)
        self.assertEqual(unit["personnel"]["noncombat_losses"], 20)
        self.assertEqual(unit["equipment"][0]["destroyed"], 1)

        retired = _init_state(60)
        retired["red_units"] = retired["red_units"][1:]
        retired["eliminated"] = [name]
        self.responses.append(json.dumps({"updated_state": retired, "report": "测试退场"}))
        response = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(response.status_code, 200, response.text)
        archived = response.json()["war_state"]["loss_records"]
        self.assertEqual(archived[0]["personnel"]["casualties"], 180)
        self.assertEqual(response.json()["battlefield"]["losses"]["teams"][0]["personnel"]["loss_rate"], .18)
        exported = self.client.get(f"/api/sessions/{sid}/export").json()
        self.assertEqual(exported["war_state"]["loss_records"], archived)

    def test_personnel_denominator_reset_refuses_turn_and_preserves_state(self):
        sid = self._create()
        initial = _init_state()
        initial["red_units"][0]["personnel"] = {
            "committed": 1000, "current": 900, "casualties": 80, "noncombat_losses": 20}
        self.responses.append(json.dumps(initial))
        response = self.client.post(f"/api/sessions/{sid}/init")
        self.assertEqual(response.status_code, 200, response.text)
        before = copy.deepcopy(backend_main._SESSIONS[sid])
        invalid = copy.deepcopy(initial)
        invalid["current_time"] = "前262年四月 · T+30分钟"
        invalid["red_units"][0]["personnel"] = {
            "committed": 100, "current": 50, "casualties": 40, "noncombat_losses": 10}
        self.responses.append(json.dumps({"updated_state": invalid, "report": "错误分母"}))
        response = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(response.status_code, 502)
        self.assertIn("累计投入", response.text)
        for field in ("war_state", "turn_count", "history", "adj_last"):
            self.assertEqual(backend_main._SESSIONS[sid][field], before[field], field)

    def test_hp_panel_rebuilds_from_exported_history_without_count_fabrication(self):
        sid = self._create(adjudicator_on=False)
        self._init(sid)
        updated = _init_state(30)
        updated['red_units'][0]['hp'] = 4500
        self.responses.append(json.dumps({'updated_state': updated, 'report': 'HP下降测试'}))
        response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()['battlefield']
        row = next(u for u in view['operational']['units'] if u['name'] == '秦军左翼')
        self.assertEqual((row['hp_change'], row['hp_decline_rate']), (-500, .1))
        self.assertIsNone(view['losses']['teams'][0]['personnel'])
        from backend import session_store
        restored, errors = session_store.load_all()
        self.assertFalse(errors)
        backend_main._SESSIONS[sid] = restored[sid]
        self.assertEqual(self.client.get(f'/api/sessions/{sid}').json()['battlefield'], view)

    def test_battles_carry_time_step_hint(self):
        items = self.client.get("/api/battles").json()["battles"]
        cp = next(b for b in items if b["name"] == "长平之战")
        self.assertEqual(cp["time_step"], "6小时")
        long_march = [b for b in items if b["name"].startswith("红军长征-")]
        self.assertEqual(len(long_march), 15)
        self.assertEqual(len({b["name"] for b in long_march}), 15)
        bridge = next(b for b in long_march if "飞夺泸定桥" in b["name"])
        self.assertEqual(bridge["time_step"], "30分钟")
        anti_japanese = [b for b in items if b["name"].startswith("抗日战争-")]
        self.assertEqual(len(anti_japanese), 20)
        self.assertEqual(len({b["name"] for b in anti_japanese}), 20)
        self.assertEqual(len([b for b in anti_japanese if "长沙会战" in b["name"]]), 3)
        cheqiao = next(b for b in anti_japanese if "车桥战役" in b["name"])
        self.assertEqual(cheqiao["time_step"], "30分钟")

    def test_create_and_session_guards(self):
        self.assertEqual(self.client.post("/api/sessions", json={"scenario": "  "}).status_code, 422)
        self.assertEqual(self.client.get("/api/sessions/nope").status_code, 404)
        self.assertEqual(self.client.post("/api/sessions/nope/init").status_code, 404)
        sid = self._create()
        # 未初始化就推进 / 复盘 / 问答：与 app.py 同样的前置拦截，且必须是可见错误
        self.assertEqual(self.client.post(f"/api/sessions/{sid}/next").status_code, 409)
        self.assertEqual(self.client.post(f"/api/sessions/{sid}/review").status_code, 409)
        self.assertEqual(self.client.post(f"/api/sessions/{sid}/chat", json={"message": "在吗"}).status_code, 409)
        self._init(sid)  # 空问题校验在初始化守卫之后才可达
        self.assertEqual(self.client.post(f"/api/sessions/{sid}/chat", json={"message": "  "}).status_code, 422)

    # ---- 初始化：快照字段与旧版逐项对齐 ----
    def test_init_snapshot_contract(self):
        sid = self._create()
        snap = self._init(sid)
        self.assertEqual(snap["turn_count"], 0)
        self.assertEqual(len(snap["history"]), 1)
        self.assertIn("T+0", snap["history"][0])
        self.assertEqual(len(snap["war_state"]["red_units"]), 2)
        self.assertEqual(len(snap["war_state"]["blue_units"]), 2)
        # 裁决开启：T+0 也裁一次，指纹必须存在（可复现凭证）
        self.assertTrue(snap["adjudication"]["enabled"])
        self.assertIsNotNone(snap["adjudication"]["last"])
        self.assertEqual(len(snap["adjudication"]["last"]["fingerprint"]), 12)
        self.assertIsNotNone(snap["adjudication"]["spec"])
        self.assertIsNotNone(snap["adjudication"]["spec"]["source"])  # 长平有裁决库文件
        self.assertEqual(snap["adjudication"]["spec"]["time_step_hint"], "6小时")
        # 地形系数按战役标定覆盖模型标签（测试状态没写 current_terrain → 模型标签为空）
        self.assertEqual(snap["adjudication"]["last"]["terrain_factor"], 1.4)
        self.assertEqual(snap["adjudication"]["last"]["terrain_source"], "战役参数库配置")
        # RAG 关闭也要有可见说明，而不是静默
        self.assertEqual(snap["rag"]["last"]["note"], "史料检索未开启")
        self.assertIsNone(snap["integrity"]["roster"])
        self.assertIsNone(snap["review"]["text"])
        self.assertEqual(snap["chat_messages"], [])

    # ---- 推进：名单守恒回填 + 位移越界只报告 ----
    def test_selected_battle_loads_coordinates_and_explicit_node(self):
        from gentactix import anchors
        point = anchors.KOREAN_ANCHORS["points"]["triangle"]
        res = self.client.post("/api/sessions", json={
            "scenario": "请初始化历史战局", "battle": "上甘岭", "use_rag": False,
            "time_step": "15分钟", "use_adjudicator": True})
        sid = res.json()["session_id"]
        data = _init_state()
        data["red_units"][0].update(name="高地守备连", node_name=point["name"],
                                     lat=38.1, lng=127.2)
        self.responses.append(json.dumps(data, ensure_ascii=False))
        result = self.client.post(f"/api/sessions/{sid}/init")
        self.assertEqual(result.status_code, 200, result.text)
        unit = result.json()["war_state"]["red_units"][0]
        self.assertEqual([unit["lat"], unit["lng"]], point["coordinates"])
        self.assertIn(str(point["coordinates"][0]), self.prompts[-1])
        self.assertIn(point["source_url"], self.prompts[-1])
        self.assertNotIn("抗美援朝-西线-温井", self.prompts[-1])

    def test_next_roster_recovery_and_violation(self):
        sid = self._create()
        self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        del updated["red_units"][1]                      # 无理由消失 → 必须回填
        updated["blue_units"][1]["lat"] += 0.1           # ~11 km ≫ 30分钟机动上限 → 必须越界
        self.responses.append(json.dumps({
            "updated_state": updated,
            "report": "秦军左翼向赵军壁垒压迫，赵军粮道仓皇后撤。",
            "source_note": "《资治通鉴》卷四",
            "eliminated": [],
        }, ensure_ascii=False))
        res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 200, res.text)
        snap = res.json()
        self.assertEqual(snap["turn_count"], 1)
        self.assertEqual(len(snap["history"]), 2)
        roster = snap["integrity"]["roster"]
        self.assertEqual([e["name"] for e in roster["recovered"]], ["秦军辎重队"])
        self.assertEqual(len(snap["war_state"]["red_units"]), 2)  # 回填后编制不缺
        self.assertEqual(len(snap["war_state"]["blue_units"]), 2)
        violations = snap["adjudication"]["violations"]
        self.assertEqual([v["name"] for v in violations], ["赵军粮道"])
        self.assertGreater(violations[0]["over_km"], 0)
        self.assertIsNotNone(snap["last_war_state"])
        # 越界只报告不修正：坐标保持模型给的位置，没被拉回
        self.assertAlmostEqual(snap["war_state"]["blue_units"][1]["lat"],
                               _init_state()["blue_units"][1]["lat"] + 0.1, places=6)

    # ---- 原子提交：管线中途抛异常，会话必须保持调用前原样 ----
    def test_midpipeline_failure_leaves_state_intact(self):
        # 故障注入（审计场景二）：check_movement 抛异常 → 502，
        # 但战局/回合/日志/名单报告不能被改写。旧实现先赋值后检查，此测必挂。
        sid = self._create()
        snap0 = self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        updated["blue_units"][1]["lat"] += 0.01
        self.responses.append(json.dumps({
            "updated_state": updated, "report": "赵军粮道缓慢后撤。", "eliminated": [],
        }, ensure_ascii=False))
        with patch.object(adjudicator, "check_movement", side_effect=RuntimeError("boom")):
            res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 502)
        snap1 = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(snap1["turn_count"], snap0["turn_count"])
        self.assertEqual(snap1["war_state"], snap0["war_state"])
        self.assertEqual(snap1["last_war_state"], snap0["last_war_state"])
        self.assertEqual(snap1["history"], snap0["history"])
        self.assertEqual(snap1["integrity"]["roster"], snap0["integrity"]["roster"])

    # ---- 同名单位只回 name+坐标：字段必须按上一回合补回（审计场景一）----
    def test_next_same_name_missing_fields_recovered(self):
        sid = self._create()
        self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        updated["red_units"][0] = {"name": "秦军左翼", "lat": 35.701, "lng": 112.801}
        self.responses.append(json.dumps({
            "updated_state": updated, "report": "两军前哨接触。", "eliminated": [],
        }, ensure_ascii=False))
        res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 200, res.text)
        snap = res.json()
        unit = next(u for u in snap["war_state"]["red_units"]
                    if u.get("name") == "秦军左翼")
        self.assertEqual(unit.get("hp"), 5000)      # 上一回合值补回
        self.assertEqual(unit.get("type"), "步兵")   # 兵种不被抹掉
        self.assertAlmostEqual(unit["lat"], 35.701, places=6)  # 模型写的坐标保留
        self.assertIn("秦军左翼",
                      [e["name"] for e in snap["integrity"]["roster"]["merged"]])

    # ---- 面板补算兜底：开关开着但没有裁决结果时必须补算，面板不空白 ----
    def test_panel_fallback_recomputes(self):
        sid = self._create()
        self._init(sid)
        backend_main._SESSIONS[sid]["adj_last"] = None   # 模拟内核曾失败 / 旧会话恢复
        snap = self.client.get(f"/api/sessions/{sid}").json()
        self.assertIsNotNone(snap["adjudication"]["last"])
        self.assertEqual(len(snap["adjudication"]["last"]["fingerprint"]), 12)

    # ---- 复盘 / 问答 ----
    def test_review_and_chat(self):
        sid = self._create()
        self._init(sid)
        self.responses.append("秦军优势在粮道与兵力厚度。")
        snap = self.client.post(f"/api/sessions/{sid}/review").json()
        self.assertIn("秦军优势在粮道与兵力厚度。", snap["review"]["text"])
        self.assertIn("状态核对摘要", snap["review"]["text"])
        self.responses.append("建议以辎重队为饵。")
        snap = self.client.post(f"/api/sessions/{sid}/chat", json={"message": "优势在哪方？"}).json()
        self.assertEqual([m["role"] for m in snap["chat_messages"]], ["user", "assistant"])
        self.assertEqual(snap["chat_messages"][1]["content"], "建议以辎重队为饵。")

    def test_bad_firing_sector_rolls_back_entire_turn_after_bounded_retries(self):
        sid = self._create()
        self._init(sid)
        before = copy.deepcopy(backend_main._SESSIONS[sid])
        candidate = _init_state(30)
        u = candidate['red_units'][0]
        u.update(attack_range=500, fire_sector={'bearing_deg': 0, 'arc_deg': 60})
        candidate['engagements'] = [{'attacker': u['name'], 'attacker_pos': [u['lat'], u['lng']],
                                    'lat': u['lat'] - .001, 'lng': u['lng']}]
        from gentactix.config import MAX_API_RETRIES
        self.responses.extend([json.dumps({'updated_state': candidate, 'report': '开火'})] * MAX_API_RETRIES)
        response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 502)
        self.assertIn('射击几何', response.text)
        for key in ('war_state', 'history', 'turn_count', 'trajectory'):
            self.assertEqual(backend_main._SESSIONS[sid][key], before[key])

    def test_report_and_review_full_strength_claims_do_not_override_counts(self):
        sid = self._create()
        self._init(sid)
        w = _init_state(30)
        w['red_units'][0]['personnel'] = {'current': 22, 'committed': 30, 'casualties': 8, 'noncombat_losses': 0}
        self.responses.append(json.dumps({'updated_state': w, 'report': '秦军左翼满编满供。'}))
        response = self.client.post(f'/api/sessions/{sid}/next')
        self.assertEqual(response.status_code, 200, response.text)
        text = response.json()['history'][0]
        self.assertNotIn('满编满供', text); self.assertIn('22/30', text)
        trace = backend_main._SESSIONS[sid]['trajectory'][-1]
        self.assertEqual(trace['guards']['report']['original_text'], '秦军左翼满编满供。')
        self.responses.append('秦军左翼满编。建议继续观察。')
        reviewed = self.client.post(f'/api/sessions/{sid}/review').json()['review']
        self.assertNotIn('秦军左翼满编', reviewed['text']); self.assertIn('建议继续观察', reviewed['text'])
        self.assertTrue(reviewed['audit']['artifact_id'])

    def test_chat_capped_at_40(self):
        sid = self._create()
        self._init(sid)
        self.responses.extend(f"答{i}" for i in range(25))
        for i in range(25):
            self.client.post(f"/api/sessions/{sid}/chat", json={"message": f"问{i}"})
        snap = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(len(snap["chat_messages"]), 40)
        self.assertEqual(snap["chat_messages"][-1]["content"], "答24")

    # ---- 导出：文件名与内容 ----
    def test_export_filename_and_payload(self):
        sid = self._create()
        self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        self.responses.append(json.dumps({"updated_state": updated, "report": "对峙持续。"}, ensure_ascii=False))
        self.client.post(f"/api/sessions/{sid}/next")
        res = self.client.get(f"/api/sessions/{sid}/export")
        self.assertEqual(res.status_code, 200)
        disp = res.headers["content-disposition"]
        self.assertIn('filename="GenTactix_T1.json"', disp)
        self.assertIn("filename*=UTF-8''GenTactix_", disp)
        body = res.json()
        self.assertEqual(body["turn_count"], 1)
        self.assertEqual(len(body["history_logs"]), 2)
        self.assertIsNotNone(body["war_state"])

    # ---- 会话隔离：一个会话初始化不能污染另一个 ----
    def test_session_isolation(self):
        sid_a, sid_b = self._create(), self._create()
        self._init(sid_a)
        snap_b = self.client.get(f"/api/sessions/{sid_b}").json()
        self.assertIsNone(snap_b["war_state"])
        self.assertEqual(snap_b["history"], [])
        self.assertIsNone(snap_b["adjudication"]["last"])

    # ---- 模型输出坏 JSON：502 可见报错，原战局原样保留 ----
    def test_bad_model_output_preserves_state(self):
        sid = self._create()
        snap0 = self._init(sid)
        self.responses.append("朕以为兵贵神速，岂有JSON之理。")
        res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 502)
        self.assertIn("推演失败", res.json()["detail"])
        snap1 = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(snap1["turn_count"], snap0["turn_count"])
        self.assertEqual(snap1["war_state"], snap0["war_state"])

    # ---- 重新开局：复盘与对话必须是一张白纸 ----
    def test_reinit_clears_review_and_chat(self):
        sid = self._create()
        self._init(sid)
        self.responses.append("复盘文本。")
        self.client.post(f"/api/sessions/{sid}/review")
        self.responses.append("回答文本。")
        self.client.post(f"/api/sessions/{sid}/chat", json={"message": "问题"})
        self._init(sid)  # 同一会话重新开局
        snap = self.client.get(f"/api/sessions/{sid}").json()
        self.assertIsNone(snap["review"]["text"])
        self.assertEqual(snap["chat_messages"], [])
        self.assertEqual(snap["turn_count"], 0)

    # ---- harness 单进程：模型操作必须串行（防 JSON-RPC 串包） ----
    def test_model_ops_serialized(self):
        self.assertTrue(hasattr(backend_main, "_OP_LOCK"))

    # ---- 角色会话记账：决策摘要随回合累积 ----
    def test_decision_log_records_turn_decisions(self):
        sid = self._create()
        self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        updated["blue_units"][1]["lat"] += 0.1  # 制造一次位移越界
        self.responses.append(json.dumps({
            "updated_state": updated, "report": "赵军粮道仓皇后撤，秦军跟进。",
            "eliminated": [],
        }, ensure_ascii=False))
        self.client.post(f"/api/sessions/{sid}/next")
        log = backend_main._SESSIONS[sid]["decision_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["turn"], 1)
        self.assertIn("赵军粮道", log[0]["summary"])
        self.assertEqual(log[0]["violations"], 1)
        self.assertTrue(log[0]["roster"])
        # 失败的回合不进决策日志（原子提交：日志与战局同生共死）
        self.responses.append("不是 JSON")
        self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(len(backend_main._SESSIONS[sid]["decision_log"]), 1)

    # ---- 重新开局：角色会话 epoch 递增 + 日志清零（旧局记忆绝不残留）----
    def test_reinit_bumps_role_epoch_and_clears_log(self):
        sid = self._create()
        self._init(sid)
        self.responses.append(json.dumps({"updated_state": copy.deepcopy(_init_state(30)),
                                          "report": "对峙。"}, ensure_ascii=False))
        self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(backend_main._SESSIONS[sid]["role_epoch"], 0)
        self.assertEqual(len(backend_main._SESSIONS[sid]["decision_log"]), 1)
        self._init(sid)  # 同一会话重新开局
        sess = backend_main._SESSIONS[sid]
        self.assertEqual(sess["role_epoch"], 1)
        self.assertEqual(sess["decision_log"], [])
        # 角色会话 id 随 epoch 换出全新 harness 会话（开关开时才非 None）
        with patch.object(harness_engine, "PERSIST_ROLES", True):
            rid = backend_main._brain_role_id(sess, "engine")
        self.assertIn("e1-engine", rid or "")

    # ---- 🤖 智能体回合：工具回喂 → submit_orders → 共用提交管线 ----
    def _fake_run_role_seq(self, replies):
        def fake(role_id, prompt, *, on_notification=None):
            self.prompts.append(prompt)
            text = replies.pop(0) if replies else "不是 JSON"
            return SimpleNamespace(final_response=text, finish_reason="completed",
                                   events=[], notifications=[], session_id=role_id)
        return fake

    def test_agent_mode_end_to_end(self):
        sid = self._create(agent_on=True)
        self._init(sid)
        updated = copy.deepcopy(_init_state(30))
        updated["red_units"][0]["lat"] += 0.001
        replies = [
            json.dumps({"tool_calls": [{"tool": "get_observation", "args": {}}]}),
            json.dumps({"action": "submit_orders", "orders": {
                "updated_state": updated, "report": "秦军左翼试探推进。",
                "eliminated": []}}, ensure_ascii=False),
        ]
        with patch.object(harness_engine, "get_role_session", return_value=None), \
             patch.object(harness_engine, "run_role", self._fake_run_role_seq(replies)):
            res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 200, res.text)
        snap = res.json()
        self.assertEqual(snap["turn_count"], 1)
        # 首条消息带协议说明；工具结果回喂进下一条消息（同会话）
        self.assertIn("领域工具协议", self.prompts[-2])
        self.assertIn("工具执行结果", self.prompts[-1])
        self.assertEqual(len(snap["war_state"]["red_units"]), 2)
        self.assertEqual(len(backend_main._SESSIONS[sid]["decision_log"]), 1)

    def test_agent_mode_exhaustion_502_state_intact(self):
        # 步数耗尽 → 502，战局/回合/日志保持调用前原样（原子提交共用）
        sid = self._create(agent_on=True)
        snap0 = self._init(sid)
        replies = ["不是 JSON"] * 9
        with patch.object(harness_engine, "get_role_session", return_value=None), \
             patch.object(harness_engine, "run_role", self._fake_run_role_seq(replies)):
            res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 502)
        self.assertIn("推演失败", res.json()["detail"])
        snap1 = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(snap1["turn_count"], snap0["turn_count"])
        self.assertEqual(snap1["war_state"], snap0["war_state"])
        self.assertEqual(backend_main._SESSIONS[sid]["decision_log"], [])

    # ---- 👁️ 观测透传：快照带 engine_meta（finish_reason / steps / 用时）----
    def test_snapshot_exposes_engine_meta(self):
        sid = self._create()
        snap = self._init(sid)
        meta = snap.get("engine_meta")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["finish_reason"], "completed")
        self.assertIn("label", meta)
        self.assertGreaterEqual(meta["elapsed_ms"], 0)

    # ---- 🛑 协作式取消：置旗 → 下一回合 409 且战局原样 → 旗标一次性消费 ----
    def test_cancel_stops_next_turn_state_intact(self):
        sid = self._create()
        snap0 = self._init(sid)
        res = self.client.post(f"/api/sessions/{sid}/cancel")
        self.assertEqual(res.status_code, 200)
        self.assertIn("取消", res.json()["note"])
        res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 409)
        self.assertIn("已取消", res.json()["detail"])
        snap1 = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(snap1["turn_count"], snap0["turn_count"])
        self.assertEqual(snap1["war_state"], snap0["war_state"])
        # 旗标已消费：下一回合照常推演
        self.responses.append(json.dumps({"updated_state": copy.deepcopy(_init_state(30)),
                                          "report": "对峙持续。"}, ensure_ascii=False))
        res = self.client.post(f"/api/sessions/{sid}/next")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["turn_count"], 1)

    # ---- 角色会话失忆重建 → 既往决策摘要注入下一提示 ----
    def test_amnesia_prefix_injects_decision_summary(self):
        sid = self._create()
        self._init(sid)
        self.responses.append(json.dumps({"updated_state": copy.deepcopy(_init_state(30)),
                                          "report": "秦军左翼压迫赵军壁垒。",
                                          "eliminated": []}, ensure_ascii=False))
        self.client.post(f"/api/sessions/{sid}/next")
        rid = "gentactix-test-engine"
        harness_engine._role_amnesia.add(rid)
        prefix = backend_main._amnesia_prefix(backend_main._SESSIONS[sid], rid)
        self.assertIn("既往决策摘要", prefix)
        self.assertIn("T+1", prefix)
        self.assertIn("秦军左翼", prefix)
        # 只消费一次：第二次不再重复注入
        self.assertEqual(backend_main._amnesia_prefix(backend_main._SESSIONS[sid], rid), "")
        harness_engine._role_amnesia.discard(rid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
