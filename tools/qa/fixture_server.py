"""Browser QA fixture server on 8724; no model calls or production state.

Only model responses are scripted. HTTP, state, validation, traces, export,
encrypted settings and geography all use the application implementation.
"""
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
out = Path(os.environ.get('GENTACTIX_QA_OUT', str(ROOT / '.cache/qa/release-3.1.1/browser')))
out.mkdir(parents=True, exist_ok=True)
for name, value in {'NATIVE_RUNTIME': '0', 'SESSION_DIR': str(out/'sessions'),
                    'TRACE_DIR': str(out/'traces'), 'LLM_CONFIG': str(out/'llm.enc'),
                    'VAULT_KEY_FILE': str(out/'vault.key')}.items():
    os.environ['GENTACTIX_'+name] = value
from backend import main, harness_engine as h
from verify_desktop_backend import _init_state
import uvicorn


def result(value, session_id='fixture'):
    return SimpleNamespace(final_response=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                           finish_reason='completed', session_id=session_id, events=[], notifications=[])


def complete(prompt, *, session_id=None, on_notification=None):
    initial = _init_state()
    initial['current_time'] = '1952-10-14 04:00'
    initial['situation_report'] = '固定响应界面验收数据，不代表历史态势。'
    initial['red_units'][0].update(
        attack_range=12000, min_attack_range=1000, fire_sector={'bearing_deg': 90, 'arc_deg': 60},
        personnel={'committed': 1000, 'current': 900, 'casualties': 80, 'noncombat_losses': 20, 'source': '验收夹具'},
        equipment=[{'name': '榴弹炮', 'unit': '门', 'committed': 12, 'operational': 9, 'damaged': 1, 'destroyed': 2}],
        supply_node_id='fixture-depot')
    initial['supply_nodes'] = [{'id': 'fixture-depot', 'name': '验收补给点', 'team': 'red', 'lat': 35.68, 'lng': 112.76, 'source': '验收夹具'}]
    initial['terrain_features'] = [{'name': '验收山脊', 'kind': 'ridge', 'geometry': 'line',
                                   'coordinates': [[35.70, 112.80], [35.71, 112.82]], 'source': '验收夹具'}]
    if '推演初始化' in prompt: return result(initial)
    final = copy.deepcopy(initial); final['current_time'] = '1952-10-14 04:15'
    final['red_units'][0]['lng'] += .001
    return result({'updated_state': final, 'report': '固定响应界面验收：15分钟的几何试算。'})


def brain(prompt, role_id, *args, **kwargs):
    role = kwargs.get('role')
    if role == 'planner':
        return result({'reply': '想定草案已整理，请确认时间与目标。', 'ready': True,
                       'scenario': '上甘岭战役，1952年10月14日04:00，固定响应界面验收。',
                       'battle': next(b['name'] for b in main.battles()['battles'] if '上甘岭' in b['name']), 'time_step': '15分钟'})
    if role == 'connection-test': return result({'status': 'ok'})
    if role in ('chat', 'strategist'): return result('固定响应验收：地形与补给限制需要分别核对。')
    return complete(prompt)


h.get_role_session = lambda sid: SimpleNamespace(id=sid)
h.LLM_BASE_URL = 'http://127.0.0.1:8000/v1'; h.LLM_API_KEY = ''
h.LLM_MODEL = '固定响应界面验收'; h.PERSIST_ROLES = False
h.complete_ex = complete; h._run_brain = brain
if __name__ == '__main__': uvicorn.run(main.app, host='127.0.0.1', port=8724, log_level='warning')
