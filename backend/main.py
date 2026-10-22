"""🛡️ GenTactix · dsh harness 版推演后端（FastAPI）。

薄编排层：会话状态、按钮级 REST 接口（初始化 / 下一轮 / 复盘 / 问答 / 导出）。
业务逻辑全部复用 gentactix/ 包；模型调用全部经 backend/harness_engine.py
（DeepSeek Harness · sdk-minimal · 局域网 vLLM）。

启动（conda wargame 环境）：
    conda run -n wargame python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700
"""

from __future__ import annotations

import copy
import functools
import json
import threading
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field

from gentactix import (adjudication_lib, adjudicator, anchors, battlefield, causal, geo, integrity,
                       prompts, rag, state, sim_clock, fire_control, reporting, missions)

from . import agent_tools, harness_engine, extensions, runtime_bridge, session_store, llm_settings, runtime_identity

ROOT = Path(__file__).resolve().parent.parent
# 战局 → 角色会话 id 的记账索引（重启后 resume 用），只在角色会话开启时写
ROLE_INDEX_PATH = Path(os.environ.get('GENTACTIX_ROLE_INDEX', str(ROOT / '.cache' / 'gentactix-roles.json')))

@asynccontextmanager
async def lifespan(_app):
    restored, errors = session_store.load_all()
    _SESSIONS.update(restored)
    for filename in errors:
        harness_engine.ENGINE_NOTICES.append({'level': 'warning', 'message': f'战局检查点无法恢复：{filename}，原文件保留'})
    yield
    for session in list(_SESSIONS.values()):
        session_store.save(session)
    harness_engine.close_harness()


app = FastAPI(title="GenTactix dsh backend", version="3.1.3", lifespan=lifespan)

# Electron 渲染进程（dev: vite 端口 / prod: file://）都要能访问本机后端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],  # Packaged file:// renderer; never serve remote pages.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware('http')
async def local_browser_boundary(request: Request, call_next):
    """CORS alone cannot stop cross-site POSTs; reject them before dispatch."""
    origin = request.headers.get('origin')
    import re
    if origin and origin != 'null' and not re.fullmatch(
            r'https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?', origin):
        return JSONResponse({'detail': '本地推演服务不接受外部网站访问'}, status_code=403)
    # Reject DNS rebinding even when Origin is absent. TestClient uses testserver.
    if request.url.hostname not in ('localhost', '127.0.0.1', '::1', 'testserver'):
        return JSONResponse({'detail': '推演服务仅支持本机地址'}, status_code=403)
    return await call_next(request)

# ==========================================
# 🗃️ 会话存储（取代 st.session_state，键名口径与 state.DEFAULT_SESSION_STATE 一致）
# ==========================================
_SESSIONS: dict[str, dict] = {}
_SESSION_LOCK = threading.Lock()
# harness 只有一个子进程（JSON-RPC 走同一条 stdio 管道），并发模型调用会互相
# 交错、串包，所以所有走模型的操作一律串行。
_OP_LOCK = threading.Lock()


def _serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not _OP_LOCK.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='模型正在工作，请完成或取消当前操作后重试')
        try:
            result = fn(*args, **kwargs)
            for session in list(_SESSIONS.values()):
                try:
                    session_store.save(session)
                except (OSError, ValueError, TypeError):
                    harness_engine.ENGINE_NOTICES.append({'level': 'warning', 'message': '战局检查点写入失败，请立即导出战局；当前内存态势保留'})
            return result
        finally:
            _OP_LOCK.release()
    return wrapper


def _new_session(scenario: str, time_step: str, use_adjudicator: bool,
                 battle: str | None, use_rag: bool, use_live_intel: bool,
                 use_agent_tools: bool = False, persist_roles=None, agent_mode='json') -> dict:
    session = copy.deepcopy(state.DEFAULT_SESSION_STATE)
    session.update({
        "id": uuid.uuid4().hex[:12],
        "time_step": time_step,
        "use_adjudicator": use_adjudicator,
        "battle_choice": battle or None,
        "use_rag": use_rag,
        "use_live_intel": use_live_intel,
        # 智能体回合（领域工具协议）：默认关，开启后每回合 ≤MAX_AGENT_STEPS 次往返
        "use_agent_tools": use_agent_tools,
        "adj_spec": None,
        # 每回合的决策摘要（角色会话被迫重建时回灌记忆用），上限 30 条
        "decision_log": [],
        # 重新开局递增：角色会话 id 带 epoch，新一局绝不继承旧局的 harness 记忆
        "role_epoch": 0,
        # 观测与控制：最近一次模型调用的元数据、协作式取消旗标、在途进度镜像
        "engine_last_meta": None,
        "cancel_requested": False,
        "progress": None,
        "trajectory": [], "causal_analysis": None, "counterfactuals": [],
        "active_observer": None, "persist_roles": persist_roles, "agent_mode": agent_mode,
    })
    return session


def _get(session_id: str) -> dict:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return session


# ==========================================
# 🧬 角色会话接线（GENTACTIX_PERSIST_ROLES=1 时启用；默认关 = 旧单发路径原样）
# ==========================================
def _brain_role_id(session: dict, role: str, force: bool = False) -> str | None:
    """开关关 → None（每次新会话，与既有测试口径一致）；开关开 → 稳定角色会话 id。

    智能体模式 force=True 无条件用角色会话（工具回喂必须落同一会话）。
    id 里带 role_epoch：重新开局换 epoch ⇒ 换全新 harness 会话，
    旧局记忆不会张冠李戴（DSH 没有清除会话 API，换 id 是唯一干净的做法）。
    """
    persist = session.get('persist_roles')
    if not ((harness_engine.PERSIST_ROLES if persist is None else persist) or force):
        return None
    scoped = f"{session['id']}e{session.get('role_epoch', 0)}"
    return harness_engine.role_session_id(scoped, role)


