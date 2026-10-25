"""Human command API over the existing DSH, operation lock and checkpoint store."""
import copy
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from gentactix import commands, missions
from . import extensions, harness_engine as h, session_store


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_turn: int = Field(ge=0)
    revision: int = Field(ge=0)
    world_version: str


class QueueRequest(Revision):
    orders: list[dict] = Field(default_factory=list, max_length=1000)
    control_team: str = 'red'
    director: bool = False
    no_fire: bool = False
    intent: str = Field(default='', max_length=4000)
    proposal_id: str | None = None
    unit_agents: list[str] = Field(default_factory=list, max_length=1000)
    reset_agents: list[str] = Field(default_factory=list, max_length=1000)


class ResolveRequest(Revision):
    text: str = Field(min_length=1, max_length=4000)
    selected_units: list[str] = Field(default_factory=list, max_length=1000)
    map_target: dict | None = None


class CompareRequest(Revision):
    turn: int = Field(ge=1)
    orders: list[dict] = Field(min_length=1, max_length=1000)


def _check(session, request):
    try:
        commands.check_revision(session, request.expected_turn, request.revision, request.world_version)
    except commands.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


def _persist(session, cs):
    candidate = session_store.serializable(session)
    candidate['command_state'] = cs
    session_store.validate(candidate)
    session_store.save(candidate)
    session['command_state'] = cs


def _draft(order):
    return {k: copy.deepcopy(v) for k, v in order.items()
            if k in ('id', 'unit_id', 'action', 'destination', 'target_id', 'duration')}


