import com.dwinovo.numen.server.*;
import com.dwinovo.numen.server.mcp.*;
import com.google.gson.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.lang.reflect.*;

/** 仓库外纯协议探针：不连接 Minecraft，不产生真实世界动作。 */
public class NumenAuditProbe {
 public static void main(String[] args) throws Exception {
  UUID id=UUID.randomUUID(); AtomicInteger invokes=new AtomicInteger(); CountDownLatch both=new CountDownLatch(2);
  ServerNumenActuator fake=(ServerNumenActuator)Proxy.newProxyInstance(NumenAuditProbe.class.getClassLoader(),new Class[]{ServerNumenActuator.class},(p,m,a)->{
   if(m.getName().equals("invoke")){
    if("transfer".equals(a[3])) { invokes.incrementAndGet(); return CompletableFuture.completedFuture(ToolResult.of("{\"success\":true,\"message\":\"moved items\"}")); }
    int n=invokes.incrementAndGet();both.countDown();if(!both.await(3,TimeUnit.SECONDS))throw new IllegalStateException("race did not synchronize");
    return CompletableFuture.completedFuture(ToolResult.of("{\"success\":true,\"data\":{\"task_id\":\"t"+n+"\"}}"));
   }
   if(m.getName().equals("listCompanions"))return CompletableFuture.completedFuture(List.of());
   return CompletableFuture.completedFuture(null);
  });
  ServerMcpServer server=new ServerMcpServer(new McpNetConfig(true,"127.0.0.1",0,65536,16,32,4,10,1000),fake,AuthTokens.empty(),SecurityPolicy.defaults());
  Method call=ServerMcpServer.class.getDeclaredMethod("invokeTool",McpPrincipal.class,String.class,JsonObject.class);call.setAccessible(true);
  JsonObject body=new JsonObject();body.addProperty("companion",id.toString());body.addProperty("client_action_id","same-action");body.addProperty("lease_id","l");
  ExecutorService workers=Executors.newFixedThreadPool(2);
  Future<?> first=workers.submit(()->{try{return call.invoke(server,McpPrincipal.admin("a"),"goto",body);}catch(Exception e){throw new RuntimeException(e);}});
  Future<?> second=workers.submit(()->{try{return call.invoke(server,McpPrincipal.admin("a"),"goto",body);}catch(Exception e){throw new RuntimeException(e);}});
  System.out.println("concurrent_reply_1="+first.get());System.out.println("concurrent_reply_2="+second.get());
  System.out.println("same_action_invocations="+invokes.get());
  // 命中既有键的第二身份请求是否经过 actuator 权限检查。
  int before=invokes.get();Object hit=call.invoke(server,McpPrincipal.admin("other-principal"),"mine",body);
  System.out.println("cross_principal_different_tool_hit="+hit);System.out.println("actuator_calls_on_hit="+(invokes.get()-before));
  JsonObject sync=new JsonObject();sync.addProperty("companion",id.toString());sync.addProperty("client_action_id","same-transfer");
  before=invokes.get();call.invoke(server,McpPrincipal.admin("a"),"transfer",sync);call.invoke(server,McpPrincipal.admin("a"),"transfer",sync);
  System.out.println("same_sync_transfer_invocations="+(invokes.get()-before));
  workers.shutdownNow();server.stop();
 }
}