def _amnesia_prefix(session: dict, role_id: str) -> str:
    """角色会话刚被重建（resume 失败）→ 注入既往决策摘要，失忆可恢复而非静默。

    必须在 get_role_session 之后调用：失忆标记是那次调用置的，且只消费一次。
    """
    if not harness_engine.consume_role_amnesia(role_id):
        return ""
    entries = session.get("decision_log") or []
    if not entries:
        return ""
    lines = []
    for e in entries:
        line = f"- T+{e['turn']}：{e['summary']}"
        if e.get("violations"):
            line += f"（位移越界 {e['violations']} 起）"
        lines.append(line)
    return ("【系统提示：你的会话记忆已重置。以下是本局既往决策摘要，"
            "请据此保持前后连贯。】\n" + "\n".join(lines) + "\n\n")


def _consume_cancel(session: dict) -> bool:
    """取消旗标一次性消费：置位后的下一次模型操作被拦下并复位。"""
    if session.get("cancel_requested"):
        session["cancel_requested"] = False
        return True
    return False


def _record_failed_operation(session, observer, phase, error):
    try:
        extensions.record_failure(session, observer, phase, error)
    except Exception as exc:
        harness_engine.ENGINE_NOTICES.append({'level': 'warning',
            'message': f'失败操作的审计记录未能写入：{type(exc).__name__}；原战局未改动'})


def _record_role_index(session: dict) -> None:
    """把战局 → 角色会话 id 记到 .cache/gentactix-roles.json（best-effort 记账）。"""
    try:
        index = {}
        if ROLE_INDEX_PATH.is_file():
            index = json.loads(ROLE_INDEX_PATH.read_text(encoding="utf-8"))
        index[session["id"]] = {
            "scenario": session.get("scenario_input", ""),
            "turn_count": session["turn_count"],
            "role_epoch": session.get("role_epoch", 0),
            "roles": [harness_engine.role_session_id(
                          f"{session['id']}e{session.get('role_epoch', 0)}", r)
                      for r in ("engine", "strategist", "chat")],
        }
        ROLE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        ROLE_INDEX_PATH.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001  索引只是记账，写失败绝不影响推演
        pass


# ==========================================
# ⚖️ / 📚 与 app.py 同语义的安全包装（失败必须可见，绝不静默降级）
# ==========================================
def _safe_adjudicate(session: dict, state_data, time_step, turn, params, phase):
    try:
        return adjudicator.adjudicate(state_data, time_step, turn=turn, params=params)
    except Exception as exc:  # noqa: BLE001
        harness_engine.ENGINE_NOTICES.append({
            "level": "warning",
            "message": (f"⚠️ 裁决内核在{phase}阶段失败（`{type(exc).__name__}: {exc}`），"
                        "本回合退回自由推演 —— 结论不可复现，请核对战局数据。"),
        })
        return None


def _retrieve_briefing(session: dict, query: str, phase: str):
    meta = {"phase": phase, "query": query, "enabled": bool(session["use_rag"]),
            "hits": [], "injected": 0, "note": ""}
    if not session["use_rag"]:
        meta["note"] = "史料检索未开启"
        return "", meta

    stats = rag.corpus_stats()
    if stats["chunks"] == 0:
        meta["note"] = f"史料库为空（{stats['dir']}），本次为模型自由发挥"
        return "", meta

    top_k, max_chars = (8, 3200) if phase == "init" else (4, 2200)
    hits = rag.search(query, top_k=top_k, battle=session["battle_choice"], phase=phase,
                      simulation_mode=session.get('simulation_mode', 'open'))
    if not hits:
        meta["note"] = "未命中相关史料，本次为模型自由发挥"
        return "", meta

    text, used = rag.build_briefing(hits, max_chars=max_chars)
    meta["hits"] = [
        {
            "doc_title": hit["doc_title"],
            "section": hit["section"],
            "source": hit["source"],
            "score": hit["score"],
            "preview": hit["text"][:180] + ("…" if len(hit["text"]) > 180 else ""),
        }
        for hit in hits
    ]
    meta["injected"] = used
    if used < len(hits):
        meta["note"] = f"命中 {len(hits)} 段，受长度上限约束仅注入 {used} 段"
    return text, meta


# ==========================================
# 📦 快照：前端一次拿全渲染所需的一切
# ==========================================
def _effective_spec(session, world=None):
    world = world or session.get('war_state') or {}
    if world.get('mission'):
        definition = world['mission']['definition']
        explicit = dict(definition['geometry'])
        return adjudication_lib.BattleSpec(name=definition['title'], params={**adjudication_lib.default_params(), **explicit},
            explicit=explicit, source=missions.DATA, source_kind='local_teaching', matched_by='explicit',
            time_step_hint=definition['time_step'], validation_status='teaching_estimate',
            warnings=['局部合成地图的教学参数；不是历史地形测绘或战损率标定'])
    return adjudicator.load_battle_spec(scenario_text=session.get('scenario_input', ''), battle=session['battle_choice'])


