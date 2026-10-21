"""Model-assisted scenario drafting; does not create or mutate a battle."""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from gentactix import adjudication_lib, rag, sim_clock
from . import harness_engine as h


class PlanRequest(BaseModel):
    messages: list[dict]


def build_router(serialized):
    router = APIRouter()

    @router.post('/api/scenarios/plan')
    @serialized
    def plan(req: PlanRequest):
        messages = req.messages
        if not messages or len(messages) > 12 or any(m.get('role') not in ('user', 'assistant') or
                not isinstance(m.get('content'), str) or not 0 < len(m['content']) <= 4000 for m in messages):
            raise HTTPException(status_code=422, detail='请提供1至12条有效问答，每条不超过4000字')
        if sum(len(m['content']) for m in messages) > 16000:
            raise HTTPException(status_code=422, detail='问答内容过长，请精简后重试')
        catalog = adjudication_lib.list_battle_details()
        hits = rag.search(messages[-1]['content'], top_k=3)
        briefing, used = rag.build_briefing(hits, max_chars=1800)
        prompt = ('你是历史推演想定编辑。根据问答整理可编辑草案；信息不足时只问一个关键问题。'
            '不要生成战局单位、不要宣称已经开始推演或已核实所有史实。'
            '只返回JSON：{"reply":"简短说明或问题","ready":true或false,"scenario":"包括战役、时间、地点、双方与目标的想定正文",'
            '"battle":"下列库中的精确名称或空字符串","time_step":"15分钟等明确跨度"}。'
            '无法确认的历史数字不要补成确数。\n可选战役库：' + json.dumps(catalog, ensure_ascii=False) +
            '\n本地史料摘录：' + briefing + '\n问答：' + json.dumps(messages, ensure_ascii=False))
        try:
            result = h._run_brain(prompt, None, role='planner')
            answer = h.parse_json_object(result.final_response)
            if not isinstance(answer.get('reply'), str) or not isinstance(answer.get('ready'), bool):
                raise ValueError('invalid planner response')
            scenario = answer.get('scenario', '')
            if not isinstance(scenario, str) or len(scenario) > 6000 or (answer['ready'] and not scenario.strip()):
                raise ValueError('invalid scenario draft')
            time_step = answer.get('time_step') or '30分钟'
            sim_clock.duration_seconds(time_step)
            known = {b['name'] for b in catalog}
            return {'reply': answer['reply'][:3000], 'ready': answer['ready'], 'scenario': scenario,
                    'battle': answer.get('battle') if answer.get('battle') in known else '', 'time_step': time_step,
                    'basis': 'model_draft', 'sources': [{'source': hit['source'], 'section': hit['section']} for hit in hits[:used]],
                    'runtime_session_id': result.session_id}
        except Exception:
            raise HTTPException(status_code=502, detail='想定草案生成失败，请检查LLM配置后重试；已有战局未改动')

    return router
