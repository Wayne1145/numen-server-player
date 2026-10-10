"""复现 Context 恢复返回值契约，以及失败首次模型调用丢消息。"""
import sys,json,tempfile,threading
from pathlib import Path
sys.path.insert(0,'/mnt/d/numen-server-player/tools/numen-ab-lab')
from brain_server import CompanionContext
from action_brain import ActionBrainRuntime,TurnCheckpoint
from persistent_brain import BrainSessionStore
from active_agent import ActiveAgent,EventCursor,fingerprint
from event_inbox import EventInbox
out=[]
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp);store=BrainSessionStore(r,'s','id');store.append_pair('u','a')
 ctx=object.__new__(CompanionContext);ctx.lock=threading.Lock();ctx.lock_path=r/'lock';ctx.store=store
 class Runtime:
  def recover_turn(self,guidance=None):return {'state':'already_committed','turn_id':'t'}
 ctx.runtime=Runtime()
 try:ctx.recover_turn();out.append({'id':'recovery_result_contract','bug_reproduced':False})
 except KeyError as e:out.append({'id':'recovery_result_contract','bug_reproduced':True,'error':str(e)})
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp);ev={'kind':'chat','source':'Wayne','detail':'@yachiyo 帮我砍树','time':'2'}
 class C:
  def __init__(self):self.calls=0;self.inbox=EventInbox(r/'inbox');self.runtime=type('R',(),{'checkpoint':TurnCheckpoint(r/'ck')})()
  def list_recent_events(self,count=50):return [ev]
  def get_self_status(self):return {'hp':20,'hunger':20}
  def run_turn(self,msg):self.calls+=1;raise ConnectionError('provider unavailable before first tool')
 ctx=C();cursor=EventCursor(r/'cursor');cursor.save(fingerprint({**ev,'time':'1'}));agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,mention_words=['@yachiyo']);agent._send_reply=lambda *_:None
 try:agent.run_once('yachiyo')
 except ConnectionError:pass
 agent.run_once('yachiyo')
 out.append({'id':'provider_failure_loses_message','bug_reproduced':ctx.calls==1 and not ctx.runtime.checkpoint.path.exists(),'calls':ctx.calls,'cursor_at_failed_event':cursor.load()==fingerprint(ev),'inbox_remaining':len(ctx.inbox.read())})
Path('/home/wayne/.hermes/cache/scratch/numen-serena-audit/probe-contract.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))