def _snapshot(session: dict) -> dict:
    if session['use_adjudicator'] and session['war_state'] and session.get('adj_spec') is None:
        session['adj_spec'] = _effective_spec(session)
    # 🐛 面板补算兜底（与 app.py 同语义）：开关开着但没有裁决结果时
    # （初始化时内核失败、或旧会话恢复），按当前战局补算一次，面板绝不空白。
    if (session["use_adjudicator"] and session["war_state"]
            and (not session["adj_last"] or session['adj_last'].get('version') != adjudicator.ADJUDICATOR_VERSION)):
        if session.get("adj_spec") is None:
            session["adj_spec"] = _effective_spec(session)
        session["adj_last"] = _safe_adjudicate(
            session, session["war_state"], session["time_step"],
            session["turn_count"], dict(session["adj_spec"].explicit), "面板补算")
    spec = session.get("adj_spec")
    return {
        "session_id": session["id"],
        "scenario": session["scenario_input"],
        "turn_count": session["turn_count"],
        "time_step": session["time_step"],
        "settings": {
            "simulation_mode": session.get('simulation_mode', 'open'),
            "mission_id": session.get('mission_id'),
            "use_agent_tools": bool(session.get("use_agent_tools")),
            "persist_roles": session.get("persist_roles") if session.get("persist_roles") is not None else harness_engine.PERSIST_ROLES,
            "agent_mode": session.get("agent_mode", "json"),
            "use_live_intel": bool(session.get("use_live_intel")),
        },
        "war_state": session["war_state"],
        "last_war_state": session["last_war_state"],
        "battlefield": battlefield.build_battlefield_view(session["war_state"], session["adj_last"],
            previous=session.get('last_war_state'), trajectory=session.get('trajectory')),
        "history": session["history"],
        "review": {
            "text": session["review_text"],
            "reasoning": session["review_reasoning"],
            "audit": session.get('review_audit'),
        },
        "chat_messages": session["chat_messages"],
        "adjudication": {
            "enabled": session["use_adjudicator"],
            "battle_choice": session["battle_choice"],
            "spec": {
                "name": spec.name,
                "summary": spec.summary_line(),
                "source": str(spec.source) if spec.source else None,
                "source_kind": spec.source_kind,
                "time_step_hint": spec.time_step_hint,
                "calibrated": spec.calibrated,
                "validation_status": spec.validation_status,
                "errors": list(spec.errors),
                "warnings": list(spec.warnings),
                "table": adjudication_lib.param_table(spec),
            } if spec else None,
            "last": session["adj_last"],
            "violations": session["adj_violations"],
        },
        "rag": {
            "enabled": session["use_rag"],
            "last": session["rag_last"],
            "corpus": rag.corpus_stats(),
        },
        "integrity": {
            "roster": session["roster_last"],
            "numeric_flags": session["numeric_flags"],
        },
        # 观测透传：finish_reason / turns / steps / tokens / elapsed_ms
        "engine_meta": session.get("engine_last_meta"),
        "causal": {"record_count": len(session.get('trajectory', [])),
                   "latest_trace_id": (session.get('trajectory') or [{}])[-1].get('id'),
                   "runtime": {k: v for k, v in harness_engine.RUNTIME_STATUS.items() if k != 'diagnostic'},
                   "plugins": extensions.registry.describe()},
        "notices": harness_engine.drain_notices(),
    }


# ==========================================
# 🔌 接口
# ==========================================
class CreateSessionRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=16000)
    time_step: str = "30分钟"
    use_adjudicator: bool = False
    battle: str | None = None
    use_rag: bool = False
    use_live_intel: bool = False
    use_agent_tools: bool = False
    persist_roles: bool | None = None
    agent_mode: str = 'json'
    simulation_mode: str = 'open'
    mission_id: str | None = None


class NextTurnRequest(BaseModel):
    instruction: str = Field(default='', max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "engine": "deepseek-harness",
        "profile": "sdk-minimal",
        "model": harness_engine.LLM_MODEL,
        "base_url": harness_engine.LLM_BASE_URL,
        "dsh_home": harness_engine.DSH_HOME,
        "harness_started": harness_engine._harness is not None,
        "adjudicator_version": adjudicator.ADJUDICATOR_VERSION,
        "runtime": {k: v for k, v in harness_engine.RUNTIME_STATUS.items() if k != 'diagnostic'},
        "build": runtime_identity.status(),
        "session_count": len(_SESSIONS),
    }


class LlmConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    max_tokens: int = 16384
    context_window: int = 262144
    timeout_seconds: int = 900
    clear_api_key: bool = False
    role_policies: dict | None = None
    max_steps: int = 8
    max_tool_calls: int = 16


def _local_settings_origin(request):
    origin = request.headers.get('origin')
    from urllib.parse import urlsplit
    if origin and origin != 'null' and urlsplit(origin).hostname not in ('localhost', '127.0.0.1', '[::1]', '::1'):
        raise HTTPException(status_code=403, detail='模型配置仅允许本机界面修改')


@app.get('/api/llm/config')
def get_llm_config():
    return llm_settings.current(harness_engine)


