"""🧠 dsh harness 调用层：推演三脑全部经 DeepSeek Harness（sdk-minimal profile）。

与旧 engine.py（OpenAI SDK 直连 DeepSeek）的差别：
- 模型调用不再由本进程直接发 HTTP，而是拉起 `dsh --profile sdk-minimal`
  子进程，由 harness 的 DeepSeek 适配器经 **局域网 vLLM**（OpenAI 兼容端点）出结果；
- dsh_home 指向项目内 `.dsh-wargame-home/`，**绝不读写 `~/.dsh`**，
  现有 dsh 全局配置（provider、profile、会话）不受任何影响；
- 坐标纠错反思循环、JSON 抽取语义与旧引擎保持一致。

连接参数（局域网 vLLM）从环境变量读取，缺省回落到仓库根目录的
`.env.wargame`（已 gitignore，密钥不入库）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from urllib.parse import urlsplit
from types import SimpleNamespace
from pathlib import Path

from deepseek_harness import DeepSeekHarness

from gentactix.config import MAX_API_RETRIES
from gentactix.geo import get_coordinate_errors
from gentactix.state import normalize_state_units
from gentactix.battlefield import CAPABILITY_NOTE
from . import runtime_bridge, extensions

ROOT = Path(__file__).resolve().parent.parent

# ==========================================
# ⚙️ 局域网 vLLM 接入配置
# ==========================================
def _load_dotenv() -> None:
    """把 .env.wargame 的 KEY=VALUE 载入 os.environ（不覆盖已有变量）。"""
    path = ROOT / ".env.wargame"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

LLM_BASE_URL = os.environ.get("GENTACTIX_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
LLM_API_KEY = os.environ.get("GENTACTIX_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("GENTACTIX_LLM_MODEL", "qwen3.8-flash-next-iq3_s")
LLM_MAX_TOKENS = int(os.environ.get("GENTACTIX_LLM_MAX_TOKENS", "16384"))

# 独立 Harness home / workspace：与 ~/.dsh 完全隔离
DSH_HOME = os.environ.get("GENTACTIX_DSH_HOME", str(ROOT / ".dsh-wargame-home"))
DSH_WORKSPACE = os.environ.get("GENTACTIX_DSH_WORKSPACE", str(ROOT / ".dsh-wargame-workspace"))

# 局域网模型不在适配器建议目录里，显式声明上下文容量；观测层按同一数字预警。
CONTEXT_WINDOW = int(os.environ.get("GENTACTIX_CONTEXT_WINDOW", "262144"))
# 单次模型调用超时：实测大生成可到 ~10 分钟，默认 900s 容纳之（超时 ⇒ 可见失败）
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("GENTACTIX_REQUEST_TIMEOUT_SECONDS", "900"))
from . import llm_settings
_saved_llm = llm_settings.load()
ROLE_POLICIES = (_saved_llm or {}).get('role_policies') or {}
RUNTIME_MAX_STEPS = (_saved_llm or {}).get('max_steps', int(os.environ.get('GENTACTIX_MAX_RUNTIME_STEPS', '8')))
RUNTIME_MAX_TOOL_CALLS = (_saved_llm or {}).get('max_tool_calls', 16)
if _saved_llm:
    LLM_BASE_URL, LLM_MODEL, LLM_API_KEY = (_saved_llm[k] for k in ('base_url', 'model', 'api_key'))
    LLM_MAX_TOKENS, CONTEXT_WINDOW = (_saved_llm[k] for k in ('max_tokens', 'context_window'))
    REQUEST_TIMEOUT_SECONDS = _saved_llm['timeout_seconds']
NATIVE_RUNTIME = os.environ.get("GENTACTIX_NATIVE_RUNTIME", "1").lower() not in ("0", "false", "no")
PLUGIN_DIR = ROOT / "plugins" / "dsh-gentactix"
RUNTIME_STATUS = {"enabled": NATIVE_RUNTIME, "connected": False, "ptc": False}
_run_policies = {}


def role_policy(role="engine", *, tools=False):
    # Reasoning tokens share the output budget on vLLM. A 2048-token chat
    # default exhausted a real four-unit battle query before any final answer.
    defaults = {"engine": LLM_MAX_TOKENS, "strategist": 8192, "chat": 8192, 'connection-test': 512, 'planner': 4096,
                "causal": 8192, "counterfactual": LLM_MAX_TOKENS}
    prefix = "GENTACTIX_" + role.upper()
    saved = ROLE_POLICIES.get(role, {})
    default_effort = 'low' if role in ('chat', 'strategist', 'causal') else None
    return {"role": role, "max_tokens": min(LLM_MAX_TOKENS, saved.get('max_tokens', int(os.environ.get(prefix + "_MAX_TOKENS", defaults.get(role, LLM_MAX_TOKENS))))),
            "reasoning_effort": saved.get('reasoning_effort', os.environ.get(prefix + "_REASONING_EFFORT") or default_effort),
            "max_steps": RUNTIME_MAX_STEPS,
            "max_tool_calls": RUNTIME_MAX_TOOL_CALLS,
            "allowed_tools": ["get_observation", "retrieve_history", "evaluate_action", "inspect_trajectory", "evaluate_counterfactual"] if tools else []}

# 项目启动补丁注销 shell/文件工具；原生插件仅注册领域只读工具。
DSH_PERSONA = (
    "你是 GenTactix 战棋推演引擎。严格按用户消息中的任务要求作答："
    "要求 JSON 时只输出 JSON，不输出任何解释文字；要求中文战报时只输出中文正文。"
    "禁止使用 bash、str_replace_editor、浏览器或写文件。"
    "仅当任务明确允许时调用 GenTactix 领域只读工具；否则直接作答。"
    "应用层 JSON 工具协议与原生工具是不同模式，严格按当前任务要求执行。"
)

_lock = threading.Lock()
_harness: DeepSeekHarness | None = None

# 无 Streamlit 时引擎侧提示收集到这里，由后端转交前端显示。
ENGINE_NOTICES: list[dict] = []


def drain_notices() -> list[dict]:
    """取走并清空引擎/地理层积压的提示。"""
    from gentactix import geo
    notices = ENGINE_NOTICES[:] + geo.GEO_NOTICES[:]
    ENGINE_NOTICES.clear()
    geo.GEO_NOTICES.clear()
    return notices


def get_harness() -> DeepSeekHarness:
    """惰性启动并复用单个 dsh sdk-minimal 进程（JSON-RPC over stdio）。"""
    global _harness
    if _harness is not None:
        return _harness
    Path(DSH_WORKSPACE).mkdir(parents=True, exist_ok=True)
    extra_env = {}
    ready_file = Path(DSH_HOME) / f"gentactix-control-{uuid.uuid4().hex}.json"
    if NATIVE_RUNTIME:
        Path(DSH_HOME).mkdir(parents=True, exist_ok=True)
        extra_env = runtime_bridge.launch_env(ready_file)
        launch_patch = Path(DSH_HOME) / "gentactix-runtime.patch.yml"
        launch_patch.write_text((PLUGIN_DIR / 'launch.patch.yml').read_text().replace(
            '__GENTACTIX_NATIVE_PLUGIN__', json.dumps(str(PLUGIN_DIR / 'index.mjs'))))
    extra_patches = json.loads(os.environ.get('GENTACTIX_DSH_PATCHES', '[]'))
    if not isinstance(extra_patches, list) or not all(isinstance(p, str) for p in extra_patches):
        raise ValueError('GENTACTIX_DSH_PATCHES必须为补丁路径的JSON数组')
    _harness = DeepSeekHarness(
        provider="deepseek-official",
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        # The DSH adapter requires a nonempty credential even for keyless vLLM.
        api_key=LLM_API_KEY or ('' if urlsplit(LLM_BASE_URL).hostname == 'api.deepseek.com' else 'EMPTY'),
        max_tokens=LLM_MAX_TOKENS,
        profile="sdk-minimal",
        patches=(*((str(launch_patch),) if NATIVE_RUNTIME else ()), *extra_patches),
        dsh_home=DSH_HOME,
        cwd=DSH_WORKSPACE,
        env={
            "DSH_SYSTEM_PROMPT": DSH_PERSONA,
            # 局域网模型不在适配器建议目录里，显式声明上下文容量
            "DSH_CONTEXT_WINDOW": str(CONTEXT_WINDOW),
            **extra_env,
        },
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        _harness.start()
        if NATIVE_RUNTIME:
            for _ in range(50):
                if ready_file.is_file():
                    break
                time.sleep(.02)
            status = runtime_bridge.connect(ready_file)
            RUNTIME_STATUS.update(status, enabled=True, connected=True)
            ready_file.unlink(missing_ok=True)
    except Exception:
        if hasattr(_harness, 'client') and hasattr(_harness.client, '_runtime_diagnostics'):
            RUNTIME_STATUS['diagnostic'] = _harness.client._runtime_diagnostics()
        _harness.close()
        _harness = None
        RUNTIME_STATUS["connected"] = False
        raise
    return _harness


def close_harness() -> None:
    global _harness
    if _harness is not None:
        _harness.close()
        _harness = None
    _role_sessions.clear()
    _role_amnesia.clear()
    runtime_bridge.close()
    RUNTIME_STATUS["connected"] = False


# ==========================================
# 🧬 持续角色会话：战局 id + 角色 → 稳定的 DSH 会话（跨回合记忆）
# ==========================================
# 默认关：旧单发路径保持「每次新会话」的既有行为与测试。
# 开启后推演/复盘/问答各自绑定一个稳定会话，harness 保留其历史；
# 智能体模式（agent_tools）不受此开关影响，无条件用角色会话。
PERSIST_ROLES = os.environ.get("GENTACTIX_PERSIST_ROLES", "0") not in ("", "0", "false", "no")

_role_sessions: dict[str, object] = {}
_role_amnesia: set[str] = set()   # 刚被重建（历史没恢复成）的角色会话


def role_session_id(battle_session_id: str, role: str) -> str:
    """战局会话 id 天然唯一 ⇒ 角色会话绝不跨战局串上下文。"""
    return f"gentactix-{battle_session_id}-{role}"


def get_role_session(role_id: str):
    """进程内已持有 → 复用；否则从磁盘恢复；恢复失败 → 新建 + 可见警告。

    绝不静默失忆：resume 失败（会话不存在、崩溃后残留 session.lock 被占用）
    会进 ENGINE_NOTICES，并由 consume_role_amnesia 通知调用方注入决策摘要。
    """
    harness = get_harness()
    session = _role_sessions.get(role_id)
    if session is not None:
        return session
    try:
        session = harness.resume_session(role_id)
    except Exception as exc:  # noqa: BLE001
        ENGINE_NOTICES.append({
            "level": "warning",
            "message": (f"🧬 角色会话 `{role_id}` 无法恢复（{type(exc).__name__}: {exc}），"
                        "已新建 —— 该角色跨回合记忆丢失，任务消息仍自含、推演可继续。"),
        })
        _role_amnesia.add(role_id)
        session = harness.start_session(role_id)
    _role_sessions[role_id] = session
    return session


def forget_role_sessions(battle_prefix: str) -> None:
    """重新开局：丢弃进程内该战局的旧角色会话。

    DSH 会话没有清除 API，靠 id 里的 epoch 换出新会话天然隔离；
    这里只保证旧对象不再被复用、注册表不泄漏。
    """
    for rid in [r for r in _role_sessions if r.startswith(f"gentactix-{battle_prefix}")]:
        _role_sessions.pop(rid, None)
        _role_amnesia.discard(rid)


def consume_role_amnesia(role_id: str) -> bool:
    """True = 该角色会话刚被重建；调用方应在下一提示前注入既往决策摘要。"""
    if role_id in _role_amnesia:
        _role_amnesia.discard(role_id)
        return True
    return False


def _role_is_warm(role_id: str) -> bool:
    """角色会话已在进程内且记忆完好（问答可只发问题，不重灌上下文）。"""
    return role_id in _role_sessions and role_id not in _role_amnesia


# ==========================================
# 👁️ 执行观测：事件流 → 可见提示 + 元数据（观测与控制层的地基）
# ==========================================
class RunObserver:
    """一次模型操作（可含多次往返）的观测器。

    - `turn/start` → info「模型开始思考」；`step/start` 每 5 步节流一条，不刷屏；
    - `assistant/message.data.usage.totalTokens` 累计，越过上下文窗口 70%
      → 一次性 warning —— 窗口溢出导致的静默截断必须提前说出口；
    - 调用结束由 main.py 取 `meta()` 存进 `session["engine_last_meta"]`，
      快照透出为 `engine_meta`，前端 caption strip 显示「finish · steps · 用时」；
    - `progress` 是可变字典，main.py 在调用前挂到 `session["progress"]`，
      观测器边跑边更新（数据通路先建，前端轮询本轮不做）。
    """

    def __init__(self, label: str):
        self.label = label
        self.turns = 0
        self.steps = 0
        self.total_tokens = 0
        self.last_finish = None
        self.elapsed_ms = 0.0
        self._warned_ctx = False
        self.progress: dict = {"label": label, "turns": 0, "steps": 0, "tokens": 0}
        self.operation_id = uuid.uuid4().hex
        self.runs = []
        self.tool_executions = []
        self.active_sessions = set()
        self._events = {}

    def record_result(self, result, *, parent=None):
        sid = str(getattr(result, 'session_id', '') or '')
        for event in getattr(result, 'events', []):
            if isinstance(event, dict) and isinstance(event.get('seq'), int):
                key = (sid, event['seq'], event.get('type'))
                if key not in self._events:
                    self.on_notification(SimpleNamespace(method='session.event', payload={'sessionId': sid, 'event': event}))
        events = [extensions.event_summary(e, sid) for e in getattr(result, 'events', []) if isinstance(e, dict)]
        seqs = [e['seq'] for e in events if isinstance(e.get('seq'), int)]
        self.runs.append({'id': f'{self.operation_id}:r{len(self.runs)+1}', 'session_id': sid,
                          'parent': parent, 'finish_reason': getattr(result, 'finish_reason', None),
                          'first_seq': min(seqs) if seqs else None, 'last_seq': max(seqs) if seqs else None,
                          'event_count': len(events), 'events': events,
                          'evidence': 'dsh_session_events' if seqs else 'no_sequence_evidence'})

    def record_failure(self, exc):
        events = list(self._events.values())
        if events:
            self.runs.append({'id': f'{self.operation_id}:r{len(self.runs)+1}', 'session_id': events[-1]['session_id'],
                              'finish_reason': 'failed', 'events': events, 'event_count': len(events),
                              'first_seq': events[0].get('seq'), 'last_seq': events[-1].get('seq'),
                              'error': extensions.safe(str(exc)[:500]), 'evidence': 'dsh_session_events'})

    def on_notification(self, notification) -> None:
        if getattr(notification, "method", None) != "session.event":
            return
        payload = getattr(notification, "payload", None) or {}
        event = payload.get("event")
        if not isinstance(event, dict):
            return
        sid = str(payload.get('sessionId') or '')
        row = extensions.event_summary(event, sid)
        self._events[(sid, event.get('seq', len(self._events)), event.get('type'))] = row
        etype = event.get("type")
        if etype == "turn/start":
            self.turns += 1
            ENGINE_NOTICES.append({"level": "info",
                                   "message": f"🔄 {self.label}：模型开始思考（第 {self.turns} 轮）"})
        elif etype == "step/start":
            self.steps += 1
            if self.steps % 5 == 1:  # 节流：每 5 步透一条
                ENGINE_NOTICES.append({"level": "info",
                                       "message": f"⏳ {self.label}：推理进行中（第 {self.steps} 步）"})
        elif etype == "assistant/message":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            total = usage.get("totalTokens")
            if isinstance(total, (int, float)) and total > self.total_tokens:
                self.total_tokens = int(total)
            if (not self._warned_ctx and self.total_tokens
                    and self.total_tokens >= 0.7 * CONTEXT_WINDOW):
                self._warned_ctx = True
                ENGINE_NOTICES.append({
                    "level": "warning",
                    "message": (f"📏 上下文已用 {self.total_tokens}/{CONTEXT_WINDOW} token（超 70%），"
                                "后续回合可能被窗口溢出截断；建议尽快收尾或重新开局。")})
        self.progress.update({"turns": self.turns, "steps": self.steps,
                              "tokens": self.total_tokens})

    def meta(self) -> dict:
        return {"label": self.label, "finish_reason": self.last_finish,
                "turns": self.turns, "steps": self.steps,
                "tokens": self.total_tokens, "elapsed_ms": int(self.elapsed_ms),
                "operation_id": self.operation_id, "trace_runs": len(self.runs)}


class HarnessRunError(RuntimeError):
    """Public, sanitized guidance derived from a runtime finish event."""


def _gate(result):
    """结束原因与空文本的统一门控（complete_ex / run_role 共用）。"""
    if result.finish_reason not in _OK_FINISH_REASONS:
        code = ''
        for event in reversed(getattr(result, 'events', []) or []):
            data = event.get('data') or {}
            reason = data.get('reason')
            error = reason.get('error') if isinstance(reason, dict) else None
            if isinstance(error, dict) and isinstance(error.get('code'), str):
                code = error['code']
                break
            if event.get('type') == 'llm/retry' and isinstance(data.get('failure'), dict):
                code = str(data['failure'].get('code') or '')
        hints = {
            'MISSING_CREDENTIAL': '当前接口缺少API Key，请打开「模型配置」填写有效密钥后测试连接。',
            'TRANSPORT': '无法连接当前LLM接口，请在「模型配置」核对API地址、服务状态和网络，并测试当前连接。',
            'AUTHENTICATION': 'LLM接口鉴权失败，请在「模型配置」检查密钥。',
        }
        hint = hints.get(code, '请打开「模型配置」测试当前连接，核对接口、模型ID、密钥和输出预算。')
        if result.finish_reason == 'max-tokens':
            hint = '输出预算耗尽，请在「模型配置」增加token上限，或减小想定规模后重试。'
        raise HarnessRunError(
            f"harness 未完成（finish_reason={result.finish_reason}），"
            f"本次输出已拒收；{hint}")
    if not result.final_response.strip():
        raise HarnessRunError(
            f"harness 未返回内容（finish_reason={result.finish_reason}），请在「模型配置」测试当前连接")
    return result


def run_role(role_id: str, prompt: str, *, on_notification=None):
    """在角色会话里跑一轮：同会话跨回合，harness 保留该角色历史。"""
    session = get_role_session(role_id)
    _configure_runtime(role_id)
    with _lock:
        result = session.run(prompt, on_notification=on_notification)
    return _gate(result)


# 实测 15 个会话里 turn/end 只出现过 completed；error / max-tokens / None
# 一律视为失败 —— 宁可报错也不放行半截 JSON（截断的 updated_state 比
# 一次可见的失败破坏力大得多）。未来出现良性结束原因时在此单点放宽。
_OK_FINISH_REASONS = {"completed"}


def complete_ex(prompt: str, *, session_id: str | None = None,
                on_notification=None):
    """单轮 harness 调用，返回完整 RunResult（结束原因与空文本门控都在这一处）。"""
    harness = get_harness()
    session_id = session_id or f"gentactix-{uuid.uuid4().hex}"
    _configure_runtime(session_id)
    with _lock:
        result = harness.run(
            prompt,
            session_id=session_id,
            on_notification=on_notification)
    return _gate(result)


def _configure_runtime(session_id):
    if NATIVE_RUNTIME and RUNTIME_STATUS.get('connected'):
        runtime_bridge.control('/configure', {'session_id': session_id,
                                             'policy': _run_policies.get(session_id) or role_policy()})


def cancel_observer(observer):
    results = []
    if observer is not None:
        for sid in tuple(observer.active_sessions):
            try:
                results.append(runtime_bridge.control('/cancel', {'session_id': sid}))
            except Exception as exc:
                results.append({'ok': False, 'mode': 'cooperative', 'error': str(exc)[:250]})
    return results


def fork_run(parent, prompt, observer, *, role='counterfactual', context=None):
    """Native AgentRegistry fork from an exact completed DSH event prefix."""
    harness = get_harness()
    if not NATIVE_RUNTIME or not RUNTIME_STATUS.get('connected'):
        raise RuntimeError('原生分支插件未连接；不能将独立会话冒充DSH fork')
    parent_id = parent['session_id']
    if parent_id not in _role_sessions:
        # A restored battle retains event references, but the runtime registry is
        # empty after restart. Resume the exact parent; never replace missing
        # history with a newly created session as get_role_session may do.
        _role_sessions[parent_id] = harness.resume_session(parent_id)
    sid = f'gentactix-cf-{uuid.uuid4().hex}'
    observer.active_sessions.add(sid)
    if context is not None:
        runtime_bridge.bind(sid, context)
    t0 = time.monotonic()
    subscription = harness.client.subscribe_session_notifications(sid)
    stop = threading.Event()
    def consume():
        while not stop.wait(.05):
            try:
                subscription.drain(observer.on_notification)
            except Exception:
                return
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    try:
        payload = runtime_bridge.control('/fork-run', {
            'parent_session_id': parent['session_id'], 'boundary': parent['last_seq'],
            'session_id': sid, 'prompt': prompt,
            'policy': role_policy(role, tools=context is not None)}, timeout=REQUEST_TIMEOUT_SECONDS)
        stop.set()
        reader.join(timeout=.5)
        subscription.drain(observer.on_notification)
        result = SimpleNamespace(**payload)
        observer.record_result(result, parent={'session_id': parent['session_id'], 'seq': parent['last_seq'],
                                               'kind': 'native_agent_factory_seed'})
        observer.last_finish = result.finish_reason
        return _gate(result)
    finally:
        stop.set()
        reader.join(timeout=.5)
        subscription.close()
        observer.active_sessions.discard(sid)
        observer.elapsed_ms += (time.monotonic()-t0)*1000
        runtime_bridge.unbind(sid)


def _run_brain(prompt: str, role_id: str | None, on_notification=None,
               observer: RunObserver | None = None, role='engine', native_context=None):
    """三脑统一出口：有角色会话 → 同会话续写；没有 → 一次性新会话（旧行为）。

    observer 存在时：事件流挂观测器，并累计用时 / 记录结束原因（meta 单点）。
    """
    if observer is not None:
        on_notification = observer.on_notification
    sid = role_id or f'gentactix-{uuid.uuid4().hex}-{role}'
    _run_policies[sid] = role_policy(role, tools=native_context is not None)
    if native_context is not None:
        runtime_bridge.bind(sid, native_context)
    if observer is not None:
        observer.active_sessions.add(sid)
    t0 = time.monotonic()
    try:
        if role_id:
            result = run_role(sid, prompt, on_notification=on_notification)
        else:
            result = complete_ex(prompt, session_id=sid, on_notification=on_notification)
        if observer is not None:
            observer.last_finish = result.finish_reason
            observer.record_result(result)
        return result
    except Exception as exc:
        if observer is not None:
            observer.record_failure(exc)
        raise
    finally:
        _run_policies.pop(sid, None)
        runtime_bridge.unbind(sid)
        if observer is not None:
            observer.elapsed_ms += (time.monotonic() - t0) * 1000
            observer.active_sessions.discard(sid)


def complete(prompt: str) -> str:
    """单轮 harness 调用，返回最终 assistant 文本（语义与旧版一致）。"""
    return complete_ex(prompt).final_response


def extract_reasoning(events) -> str:
    """从最后一个 assistant/message 事件里收集 type=="reasoning" 的文本块。

    思维链不在 RunResult 顶层字段，而是随事件流的 content 块到达
    （SDK 的 final_response 同形状地只取 text 块，reasoning 块由本函数取）。
    """
    for event in reversed(list(events or [])):
        if not isinstance(event, dict) or event.get("type") != "assistant/message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        message = data.get("message")
        content_owner = message if isinstance(message, dict) else data
        content = content_owner.get("content")
        if not isinstance(content, list):
            continue
        parts = [str(block.get("text") or "") for block in content
                 if isinstance(block, dict) and block.get("type") == "reasoning"]
        return "\n\n".join(p for p in parts if p)
    return ""


def parse_json_object(raw_text: str) -> dict:
    """从模型回复里抽取 JSON 对象（markdown 围栏 / 裸文本兜底），失败必报错。"""
    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.split("\n", 1)[-1].rsplit("```", 1)[0]

    start_idx = clean_text.find('{')
    end_idx = clean_text.rfind('}')

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(clean_text[start_idx:end_idx + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI 返回的 JSON 无法解析：{exc}\n原始回复：\n{raw_text}") from exc
    raise ValueError(f"未能提取到有效的 JSON。AI 原始回复：\n{raw_text}")


# ==========================================
# 🧠 引擎 A：JSON 战局推演（含坐标反思循环）
# ==========================================
def get_ai_response(prompt: str, role_id: str | None = None,
                    observer: RunObserver | None = None, native_context=None) -> dict:
    """经 harness 生成 JSON 战局，并做 markdown 代码块 / 裸文本兜底抽取。"""
    return parse_json_object(_run_brain(prompt, role_id, observer=observer, native_context=native_context).final_response)


def get_ai_response_with_retry(prompt: str, max_retries: int = MAX_API_RETRIES,
                               role_id: str | None = None,
                               observer: RunObserver | None = None,
                               cancel_check=None, native_context=None) -> dict:
    """带反思循环的推演引擎，强制纠正 AI 坐标幻觉（语义与旧 engine 一致）。

    cancel_check在重试之间生效；项目原生插件另外通过Agent.cancel取消在途调用。
    """
    current_prompt = prompt
    result_json = None

    for attempt in range(max_retries):
        if cancel_check is not None and cancel_check():
            raise RuntimeError("用户已取消，坐标反思循环停止，战局未改动")
        result_json = get_ai_response(current_prompt, role_id=role_id, observer=observer, native_context=native_context)
        # 键名归一化必须早于坐标校验（中文键 → is_naval 判不出海军的坑）
        normalize_state_units(result_json)
        state_to_check = result_json.get("updated_state", result_json)
        errors = get_coordinate_errors(state_to_check)

        if not errors:
            if attempt > 0:
                ENGINE_NOTICES.append({"level": "success", "message": "✅ 模型已成功自我修正坐标！"})
            return result_json

        ENGINE_NOTICES.append({
            "level": "warning",
            "message": f"🛑 检测到海军单位上岸，正在进行第 {attempt + 1} 次重新推理...",
        })

        error_feedback = "\n\n【系统最高警告：坐标物理规则冲突！】\n你刚才生成的 JSON 中存在以下致命地理错误：\n"
        for err in errors:
            error_feedback += f"- {err}\n"
        error_feedback += "请你立刻重新评估战局，重新生成一组合法的 JSON 数据。必须确保所有海军单位的经纬度都在海洋中！"

        current_prompt += error_feedback

    ENGINE_NOTICES.append({
        "level": "error",
        "message": f"⚠️ AI 连续 {max_retries} 次未能修正坐标错误，系统强制放行，请注意地图穿模。",
    })
    return result_json


# ==========================================
# 🧠 引擎 B：战略复盘
# ==========================================
def get_strategic_review(history, current_state, role_id: str | None = None,
                         prefix: str = "", observer: RunObserver | None = None) -> tuple[str, str]:
    """首席战略推演官深度复盘。返回 (reasoning, conclusion)。

    思维链随事件流的 reasoning 内容块到达（不在 RunResult 顶层字段），
    由 extract_reasoning 取回；模型没产出思维块时才是空字符串。
    prefix 仅在角色会话失忆重建时携带既往决策摘要，任务消息本身保持自含。
    """
    prompt = prefix + f"""
    {CAPABILITY_NOTE}
    你现在是最高统帅部的首席战略推演官璇玑。
    以下是本次沙盘推演从开战至今的全部战报记录：
    {json.dumps(history, ensure_ascii=False, indent=2)}

    这是当前最新的战场单位状态（包含残余兵力、位置、士气、补给）：
    {json.dumps(current_state, ensure_ascii=False, indent=2)}

    请你抛开一切 JSON 格式束缚，用极其专业的军事术语，写一份 500 字左右的【战局深度复盘】。
    要求：
    1. 分析双方在刚才几次交锋中的战略得失（谁占了便宜，谁犯了致命错误）。
    2. 评估地形对当前局势的影响。
    3. 为处于劣势的一方提供下一步的“破局建议”，为处于优势的一方提供“稳固胜势建议”。
    4. 不仅要分析战术得失，还要说明当前的战术成果是否可以影响到战略层面的得失。
    """
    result = _run_brain(prompt, role_id, observer=observer, role='strategist')
    return extract_reasoning(result.events), result.final_response


# ==========================================
# 💬 引擎 C：实时问答
# ==========================================
def ask_xuanji(history, war_state, chat_messages, question: str,
               role_id: str | None = None,
               observer: RunObserver | None = None) -> tuple[str, str]:
    """统帅向璇玑提问：注入战场上下文，返回 (reasoning, answer)。"""
    # 角色会话记忆完好时，战报/沙盘/旧对话 harness 已经记得，只发问题；
    # 一次性会话（或刚失忆重建）必须自带全部上下文 —— 任务消息保持自含。
    if role_id and _role_is_warm(role_id):
        result = _run_brain(f"{CAPABILITY_NOTE}\n[user] {question}\n请回答统帅的最新问题。", role_id,
                            observer=observer, role='chat')
        return extract_reasoning(result.events), result.final_response

    system_prompt = f"""
    {CAPABILITY_NOTE}
    你是最高统帅部的首席战略推演官璇玑。冷酷、理性，深谙军事战略。
    以下是开战至今的全部战报：{json.dumps(history, ensure_ascii=False)}
    这是当前最新沙盘状态：{json.dumps(war_state, ensure_ascii=False)}
    请直接用专业的中文军事术语回答统帅的问题，不要输出 JSON，不要有多余寒暄。
    """

    parts = [system_prompt]
    # 追加最近的对话历史（保持记忆，最多回带 5 轮，避免上下文无限膨胀）
    for m in chat_messages[-5:]:
        parts.append(f"[{m['role']}] {m.get('content') or ''}")
    parts.append(f"[user] {question}")
    parts.append("请回答统帅的最新问题。")

    result = _run_brain("\n\n".join(parts), role_id, observer=observer, role='chat')
    return extract_reasoning(result.events), result.final_response
