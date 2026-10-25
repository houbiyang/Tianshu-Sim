"""Isolated UI fixture; only model order responses are fixed. No real LLM calls."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
out=Path(os.environ.get('GENTACTIX_QA_OUT',str(ROOT/'.cache/qa/missions/browser')));out.mkdir(parents=True,exist_ok=True)
for key,suffix in [('SESSION_DIR','sessions'),('TRACE_DIR','traces'),('ROLE_INDEX','roles.json'),('LLM_CONFIG','llm.enc'),('VAULT_KEY_FILE','vault.key')]:os.environ['GENTACTIX_'+key]=str(out/suffix)
os.environ['GENTACTIX_NATIVE_RUNTIME']='0'
from backend import main, harness_engine as h
from types import SimpleNamespace
import uvicorn
h.PERSIST_ROLES=False;h.LLM_BASE_URL='http://127.0.0.1:8000/v1';h.LLM_API_KEY='';h.LLM_MODEL='界面验收夹具（无模型调用）'
h.get_role_session=lambda sid:SimpleNamespace(id=sid)
h.get_ai_response=lambda *args,**kwargs:{'orders':[{'unit':f'红方教学编组{i}','action':'conceal'} for i in range(1,4)],'reason':'界面测试夹具'}
if __name__=='__main__':uvicorn.run(main.app,host='127.0.0.1',port=8725,log_level='warning')