@app.put('/api/llm/config')
def put_llm_config(req: LlmConfigRequest, request: Request):
    _local_settings_origin(request)
    if not _OP_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail='模型正在工作，请完成或取消当前操作后再修改配置')
    try:
        value = req.model_dump() if hasattr(req, 'model_dump') else req.dict()
        return llm_settings.apply(harness_engine, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        _OP_LOCK.release()


@app.post('/api/llm/test')
@_serialized
def test_llm_config(request: Request):
    _local_settings_origin(request)
    try:
        result = harness_engine._run_brain('连接测试：不要调用工具，只回复JSON {"status":"ok"}。', None, role='connection-test')
        answer = harness_engine.parse_json_object(result.final_response)
        if answer.get('status') != 'ok':
            raise ValueError('模型未返回预期测试结果')
        return {'ok': True, 'model': harness_engine.LLM_MODEL, 'transport': 'DSH', 'finish_reason': result.finish_reason}
    except harness_engine.HarnessRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=502, detail='DSH连接测试失败，请检查接口、模型ID、密钥和服务状态；配置已保留')


@app.get('/api/sessions')
def list_sessions():
    return {'sessions': [{'session_id': s['id'], 'turn_count': s['turn_count'], 'scenario': s.get('scenario_input', ''),
                          'initialized': isinstance(s.get('war_state'), dict)}
                         for s in list(_SESSIONS.values())]}


@app.post('/api/sessions/checkpoint')
@_serialized
def checkpoint_sessions():
    for session in list(_SESSIONS.values()):
        session_store.save(session)
    return {'ok': True, 'sessions': len(_SESSIONS), 'build': runtime_identity.status()}


@app.post('/api/sessions/import')
@_serialized
def import_session(payload: dict):
    """Restore a full export or a legacy export accompanied by its API snapshot."""
    document = payload.get('export', payload)
    if not isinstance(document, dict):
        raise HTTPException(status_code=422, detail='导出文件必须是JSON对象')
    if document.get('format') == 'gentactix-session-v1':
        data = document.get('session')
    else:
        snapshot = payload.get('snapshot') or {}
        if not isinstance(snapshot, dict) or not snapshot.get('session_id') or not snapshot.get('scenario') or not snapshot.get('time_step'):
            raise HTTPException(status_code=422, detail='旧版导出还需要原会话快照，不能猜测场景与规则配置')
        if any(not isinstance(snapshot.get(key, {}), dict) for key in ('adjudication', 'rag')) or not isinstance(document.get('review', {}), dict):
            raise HTTPException(status_code=422, detail='旧版快照的裁决、检索与复盘字段必须是对象')
        data = _new_session(snapshot['scenario'], snapshot['time_step'],
            snapshot.get('adjudication', {}).get('enabled', False),
            snapshot.get('adjudication', {}).get('battle_choice'),
            snapshot.get('rag', {}).get('enabled', False), False)
        data.update(id=snapshot['session_id'], scenario_input=snapshot['scenario'],
            war_state=document.get('war_state'), last_war_state=snapshot.get('last_war_state'),
            turn_count=document.get('turn_count', 0), history=document.get('history_logs', []),
            chat_messages=document.get('chat_messages', []), trajectory=document.get('trajectory', []),
            causal_analysis=document.get('causal_analysis'), counterfactuals=document.get('counterfactuals', []),
            review_text=document.get('review', {}).get('conclusion'),
            review_reasoning=document.get('review', {}).get('reasoning'))
    import re
    if not isinstance(data, dict) or not re.fullmatch(r'[a-f0-9]{12}', str(data.get('id', ''))):
        raise HTTPException(status_code=422, detail='会话ID不合法')
    if data['id'] in _SESSIONS:
        raise HTTPException(status_code=409, detail='会话已存在，导入不会覆盖现有战局')
    if not isinstance(data.get('war_state'), dict) or not isinstance(data.get('history'), list):
        raise HTTPException(status_code=422, detail='缺少完整战局或战报')
    try:
        sim_clock.duration_seconds(data.get('time_step'))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    restored = _new_session(data.get('scenario_input', ''), data['time_step'],
        data.get('use_adjudicator', False), data.get('battle_choice'), data.get('use_rag', False),
        data.get('use_live_intel', False))
    restored.update(copy.deepcopy(data))
    restored.update(adj_spec=None, active_observer=None, progress=None, cancel_requested=False)
    try:
        session_store.validate(restored)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session_store.save(restored)
    _SESSIONS[restored['id']] = restored
    return {'session_id': restored['id'], 'turn_count': restored['turn_count']}


@app.get("/api/battles")
def battles():
    # 结构化列表：前端下拉框需要每场战役的建议回合跨度（time_step 提示）
    return {"battles": adjudication_lib.list_battle_details()}


@app.get('/api/missions')
def mission_catalog():
    return {'missions': [{k: v for k, v in m.items() if k != 'initial_state'} for m in missions.catalog()]}


