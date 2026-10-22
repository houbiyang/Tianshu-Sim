"""Causal/trace API. All model branches start before the selected decision."""
from __future__ import annotations

import copy
import json
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gentactix import adjudicator, battlefield, causal, geo, integrity, state
from gentactix.battlefield import CAPABILITY_NOTE
from . import extensions, harness_engine as h


class AnalysisRequest(BaseModel):
    explain: bool = False


class CounterfactualRequest(BaseModel):
    turn: int = Field(ge=1)
    mode: str = 'geometry'
    undo: list[dict] = Field(default_factory=list, max_length=24)
    changes: list[dict] = Field(default_factory=list, max_length=24)
    instruction: str = Field(default='', max_length=3000)
    samples: int = Field(default=1, ge=1, le=3)


def _record(session, turn):
    for record in session.get('trajectory', []):
        if record['turn'] == turn:
            return record
    raise ValueError('未找到当前开局的回合轨迹；旧进程的历史战报不能补造决策前快照')


def verify_dsh_records(record, *, flush_timeout=2.0):
    # DSH publishes turn/end before its buffered JSONL writer has necessarily
    # flushed. Bound the wait for all runs together; missing evidence stays partial.
    deadline = time.monotonic() + flush_timeout
    rows = []
    for run in record.get('runs', []):
        sid = run.get('session_id', '')
        row = {'run_id': run['id'], 'session_id': sid, 'status': 'unavailable'}
        if re.fullmatch(r'[A-Za-z0-9_.-]+', sid) and isinstance(run.get('last_seq'), int):
            paths = list((Path(h.DSH_HOME) / 'sessions').glob(f'*/{sid}/session.v4.jsonl'))
            if len(paths) == 1:
                wanted = {(e['seq'], e.get('type')) for e in run.get('events', []) if isinstance(e.get('seq'), int)}
                while True:
                    found, corrupt = set(), False
                    with paths[0].open() as stream:
                        for line in stream:
                            # An unfinished final line can be a concurrent append.
                            if not line.endswith('\n'):
                                continue
                            try:
                                item = json.loads(line)
                            except json.JSONDecodeError:
                                corrupt = True
                                break
                            key = (item.get('seq'), item.get('type'))
                            if key in wanted:
                                found.add(key)
                    if corrupt or found == wanted or time.monotonic() >= deadline:
                        break
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
                row.update(status='corrupt' if corrupt else 'verified' if wanted and found == wanted else 'partial',
                           expected_events=len(wanted), found_events=len(found))
        rows.append(row)
    return rows


def _branch_candidate(result, before, record):
    from gentactix import sim_clock
    if result.get('action') == 'submit_orders':
        result = result.get('orders') or {}
    candidate = result.get('updated_state', result)
    if not isinstance(candidate, dict) or not isinstance(candidate.get('red_units'), list) or not isinstance(candidate.get('blue_units'), list):
        raise ValueError('分支缺少完整red_units/blue_units')
    candidate = copy.deepcopy(candidate)
    sim_clock.enforce_step(before, candidate, record['time_step'])
    state.normalize_state_units(candidate)
    errors = geo.get_coordinate_errors(candidate)
    if errors:
        raise ValueError('分支坐标物理校验失败：' + str(errors))
    if isinstance(result.get('eliminated'), list):
        candidate['eliminated'] = result['eliminated']
    candidate, roster = integrity.reconcile_roster(before, candidate)
    battlefield.carry_battlefield_state(before, candidate)
    numeric = integrity.check_numeric_integrity(before, candidate)
    pre = causal.geometry(before, record)
    violations = adjudicator.check_movement(before, candidate, pre)
    clock = causal.clock_check(before, candidate, record['time_step'])
    return {'state': candidate, 'report': str(result.get('report') or ''), 'roster': roster,
            'numeric_flags': numeric, 'movement_violations': violations, 'clock': clock,
            'admissible': not numeric and not violations and clock['status'] == 'verified'}


