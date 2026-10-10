"""不接触生产世界的独立缺陷复现；全部使用临时目录和 MCP 替身。"""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, '/mnt/d/numen-server-player/tools/numen-ab-lab')
from active_agent import ActiveAgent, EventCursor, fingerprint
from action_brain import ActionBrainRuntime, TurnCheckpoint
from persistent_brain import BrainSessionStore
from event_inbox import EventInbox

out = []
def event(kind='chat', time='2', detail='@yachiyo 采铁'):
    return {'kind':kind,'source':'Wayne','detail':detail,'time':time}

class Ctx:
    def __init__(self, root, events):
        self.events_list=events; self.inbox=EventInbox(root/'inbox'); self.calls=[]
        self.runtime=type('R',(),{})(); self.runtime.checkpoint=TurnCheckpoint(root/'ck.json')
    def list_recent_events(self,count=50):return self.events_list
    def get_self_status(self):return {'hp':20,'hunger':20}
    def recover_turn(self,guidance=None):self.calls.append(('recover',guidance));return {'final':'恢复完成'}
    def run_turn(self,message):self.calls.append(('run',message));return {'final':'执行了命令'}

# cancel 与旧聊天同批出现，不应再次执行旧聊天。
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); old=event(time='2'); cancel=event('cancel','3','@yachiyo cancel')
    ctx=Ctx(root,[cancel,old]);ctx.runtime.checkpoint.write({'state':'tool_dispatched'})
    cursor=EventCursor(root/'cursor');cursor.save(fingerprint(event(time='1',detail='旧')))
    agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,mention_words=['@yachiyo'])
    agent._send_reply=lambda *_:None
    agent.run_once('yachiyo')
    out.append({'id':'cancel_restarts_old_chat','bug_reproduced':any(c[0]=='run' for c in ctx.calls),'calls':ctx.calls})

# 只有 cancel 的轮询也不应启动“玩家任务”回合。
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);ctx=Ctx(root,[event('cancel','3','@yachiyo cancel')]); cursor=EventCursor(root/'cursor');cursor.save(fingerprint(event(time='1')))
    agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,mention_words=['@yachiyo']);agent._send_reply=lambda *_:None
    agent.run_once('yachiyo')
    out.append({'id':'cancel_triggers_model','bug_reproduced':bool(ctx.calls),'calls':ctx.calls})

# 不可用工具 early_final 必须像普通 final 一样提交并清 checkpoint。
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);store=BrainSessionStore(root/'sessions','s','id');ck=TurnCheckpoint(root/'ck.json')
    decisions=iter([{'type':'tool','name':'get_self_status','arguments':{}}, *[{'type':'tool','name':'not_existing','arguments':{}}]*3])
    class Mcp:
        def call(self,name,args):return {'hp':20,'hunger':20}
    runtime=ActionBrainRuntime(store=store,checkpoint=ck,mcp=Mcp(),companion='yachiyo',model=lambda *_:json.dumps(next(decisions)),tools=[{'name':'get_self_status'}],requires_control={'get_self_status':False},max_rounds=8)
    result=runtime.run_turn('测试')
    out.append({'id':'early_final_leaves_checkpoint','bug_reproduced':ck.path.exists() and not store.messages(),'checkpoint_exists':ck.path.exists(),'messages':len(store.messages()),'result':result.get('early_final')})

# 第一个进程刚启动且事件缓冲为空时，第一条新指令不能被当成历史跳过。
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);ctx=Ctx(root,[]);cursor=EventCursor(root/'cursor');agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,mention_words=['@yachiyo']);agent._send_reply=lambda *_:None
    agent.run_once('yachiyo');ctx.events_list=[event()];agent.run_once('yachiyo')
    out.append({'id':'first_command_skipped','bug_reproduced':not ctx.calls,'calls':ctx.calls,'cursor_exists':cursor.path.exists()})

Path('/home/wayne/.hermes/cache/scratch/numen-serena-audit/probe-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))