@app.get("/api/corpus")
def corpus():
    return rag.corpus_stats()


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest):
    if not req.scenario.strip():
        raise HTTPException(status_code=422, detail="场景描述不能为空")
    try:
        sim_clock.duration_seconds(req.time_step)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.agent_mode not in ('json', 'native'):
        raise HTTPException(status_code=422, detail='agent_mode必须为json或native')
    if req.simulation_mode not in ('open', 'replay'):
        raise HTTPException(status_code=422, detail='模式必须为open或replay')
    if req.mission_id:
        try:
            definition = missions.definition(req.mission_id)
            if req.use_agent_tools or req.use_live_intel:
                raise ValueError('固定局部想定使用DSH命令生成；请关闭领域工具循环和实时情报选项')
            if req.simulation_mode != 'open':
                raise ValueError('固定局部初态用于开放推演；历史回放请使用资料模式')
            if req.battle and adjudication_lib.resolve(battle=req.battle).name != definition['battle']:
                raise ValueError('局部想定与所选战役不一致')
            req.battle = definition['battle']
            req.time_step = definition['time_step']
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.agent_mode == 'native' and req.use_agent_tools and not harness_engine.NATIVE_RUNTIME:
        raise HTTPException(status_code=409, detail='原生运行时插件未启用，请选择JSON协议模式')
    session = _new_session(req.scenario.strip(), req.time_step,
                           req.use_adjudicator, req.battle,
                           req.use_rag, req.use_live_intel,
                           req.use_agent_tools, req.persist_roles, req.agent_mode)
    session["scenario_input"] = req.scenario.strip()
    session.update(simulation_mode=req.simulation_mode, mission_id=req.mission_id)
    with _SESSION_LOCK:
        _SESSIONS[session["id"]] = session
    session_store.save(session)
    return {"session_id": session["id"]}


@app.delete("/api/sessions/{session_id}")
@_serialized
def delete_session(session_id: str):
    with _SESSION_LOCK:
        _get(session_id)
        # Keep the in-memory battle if removing its checkpoint fails.
        try:
            session_store.remove(session_id)
        except OSError as exc:
            raise HTTPException(status_code=500, detail='无法删除本机存档，战局已保留；请检查存储目录权限后重试') from exc
        _SESSIONS.pop(session_id)
        harness_engine.forget_role_sessions(session_id)
    return {"ok": True, "session_id": session_id, "audit_retained": True}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return _snapshot(_get(session_id))


@app.get('/api/sessions/{session_id}/progress')
def get_progress(session_id: str):
    session = _get(session_id)
    observer = session.get('active_observer')
    return {'active': observer is not None, 'progress': dict(session.get('progress') or {}),
            'operation_id': observer.operation_id if observer else None}


@app.post("/api/sessions/{session_id}/init")
@_serialized
def init_session(session_id: str):
    session = _get(session_id)
    scenario_input = session["scenario_input"]

    # 1. 高精坐标锚点匹配
    available_nodes = anchors.collect_available_nodes(scenario_input, battle=session["battle_choice"])

    # 2. 史料检索（初始化阶段整卡取回）
    briefing, rag_meta = _retrieve_briefing(session, scenario_input, "init")

    # 重新开局：换 epoch + 丢弃进程内旧角色会话，新一局绝不继承旧局的 harness 记忆
    init_epoch = session.get('role_epoch', 0) + int(session['war_state'] is not None)

    # 角色会话开启时，初始化就是引擎角色会话的第一轮（跨回合记忆从这里开始）
    engine_role = _brain_role_id({**session, 'role_epoch': init_epoch}, "engine")

    if _consume_cancel(session):
        raise HTTPException(status_code=409, detail="已取消：本次初始化未执行，会话未改动")
    observer = harness_engine.RunObserver("初始化")
    session["progress"] = observer.progress   # 在途进度镜像（数据通路）
    session['active_observer'] = observer

    try:
        init_prompt = prompts.build_init_prompt(scenario_input, available_nodes, briefing,
                                                simulation_mode=session.get('simulation_mode', 'open'))
        if session.get('mission_id'):
            init_state = missions.initial(session['mission_id'])
            init_prompt = '固定教学初态，未调用模型。定义摘要：' + init_state['mission']['definition_hash']
        else:
            init_state = harness_engine.get_ai_response_with_retry(
                init_prompt, role_id=engine_role, observer=observer,
                cancel_check=lambda: bool(session.get("cancel_requested")))

        # 3. 锚点吸附 + 海军强制落水
        if not session.get('mission_id'):
            geo.sanitize_init_units(init_state, available_nodes)
        sim_clock.initialize(init_state)
        fire_control.enforce(init_state)
        if session.get('cancel_requested'):
            raise RuntimeError('用户已取消，初始化不提交')

        # T+0 也裁一次：开局就能看到哪条粮道是脆的（只用于面板显示）
        if session["use_adjudicator"]:
            adj_spec = _effective_spec(session, init_state)
            adj_last = _safe_adjudicate(
                session, init_state, session["time_step"], 0,
                (init_state['mission']['definition']['geometry'] if session.get('mission_id') else dict(adj_spec.explicit)), "初始化")
        else:
            adj_spec = None
            adj_last = None

        start_time = init_state.get("current_time", "未知时间")
        initial_report = init_state.get(
            "situation_report", "各单位已进入战斗位置，战役即将打响。")
        initial_report, report_audit = reporting.render(initial_report, init_state, turn=0, adjudication=adj_last)
        init_state['situation_report'] = initial_report
        initial_note = init_state.get("source_note")
        history = [state.compose_report_entry(
            initial_report, initial_note)]

        trace_record = extensions.prepare_record(session, before=None, after=init_state,
            turn=0, epoch=init_epoch, params=dict(adj_spec.explicit) if adj_spec else {},
            prompt=init_prompt, observer=observer, report=initial_report,
            guards={'report': report_audit})

        # ✅ 原子提交：任何一步失败都不动会话，重试初始化不会留下半截战局
        session["rag_last"] = rag_meta
        session["war_state"] = init_state
        session["last_war_state"] = None
        session["turn_count"] = 0
        session["roster_last"] = None
        session["numeric_flags"] = []
        session["adj_violations"] = []
        session["adj_spec"] = adj_spec
        session["adj_last"] = adj_last
        # 复盘与对话必须是一张白纸（防上一局张冠李戴）
        session["review_text"] = None
        session["review_reasoning"] = None
        session['review_audit'] = None
        session["chat_messages"] = []
        session["history"] = history
        session["decision_log"] = []
        session["engine_last_meta"] = observer.meta()
        session["progress"] = None
        session['active_observer'] = None
        session['role_epoch'] = init_epoch
        session['trajectory'] = [trace_record]
        session['causal_analysis'] = None
        session['counterfactuals'] = []
        if engine_role:
            _record_role_index(session)
    except Exception as exc:  # noqa: BLE001
        session["progress"] = None
        session['active_observer'] = None
        _record_failed_operation(session, observer, 'init', exc)
        if session.get("cancel_requested"):
            session["cancel_requested"] = False
            raise HTTPException(status_code=409,
                                detail="已取消：坐标反思循环在重试边界停止，会话未改动") from exc
        raise HTTPException(status_code=502, detail=f"初始化失败：{exc}") from exc

    return _snapshot(session)