def _explanation_prompt(analysis):
    return (CAPABILITY_NOTE + '\n你是战局分析员。依据下面执行证据，给出简短、可核对的归因假说。'
            '状态变化不能直接证明意图或因果；不要输出内部思维链、胜率或因果贡献百分比。'
            '每条假说必须引用提供的trace_id，并说明替代解释和局限。只返回JSON：'
            '{"summary":"...","hypotheses":[{"claim":"...","evidence_ids":["..."],'
            '"alternative":"...","limitation":"..."}]}\n' + json.dumps(
                causal.explanation_evidence(analysis), ensure_ascii=False, separators=(',', ':')))


def build_router(get_session, serialized):
    router = APIRouter()

    @router.get('/api/runtime/plugins')
    def plugins():
        return {'application': extensions.registry.describe(),
                'native': {k: v for k, v in h.RUNTIME_STATUS.items() if k != 'diagnostic'},
                'policies': {r: h.role_policy(r) for r in ('engine', 'strategist', 'chat', 'causal', 'counterfactual')}}

    @router.get('/api/sessions/{session_id}/analysis')
    def get_analysis(session_id: str):
        session = get_session(session_id)
        data = copy.deepcopy(session.get('causal_analysis')) or extensions.analyze(session.get('trajectory', []))
        return {**data, 'counterfactuals': session.get('counterfactuals', [])}

    @router.get('/api/sessions/{session_id}/traces/{turn}')
    def get_trace(session_id: str, turn: int):
        session = get_session(session_id)
        try:
            record = _record(session, turn)
            return {'record': causal.record_summary(record), 'persistence': verify_dsh_records(record)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post('/api/sessions/{session_id}/analysis')
    @serialized
    def analyze(session_id: str, req: AnalysisRequest):
        session = get_session(session_id)
        if not session.get('trajectory'):
            raise HTTPException(status_code=409, detail='请先用新版后端初始化并提交至少一个回合')
        if req.explain and not any(r['turn'] > 0 for r in session['trajectory']):
            raise HTTPException(status_code=409, detail='归因解读需要至少一个已提交的推进回合')
        observer = h.RunObserver('因果解读')
        session['active_observer'] = observer
        session['progress'] = observer.progress
        try:
            data = extensions.analyze(session['trajectory'])
            if req.explain:
                response = h._run_brain(_explanation_prompt(data), None, observer=observer, role='causal')
                answer = h.parse_json_object(response.final_response)
                allowed = {r['id'] for r in session['trajectory']}
                hypotheses = answer.get('hypotheses')
                if not isinstance(answer.get('summary'), str) or not isinstance(hypotheses, list):
                    raise ValueError('因果解读缺少summary/hypotheses')
                kept = []
                for item in hypotheses[:8]:
                    ids = item.get('evidence_ids') if isinstance(item, dict) else None
                    if not isinstance(ids, list) or not ids or any(i not in allowed for i in ids):
                        raise ValueError('模型引用了不存在的轨迹证据，解读未采纳')
                    kept.append({k: item.get(k, '') for k in ('claim', 'evidence_ids', 'alternative', 'limitation')})
                data['explanation'] = {'summary': answer['summary'], 'hypotheses': kept,
                                       'basis': 'model_hypothesis', 'runs': observer.runs}
            if session.get('cancel_requested'):
                raise RuntimeError('已取消因果解读')
            extensions.persist_artifact(session, 'analyses', data)
            session['causal_analysis'] = data
            return {**data, 'counterfactuals': session.get('counterfactuals', [])}
        except Exception as exc:
            cancelled = bool(session.pop('cancel_requested', False))
            raise HTTPException(status_code=409 if cancelled else 502, detail=f'因果分析未提交：{exc}') from exc
        finally:
            session['active_observer'] = None
            session['progress'] = None

    @router.post('/api/sessions/{session_id}/counterfactual')
    @serialized
    def counterfactual(session_id: str, req: CounterfactualRequest):
        session = get_session(session_id)
        observer = h.RunObserver('反事实分支')
        session['active_observer'] = observer
        session['progress'] = observer.progress
        try:
            record = _record(session, req.turn)
            if req.mode == 'geometry':
                data = causal.geometry_counterfactual(record, req.undo, req.changes)
            elif req.mode == 'model':
                if not req.instruction.strip():
                    raise ValueError('模型反事实需要明确替代决策')
                previous = _record(session, req.turn-1)
                parents = [r for r in previous.get('runs', []) if isinstance(r.get('last_seq'), int)]
                if not parents:
                    raise ValueError('决策前回合缺少DSH事件边界，不能原生fork；请用新版后端重新开局')
                parent = parents[-1]
                prompt = record['prompt'] + ('\n【成对重演】从给定决策前状态推进同样的一个回合。'
                    '本次不用应用层tool_calls循环；最终只返回完整updated_state/report/decisions JSON。'
                    '不接收原始回合结果作为输入，不擅自改变时间跨度。')
                samples = []
                past = [r for r in session['trajectory'] if r['turn'] < req.turn]
                branch_context = {'session': {'war_state': copy.deepcopy(record['before']),
                    'trajectory': copy.deepcopy(past), 'history': [r.get('report', '') for r in past],
                    'use_rag': session.get('use_rag'), 'battle_choice': session.get('battle_choice'),
                    'scenario_input': session.get('scenario_input')},
                    'pre_adj': causal.geometry(record['before'], record)}
                for _ in range(req.samples):
                    pair = {}
                    for arm, instruction in (('control', '按原任务作决策，不追加干预。'), ('intervention', req.instruction)):
                        if session.get('cancel_requested'):
                            raise RuntimeError('用户取消反事实重演')
                        run = h.fork_run(parent, prompt + '\n本分支指令：' + instruction, observer, context=branch_context)
                        parsed = h.parse_json_object(run.final_response)
                        pair[arm] = _branch_candidate(parsed, record['before'], record)
                        pair[arm]['run'] = observer.runs[-1]
                    control = causal.geometry(pair['control']['state'], record)
                    alternative = causal.geometry(pair['intervention']['state'], record)
                    pair['metrics'] = causal.compare_metrics(control, alternative)
                    pair['status_changes'] = causal.verdict_changes(control, alternative)
                    pair['admissible'] = pair['control']['admissible'] and pair['intervention']['admissible']
                    samples.append(pair)
                data = {'mode': 'paired_model_fork', 'read_only': True, 'turn': req.turn,
                        'source_trace_id': record['id'], 'parent_session_id': parent['session_id'],
                        'parent_seq': parent['last_seq'], 'instruction': req.instruction,
                        'samples': samples, 'scope': '同一决策前DSH事件前缀、相同态势和参数，两条独立原生子会话。',
                        'limitations': ['未控制服务端采样随机性；成对重演差异不等于已识别的真实战役因果效应',
                                        '时间、位移或计数校验未通过的样本标为不可采纳，不计算胜率与因果贡献百分比',
                                        '史料与模型既有历史知识可能引入结局先验，不能等同于真实世界随机对照实验',
                                        '没有新增确定性战损算法，反事实伤亡属于模型假设']}
            else:
                raise ValueError('mode必须为geometry或model')
            if session.get('cancel_requested'):
                raise RuntimeError('用户取消反事实分析')
            data['id'] = uuid.uuid4().hex
            extensions.persist_artifact(session, 'counterfactuals', data)
            session.setdefault('counterfactuals', []).append(data)
            return data
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            cancelled = bool(session.pop('cancel_requested', False))
            raise HTTPException(status_code=409 if cancelled else 502, detail=f'反事实未提交：{exc}') from exc
        finally:
            session['active_observer'] = None
            session['progress'] = None

    return router
