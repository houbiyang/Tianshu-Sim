"""Isolated command UI fixture. No real model; never use with a real game directory."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(os.environ.get('GENTACTIX_QA_OUT', str(ROOT / '.cache/qa/commands-browser')))
OUT.mkdir(parents=True, exist_ok=True)
for key, suffix in [('SESSION_DIR', 'sessions'), ('TRACE_DIR', 'traces'), ('ROLE_INDEX', 'roles.json'),
                    ('LLM_CONFIG', 'llm.enc'), ('VAULT_KEY_FILE', 'vault.key'), ('DSH_HOME', 'home'), ('DSH_WORKSPACE', 'workspace')]:
    os.environ['GENTACTIX_' + key] = str(OUT / suffix)
os.environ['GENTACTIX_NATIVE_RUNTIME'] = '0'
from backend import main, harness_engine as h
import uvicorn

h.PERSIST_ROLES = False
h.LLM_API_KEY = ''
h.LLM_MODEL = '界面验收夹具（无真实模型）'

def propose(prompt, **kwargs):
    if '你是战棋指挥解析员' in prompt:
        selected = json.loads(prompt.split('选中单位：')[1].split('\n')[0])
        target = json.loads(prompt.split('map_target：')[1].split('\n')[0])
        if not selected:
            observation = json.loads(prompt.split('当前公开观察与动作能力：')[1].split('\n')[0])
            return {'orders': [{'unit_id': u['id'], 'action': 'hold'} for u in observation['units'] if u['team'] == observation['control_team']], 'no_fire': True}
        if not target:
            return {'orders': [], 'clarifications': ['请先在地图选择目的地']}
        return {'orders': [{'unit_id': selected[0], 'action': 'move', 'destination': target,
                            'duration': 'until_arrived'}], 'clarifications': []}
    return {'orders': [], 'decisions': []}

h.get_ai_response = propose
if __name__ == '__main__':
    uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ.get('GENTACTIX_QA_PORT', '18870')), log_level='warning')