@app.post("/api/sessions/{session_id}/next")
@_serialized
def next_turn(session_id: str, req: NextTurnRequest | None = None):
    session = _get(session_id)
    if not session["war_state"]:
        raise HTTPException(status_code=409, detail="请先初始化沙盘")
    if session.get('mission_id') and session['war_state']['mission']['status'] == 'completed':
        raise HTTPException(status_code=409, detail='目标已结算；请导出复盘或重新开局')
    instruction = req.instruction if req else ''

    time_step = session["time_step"]
    current_time_str = session["war_state"].get('current_time', '00:00')
    next_turn_num = session["turn_count"] + 1

    # 史料检索：查询拼上「哪场战役 + 当前时间 + 当前地形」
    query = " ".join(str(x) for x in (
        session.get("scenario_input", ""),
        current_time_str,
        session["war_state"].get("current_terrain", ""),
    ) if x)
    briefing, rag_meta = _retrieve_briefing(session, query, "step")

    # 推演前先裁决：war_state 仍是上一回合末的真实态势
    prev_state = session["war_state"]
    adj_params = {}
    pre_adj = None
    if session["use_adjudicator"]:
        session["adj_spec"] = _effective_spec(session)
        adj_params = (prev_state['mission']['definition']['geometry'] if session.get('mission_id') else dict(session["adj_spec"].explicit))
        pre_adj = _safe_adjudicate(
            session, prev_state, time_step, next_turn_num, adj_params, "推演前")

    # 角色会话先建立/恢复（失忆标记由这次调用置位），再决定要不要注入决策摘要。
    # 智能体模式无条件用角色会话：工具结果必须回喂同一会话。
    engine_role = _brain_role_id(session, "engine",
                                 force=bool(session.get("use_agent_tools")))
    if engine_role:
        harness_engine.get_role_session(engine_role)

    # 协作式取消：旗标在**入口与步界**消费；单次在途模型调用不可中断。
    if _consume_cancel(session):
        raise HTTPException(status_code=409, detail="已取消：本回合未执行，战局未改动")
    observer = harness_engine.RunObserver(f"推演 T+{next_turn_num}")
    session["progress"] = observer.progress   # 在途进度镜像（数据通路）
    session['active_observer'] = observer
    cancel_check = lambda: bool(session.get("cancel_requested"))

    try:
        step_prompt = (_amnesia_prefix(session, engine_role) if engine_role else "") + prompts.build_step_prompt(
            session["war_state"], next_turn_num, time_step,
            session["use_live_intel"], briefing, adjudication=pre_adj,
            simulation_mode=session.get('simulation_mode', 'open'))
        if instruction:
            step_prompt += '\n本轮指令（不得改写规则）：' + instruction
        native = session.get('use_agent_tools') and session.get('agent_mode') == 'native'
        if session.get('mission_id'):
            step_prompt = missions.prompt(prev_state, instruction, briefing)
            for attempt in range(2):
                proposal = harness_engine.get_ai_response(step_prompt, role_id=engine_role,
                    observer=observer, cancel_check=cancel_check)
                try:
                    candidate = missions.advance(prev_state, proposal.get('orders'), time_step)
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    if attempt:
                        raise ValueError('命令校验重试后仍失败：' + str(exc)) from exc
                    step_prompt += '\n候选命令未通过，状态未改变。请纠正后完整重发orders：' + str(exc)
            result = {'updated_state': candidate, 'report': missions.report(candidate),
                      'source_note': '教学规则' + missions.VERSION + '；命令由模型提出，状态由程序结算。',
                      'decisions': [{'team': next(t for t, u in missions.units(prev_state) if u['name'] == o['unit']), 'unit_name': o['unit'], 'action': str(o), 'rationale': str(proposal.get('reason', ''))} for o in proposal['orders']]}
        elif native:
            step_prompt += ('\n【原生领域工具回合】可调用已注册的只读领域工具get_observation、retrieve_history、'
                            'evaluate_action、inspect_trajectory、evaluate_counterfactual。先核对状态和可行性；'
                            '最终直接返回updated_state/report/decisions的JSON，不使用应用层tool_calls协议。')
            result = harness_engine.get_ai_response_with_retry(step_prompt, role_id=engine_role,
                observer=observer, cancel_check=cancel_check, previous_state=prev_state, native_context={
                    'session': {k: copy.deepcopy(session.get(k)) for k in ('war_state', 'use_rag', 'scenario_input',
                        'battle_choice', 'history', 'trajectory', 'simulation_mode')}, 'pre_adj': pre_adj})
        elif session.get("use_agent_tools"):
            # 唯一分支点：智能体只负责**产出候选**，下游守卫链与原子提交完全共用
            result = agent_tools.run_decision_turn(
                session, step_prompt, engine_role, pre_adj=pre_adj,
                cancel_check=cancel_check, observer=observer)
        else:
            result = harness_engine.get_ai_response_with_retry(
                step_prompt, role_id=engine_role, observer=observer,
                cancel_check=cancel_check, previous_state=prev_state)

        updated_data = result.get("updated_state", result)
        sim_clock.enforce_step(prev_state, updated_data, time_step)

        # 强制洗数据（落水算法）
        if not session.get('mission_id'):
            geo.sanitize_step_units(updated_data)

        # 名单守恒：赋值之前核对，无理由消失的按上一回合快照回填
        if isinstance(updated_data, dict) and isinstance(result, dict):
            declared = result.get("eliminated")
            if isinstance(declared, list) and declared and not updated_data.get("eliminated"):
                updated_data["eliminated"] = declared
        updated_data, roster = integrity.reconcile_roster(prev_state, updated_data)
        battlefield.carry_battlefield_state(prev_state, updated_data)
        fire_control.enforce(updated_data, prev_state)
        numeric_flags = integrity.check_numeric_integrity(prev_state, updated_data)

        report_text = result.get("report")
        if not report_text and isinstance(updated_data, dict):
            report_text = updated_data.get("report", f"【T+{next_turn_num}】系统未能成功生成文字战报，但战场态势已发生改变。")
        # 事后裁决 + 位移越界检查（只报告，不拉回坐标）
        if session["use_adjudicator"]:
            adj_last = _safe_adjudicate(
                session, updated_data, time_step, next_turn_num, adj_params, "推演后")
            violations = adjudicator.check_movement(
                prev_state, updated_data, pre_adj)
        else:
            adj_last = None
            violations = []

        report_text, report_audit = reporting.render(report_text, updated_data, prev_state,
            turn=next_turn_num, adjudication=adj_last)
        # Prevent stale model prose inside the state from poisoning later prompts.
        updated_data.pop('situation_report', None)
        updated_data.pop('report', None)
        note = result.get('source_note') or updated_data.get('source_note')
        history_entry = state.compose_report_entry(report_text, note)

        if cancel_check():
            raise RuntimeError('用户已取消，候选战局不提交')
        trace_record = extensions.prepare_record(session, before=prev_state, after=updated_data,
            turn=next_turn_num, params=adj_params, prompt=step_prompt, result=result,
            observer=observer, report=report_text or '',
            guards={'roster': roster, 'numeric_flags': numeric_flags, 'movement_violations': violations,
                    'fire_geometry': {'status': 'verified'}, 'report': report_audit})

        # ✅ 全部校验通过 → 原子提交：上面任何一步抛异常都到不了这里，
        #    战局、回合数、日志、裁决一律保持调用前原样（502 不留半截状态）。
        session["rag_last"] = rag_meta
        session["last_war_state"] = copy.deepcopy(prev_state)
        session["war_state"] = updated_data
        session["roster_last"] = roster
        session["numeric_flags"] = numeric_flags
        session["adj_last"] = adj_last
        session["adj_violations"] = violations
        if history_entry:
            session["history"].insert(0, history_entry)
        session["turn_count"] = next_turn_num
        # 决策摘要进 decision_log：角色会话被迫重建时靠它恢复跨回合连贯
        session["decision_log"].append({
            "turn": next_turn_num,
            "summary": (report_text or "").strip()[:200],
            "violations": len(violations),
            "roster": integrity.summarize(roster, numeric_flags),
        })
        if len(session["decision_log"]) > 30:
            session["decision_log"] = session["decision_log"][-30:]
        session["engine_last_meta"] = observer.meta()
        session["progress"] = None
        session['active_observer'] = None
        session.setdefault('trajectory', []).append(trace_record)
        session['causal_analysis'] = None
        if engine_role:
            _record_role_index(session)
    except Exception as exc:  # noqa: BLE001
        session["progress"] = None
        session['active_observer'] = None
        _record_failed_operation(session, observer, 'next', exc)
        if session.get("cancel_requested"):
            session["cancel_requested"] = False
            raise HTTPException(status_code=409,
                                detail="已取消：回合在步界停止，战局未改动") from exc
        raise HTTPException(status_code=502, detail=f"本回合推演失败：{exc}") from exc

    return _snapshot(session)


