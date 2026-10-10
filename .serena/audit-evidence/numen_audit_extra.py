"""追加持久化与恢复跨进程边界复现，不读真实会话。"""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,'/mnt/d/numen-server-player/tools/numen-ab-lab')
from persistent_brain import BrainSessionStore
from action_brain import ActionBrainRuntime,TurnCheckpoint
out=[]
# 最后一行半写记录虽被忽略，但追加没有截断损坏尾巴。
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp);s=BrainSessionStore(r,'s','id');s.path.parent.mkdir(parents=True,exist_ok=True);s.path.write_text('{"messages":[')
 s=BrainSessionStore(r,'s','id');s.append_pair('hello','reply')
 try:
  again=BrainSessionStore(r,'s','id');out.append({'id':'torn_tail','bug_reproduced':len(again.messages())!=2,'restored_messages':len(again.messages())})
 except Exception as exc:out.append({'id':'torn_tail','bug_reproduced':True,'error':str(exc)})
# Context 缓存会让 store 的事务集合过期：另进程提交后仍认为同一 turn 未提交。
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp);a=BrainSessionStore(r,'s','id');b=BrainSessionStore(r,'s','id');b.append_batch([{'role':'user','content':'u'},{'role':'assistant','content':'done'}],transaction_id='same-turn')
 ck=TurnCheckpoint(r/'ck.json');ck.write({'state':'committing','turn_id':'same-turn','transcript':[{'role':'user','content':'u'},{'role':'assistant','content':'done'}]})
 runtime=ActionBrainRuntime(store=a,checkpoint=ck,mcp=None,companion='yachiyo',model=None,tools=[],requires_control={})
 runtime.recover_turn();fresh=BrainSessionStore(r,'s','id')
 out.append({'id':'cached_store_duplicates_commit','bug_reproduced':len(fresh.messages())==4,'messages':len(fresh.messages())})
Path('/home/wayne/.hermes/cache/scratch/numen-serena-audit/probe-extra.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))