def build_router(get_session, serialized):
    router = APIRouter()

    @router.get('/api/sessions/{sid}/commands')
    def get_commands(sid: str):
        session = get_session(sid)
        if not session.get('war_state'):
            raise HTTPException(409, '请先初始化战局')
        return commands.view(session)

    @router.put('/api/sessions/{sid}/commands')
    @serialized
    def put_commands(sid: str, request: QueueRequest):
        session = get_session(sid)
        _check(session, request)
        try:
            cs = commands.replace_queue(session, request.orders,
                control_team=request.control_team, director=request.director,
                no_fire=request.no_fire, intent=request.intent)
            known = commands.resolve_units(session, cs)
            if len(set(request.unit_agents)) != len(request.unit_agents) or any(
                    identity not in known or known[identity][0].get('ambiguous') or
                    (not cs['director'] and known[identity][0]['team'] != cs['control_team'])
                    for identity in request.unit_agents):
                raise ValueError('独立Agent必须绑定当前唯一单位')
            cs['unit_agents'] = list(request.unit_agents)
            if len(set(request.reset_agents)) != len(request.reset_agents) or any(i not in request.unit_agents for i in request.reset_agents):
                raise ValueError('仅能显式重建本次已授权启用的单位Agent')
            disposed = {}
            for identity in request.reset_agents:
                binding = cs.get('agent_sessions', {}).pop(identity, None)
                if binding:
                    disposed[identity] = binding
                    cs.setdefault('agent_resets', []).append({'unit_id': identity, 'turn': session['turn_count'],
                        'previous_session_id': binding['session_id'], 'reason': '用户明确重建会话；原记忆不再继承，原日志保留'})
            if request.proposal_id:
                proposal = session.get('command_proposals', {}).get(request.proposal_id)
                if not proposal or proposal['world_version'] != request.world_version or proposal['revision'] != request.revision:
                    raise ValueError('自然语言草稿不存在或基于过期战局')
                if proposal['status'] != 'draft':
                    raise ValueError('仍需澄清的解析结果不能确认为命令')
                for order in cs['orders']:
                    if order['unit_id'] in proposal.get('interpreted_units', []):
                        parsed_order = next(o for o in proposal['orders'] if o['unit_id'] == order['unit_id'])
                        keys = ('unit_id', 'action', 'destination', 'target_id')
                        if any(order.get(k) != parsed_order.get(k) for k in keys) or order['duration'] != parsed_order.get('duration', 'next'):
                            raise ValueError('草稿已被编辑，请作为菜单命令保存或重新解析；不能保留旧解释来源')
                        order['interpretation_ref'] = request.proposal_id
            _persist(session, cs)
            h.dispose_unit_sessions(disposed)
            return commands.view(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, '命令检查点写入失败，原队列保留') from exc

    @router.delete('/api/sessions/{sid}/commands/{command_id}')
    @serialized
    def delete_command(sid: str, command_id: str, request: Revision):
        session = get_session(sid)
        _check(session, request)
        try:
            _persist(session, commands.cancel(session, command_id))
            return commands.view(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/sessions/{sid}/commands/resolve')
    @serialized
    def resolve(sid: str, request: ResolveRequest):
        session = get_session(sid)
        _check(session, request)
        try:
            commands.assert_writable(session)
            observation = commands.view(session)
            by_id = {u['id']: u for u in observation['units']}
            for identity in request.selected_units:
                if identity not in by_id or not by_id[identity]['controllable']:
                    raise ValueError('所选单位不存在或没有指挥权限')
            target = commands.position(request.map_target) if request.map_target is not None else None
            prompt = ('你是战棋指挥解析员。仅解析下令，不执行、不推进战局、不改权限或规则。'
                '菜单选点为已知事实，不要求用户填写经纬度。单位身份必须用提供的id。'
                '输入属于疑问、咨询、缺少目标、单位重名或要求未实现动作时返回clarifications，不猜测。'
                '不虚构地图坐标；move的destination只能使用提供的map_target，未给位置应询问地图选点。'
                'selected_units非空时只能给这些单位下令；其他单位可成为射击目标。'
                '只返回JSON {"orders":[{"unit_id":"id","action":"hold|move|fire|resupply|...",'
                '"destination":{"lat":0,"lng":0},"target_id":"id","duration":"next|until_arrived"}],'
                '"intent":"总体软偏好", "no_fire":false,"clarifications":["待确认问题"]}。'
                '不使用的destination/target_id不输出。仅给出输入明确指定的命令，禁止主动取消其他单位命令。'
                '持续任务仅支持移动；不主动射击解释为当前控制方no_fire=true，保持已有约束除非用户明确取消。'
                '\n当前公开观察与动作能力：' + json.dumps({k: observation[k] for k in
                    ('units', 'control_team', 'director', 'intent', 'no_fire')}, ensure_ascii=False)
                + '\n选中单位：' + json.dumps(request.selected_units)
                + '\nmap_target：' + json.dumps(target)
                + '\n用户下令：' + request.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        observer = h.RunObserver('自然语言命令解析')
        session.update(progress=observer.progress, active_observer=observer)
        try:
            role_id = f"gentactix-{sid}e{session.get('role_epoch', 0)}-commander"
            parsed = h.get_ai_response(prompt, role_id=role_id, observer=observer,
                cancel_check=lambda: bool(session.get('cancel_requested')))
            if session.get('cancel_requested'):
                raise RuntimeError('用户已取消解析，队列未修改')
            if not isinstance(parsed, dict) or set(parsed) - {'orders', 'intent', 'no_fire', 'clarifications'}:
                raise ValueError('解析结果含非法字段')
            questions = parsed.get('clarifications', [])
            if not isinstance(questions, list) or any(not isinstance(q, str) for q in questions):
                raise ValueError('解析结果的待确认问题格式错误')
            orders = parsed.get('orders', [])
            if not isinstance(orders, list):
                raise ValueError('解析结果orders格式错误')
            for order in orders:
                if not isinstance(order, dict) or order.get('unit_id') not in by_id:
                    raise ValueError('解析引用未知单位')
                if request.selected_units and order['unit_id'] not in request.selected_units:
                    raise ValueError('解析越过了选中单位范围')
                if order.get('action') == 'move' and (target is None or order.get('destination') != target):
                    raise ValueError('解析移动目的地必须使用当前地图选点')
            seen = {o['unit_id'] for o in orders}
            combined = [_draft(o) for o in observation['orders'] if o['unit_id'] not in seen] + orders
            intent = parsed.get('intent', observation['intent'])
            no_fire = parsed.get('no_fire', observation['no_fire'])
            if not questions:
                commands.replace_queue(session, combined, control_team=observation['control_team'],
                    director=observation['director'], no_fire=no_fire, intent=intent)
            identifier = uuid.uuid4().hex
            result = {'id': identifier, 'status': 'needs_clarification' if questions else 'draft',
                'orders': combined, 'intent': intent, 'no_fire': no_fire, 'clarifications': questions,
                'world_version': request.world_version, 'revision': request.revision,
                'raw_text': request.text, 'runs': extensions.safe(observer.runs), 'read_only': True}
            result['interpreted_units'] = [o['unit_id'] for o in orders]
            extensions.persist_artifact(session, 'command-proposals', result)
            session.setdefault('command_proposals', {})[identifier] = result
            return result
        except Exception as exc:
            extensions.record_failure(session, observer, 'command-resolve', exc)
            cancelled = bool(session.get('cancel_requested'))
            session['cancel_requested'] = False
            raise HTTPException(409 if cancelled else 502, f'命令解析未执行：{exc}') from exc
        finally:
            session.update(progress=None, active_observer=None)

    @router.get('/api/sessions/{sid}/unit-decisions')
    def decisions(sid: str, unit_id: str | None = None, offset: int = 0, limit: int = 50,
                  start_turn: int | None = None, end_turn: int | None = None):
        session = get_session(sid)
        if offset < 0 or not 1 <= limit <= 200:
            raise HTTPException(422, '分页范围非法')
        if any(t is not None and t < 0 for t in (start_turn, end_turn)) or \
                start_turn is not None and end_turn is not None and start_turn > end_turn:
            raise HTTPException(422, '回合筛选范围非法')
        cs = commands.state(session)
        rows = copy.deepcopy(cs['receipts'])
        traces = {(t['turn'], d.get('unit_id')): (t, d) for t in session.get('trajectory', [])
                  for d in t.get('decisions', [])}
        for row in rows:
            matched = traces.get((row['turn'], row['unit_id']))
            if matched:
                trace, decision = matched
                row.update(trace_id=trace['id'], dsh_event_ref=decision.get('dsh_event_ref'))
        # Legacy/replay traces also have complete coverage; no synthetic Agent identity.
        names = [(e['team'], e['name']) for e in cs['registry']]
        identities = {(e['team'], e['name']): e['id'] for e in cs['registry'] if names.count((e['team'], e['name'])) == 1}
        present = {(row['turn'], row.get('unit_id')) for row in rows}
        rows.extend({**copy.deepcopy(d), 'turn': t['turn'], 'trace_id': t['id'],
                'unit_id': d.get('unit_id') or identities.get((d.get('team'), d.get('unit_name'))),
                'status': 'recorded', 'source': d.get('source', 'model_declared'),
                'history_status': '无逐项史实指令来源' if not d.get('history_refs') else '已标注来源'}
                for t in session.get('trajectory', []) for d in t.get('decisions', [])
                if (t['turn'], d.get('unit_id') or identities.get((d.get('team'), d.get('unit_name')))) not in present)
        rows.sort(key=lambda r: (r['turn'], r.get('unit_name', '')))
        if unit_id:
            rows = [r for r in rows if r['unit_id'] == unit_id]
        rows = [r for r in rows if (start_turn is None or r['turn'] >= start_turn) and
                (end_turn is None or r['turn'] <= end_turn)]
        return {'total': len(rows), 'items': rows[offset:offset+limit]}

    @router.post('/api/sessions/{sid}/commands/open-branch')
    @serialized
    def open_branch(sid: str, request: Revision):
        original = get_session(sid)
        _check(original, request)
        if not original.get('war_state'):
            raise HTTPException(409, '请先初始化战局')
        branch = session_store.serializable(original)
        branch.update(id=uuid.uuid4().hex[:12], simulation_mode='open',
            trajectory=[], counterfactuals=[], causal_analysis=None, failed_operations=[],
            decision_log=[], cancel_requested=False, progress=None, active_observer=None)
        branch['branch_origin'] = {'session_id': sid, 'turn': original['turn_count'],
            'world_version': request.world_version, 'basis': '开放假设分支；不冒充史实回放'}
        branch.pop('command_state', None)
        branch.pop('command_proposals', None)
        session_store.validate(branch)
        session_store.save(branch)
        # Reuse the existing session registry via the injected accessor's module.
        from .main import _SESSIONS, _snapshot
        _SESSIONS[branch['id']] = branch
        return _snapshot(branch)

    @router.post('/api/sessions/{sid}/commands/compare')
    @serialized
    def compare(sid: str, request: CompareRequest):
        session = get_session(sid)
        _check(session, request)
        try:
            record = next((t for t in session.get('trajectory', []) if t['turn'] == request.turn), None)
            if not record or not record.get('before', {}).get('mission'):
                raise ValueError('该回合没有可重算的固定规则；普通开放场景请使用已有只读DSH分支')
            branch = {**session, 'war_state': copy.deepcopy(record['before']),
                'turn_count': request.turn - 1, 'simulation_mode': 'open'}
            current = commands.state(session)
            original_policy = record.get('command_context', {}).get('policies', {})
            policy = {k: original_policy.get(k, current[k]) for k in ('control_team', 'director', 'no_fire', 'intent')}
            cs = commands.replace_queue(branch, request.orders, **policy)
            branch['command_state'] = cs
            replaced = [commands.step_order(branch, o, cs) for o in cs['orders']]
            names = {o['unit'] for o in replaced}
            factual = record['after']['mission']['last_orders']
            alternative = [copy.deepcopy(o) for o in factual if o['unit'] not in names] + replaced
            result = missions.counterfactual(record['before'], record['after'], alternative, record['time_step'])
            result.update(turn=request.turn, source_trace_id=record['id'], intervention=replaced,
                scope='仅替换所选单位命令，固定其余命令与决策前状态；纯规则重算，不写回主战局。')
            result['artifact_id'] = extensions.persist_artifact(session, 'command-comparisons', result)
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