@app.post("/api/sessions/{session_id}/review")
@_serialized
def review(session_id: str):
    session = _get(session_id)
    if not session["war_state"] or not session["history"]:
        raise HTTPException(status_code=409, detail="请先进行至少一轮推演，再呼叫总参谋长复盘")
    strategist_role = _brain_role_id(session, "strategist")
    if strategist_role:
        harness_engine.get_role_session(strategist_role)
    observer = harness_engine.RunObserver("战略复盘")
    session["progress"] = observer.progress
    session['active_observer'] = observer
    try:
        # Refresh legacy geometry before the reviewer sees any status claims.
        _snapshot(session)
        reasoning, conclusion = harness_engine.get_strategic_review(
            session["history"], session["war_state"],
            role_id=strategist_role,
            prefix=_amnesia_prefix(session, strategist_role) if strategist_role else "",
            observer=observer, adjudication=session.get('adj_last'))
        if session.get('cancel_requested'):
            raise RuntimeError('用户已取消，复盘不提交')
        conclusion, review_audit = reporting.render(conclusion, session['war_state'],
            session.get('last_war_state'), adjudication=session.get('adj_last'), review=True)
        review_artifact = extensions.persist_artifact(session, 'review-audit', review_audit)
        session['review_audit'] = {'artifact_id': review_artifact, **extensions.safe(review_audit)}
        session["review_reasoning"] = reasoning
        session["review_text"] = conclusion
        session["engine_last_meta"] = observer.meta()
        session["progress"] = None
        session['active_observer'] = None
    except Exception as exc:  # noqa: BLE001
        session["progress"] = None
        session['active_observer'] = None
        _record_failed_operation(session, observer, 'review', exc)
        cancelled = bool(session.pop('cancel_requested', False))
        raise HTTPException(status_code=409 if cancelled else 502, detail=f"总参谋长复盘失败：{exc}") from exc
    return _snapshot(session)


@app.post("/api/sessions/{session_id}/chat")
@_serialized
def chat(session_id: str, req: ChatRequest):
    session = _get(session_id)
    if not session["war_state"]:
        raise HTTPException(status_code=409, detail="请先初始化沙盘")
    question = req.message.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    # 问答角色会话：记忆完好时 ask_xuanji 只发问题，冷会话/失忆自带全部上下文
    chat_role = _brain_role_id(session, "chat")
    observer = harness_engine.RunObserver("璇玑问答")
    session["progress"] = observer.progress
    session['active_observer'] = observer
    try:
        reasoning, answer = harness_engine.ask_xuanji(
            session["history"], session["war_state"],
            session["chat_messages"], question, role_id=chat_role, observer=observer)
        if session.get('cancel_requested'):
            session['cancel_requested'] = False
            raise HTTPException(status_code=409, detail='已取消问答，消息未提交')
        session["chat_messages"].append({"role": "user", "content": question})
        session["chat_messages"].append({"role": "assistant", "content": answer,
                                         "reasoning": reasoning})
        # 对话记录限长，防止长时间推演后会话无限增长（与 app.py 同口径）
        if len(session["chat_messages"]) > 40:
            session["chat_messages"] = session["chat_messages"][-40:]
        session["engine_last_meta"] = observer.meta()
        session["progress"] = None
        session['active_observer'] = None
    except Exception as exc:  # noqa: BLE001
        session["progress"] = None
        session['active_observer'] = None
        _record_failed_operation(session, observer, 'chat', exc)
        cancelled = bool(session.pop('cancel_requested', False)) or isinstance(exc, HTTPException) and exc.status_code == 409
        raise HTTPException(status_code=409 if cancelled else 502, detail=f"璇玑问答失败：{exc}") from exc
    return _snapshot(session)


@app.post("/api/sessions/{session_id}/cancel")
def cancel_session(session_id: str):
    """取消不取得模型操作锁。连接项目插件时调用原生Agent.cancel中止在途请求；
    没有原生控制通道时回退到入口/步界旗标。候选仍须经过提交前取消检查。
    """
    session = _get(session_id)
    session["cancel_requested"] = True
    native_result = harness_engine.cancel_observer(session.get('active_observer'))
    return {"ok": True,
            "runtime": native_result,
            "note": ("原生取消已送达运行时，正在停止本次候选提交。" if any(r.get('ok') for r in native_result)
                     else "已登记步界取消：当前没有可取消的原生运行，候选提交前会再次检查。")}


@app.get("/api/sessions/{session_id}/export")
def export_session(session_id: str):
    session = _get(session_id)
    # 所有状态更新完成后再生成快照，避免导出滞后一轮
    export_data = {
        "format": "gentactix-session-v1",
        "session": session_store.serializable(session),
        "turn_count": session["turn_count"],
        "war_state": session["war_state"],
        "history_logs": session["history"],
        "review": {
            "reasoning": session["review_reasoning"],
            "conclusion": session["review_text"],
            "audit": session.get('review_audit'),
        },
        "chat_messages": session["chat_messages"],
        "trajectory": session.get('trajectory', []),
        "causal_analysis": session.get('causal_analysis'),
        "counterfactuals": session.get('counterfactuals', []),
        "failed_operations": session.get('failed_operations', []),
    }
    body = json.dumps(export_data, ensure_ascii=False, indent=4)
    # 与 app.py 同款文件名；中文部分走 RFC 5987，ASCII 兜底给老下载器
    ascii_name = f"GenTactix_T{session['turn_count']}.json"
    utf8_name = quote(f"GenTactix_推演数据_T{session['turn_count']}.json")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'},
    )


def _native_tool_dispatch(context, payload):
    session = context['session']
    return agent_tools.execute_tool_call(session, session.get('war_state'), payload,
                                         pre_adj=context.get('pre_adj'))


runtime_bridge.register_dispatcher(_native_tool_dispatch)

from .analysis_routes import build_router  # Router depends on the shared operation lock/session accessor.
app.include_router(build_router(_get, _serialized))
from .scenario_routes import build_router as build_scenario_router
app.include_router(build_scenario_router(_serialized))
