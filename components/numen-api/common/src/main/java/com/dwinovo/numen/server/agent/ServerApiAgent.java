// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.agent;

import com.dwinovo.numen.Constants;
import com.dwinovo.numen.agent.llm.ConvoState;
import com.dwinovo.numen.agent.provider.AssistantTurn;
import com.dwinovo.numen.agent.provider.LlmToolCall;
import com.dwinovo.numen.server.Lease;
import com.dwinovo.numen.server.McpPrincipal;
import com.dwinovo.numen.server.ServerNumenActuator;
import com.dwinovo.numen.server.TaskStatus;
import com.dwinovo.numen.server.ToolResult;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiFunction;

/**
 * The OPTIONAL server-side API agent: one LLM-driven conversation loop per companion, running on a
 * worker thread with <b>every world action funnelled through the same {@link ServerNumenActuator}
 * (and thus the same policy / rate-limit / audit) and the same write lease that an MCP driver would
 * hold</b> — so an API agent and an MCP agent can never drive one body at once (S3's single-writer
 * lease enforces it; the loser gets "already controlled").
 *
 * <p>The loop: user text → LLM turn → for each tool call, {@code actuator.invoke}; async actions
 * (task_id) are polled via {@code getTaskStatus} until idle before the result returns to the model;
 * tool results feed back into the conversation; repeat until the model stops calling tools or the
 * turn budget runs out. The LLM transport is injected as a function so the loop core is
 * unit-testable with a scripted model and a mock actuator (no network, no Minecraft).
 */
public final class ServerApiAgent {

    /** LLM transport: (messages, systemPrompt) → assistant turn. Real impl wraps NumenLlmClient. */
    public interface LlmFn extends BiFunction<List<ConvoState.Msg>, String, CompletableFuture<AssistantTurn>> {}

    public enum State { IDLE, RUNNING, STOPPED }

    private static final String SYSTEM_PROMPT = """
            You control a Minecraft companion body on a live server. Perceive with get_self_status /
            scan_blocks before acting; act with goto / collect_items / equip_item / craft / transfer
            and friends. Action tools run in the background — their final state is reported back to
            you before your next turn. Be concise; stop calling tools when the request is done.""";

    private final ServerNumenActuator actuator;
    private final McpPrincipal principal;
    private final UUID companionId;
    private final LlmFn llm;
    private final int maxTurns;
    private final long pollMs;
    private final long invokeTimeoutSeconds;

    private final ConvoState convo = new ConvoState();
    private volatile State state = State.IDLE;
    private volatile String lastSummary = "";
    private volatile Lease lease;

    public ServerApiAgent(ServerNumenActuator actuator, McpPrincipal principal, UUID companionId,
                          LlmFn llm, int maxTurns, long pollMs, long invokeTimeoutSeconds) {
        this.actuator = actuator;
        this.principal = principal;
        this.companionId = companionId;
        this.llm = llm;
        this.maxTurns = Math.max(1, maxTurns);
        this.pollMs = Math.max(50, pollMs);
        this.invokeTimeoutSeconds = invokeTimeoutSeconds;
    }

    public State state() { return state; }
    public String lastSummary() { return lastSummary; }
    public UUID companionId() { return companionId; }

    /**
     * Run one user request to completion (blocking — call on a worker thread, never the server
     * thread). Acquires the write lease up front (fails fast if MCP or anyone else holds it),
     * releases it at the end. Returns the model's final text.
     */
    public String run(String userText) throws Exception {
        state = State.RUNNING;
        try {
            lease = await(actuator.acquireControl(principal, companionId));
            convo.addUser(userText);
            for (int turn = 0; turn < maxTurns; turn++) {
                AssistantTurn at = llm.apply(convo.snapshot(), SYSTEM_PROMPT)
                        .get(invokeTimeoutSeconds, TimeUnit.SECONDS);
                convo.addAssistant(at);
                if (!at.hasToolCalls()) {
                    lastSummary = at.content();
                    return at.content();
                }
                for (LlmToolCall call : at.toolCalls()) {
                    if (state == State.STOPPED) throw new InterruptedException("agent stopped");
                    convo.addToolResult(call.id(), execTool(call));
                }
            }
            lastSummary = "(turn budget reached)";
            return lastSummary;
        } finally {
            if (lease != null) {
                try { await(actuator.releaseControl(principal, companionId, lease.leaseId())); }
                catch (Exception ignored) { /* lease TTL sweeper will reclaim */ }
                lease = null;
            }
            if (state != State.STOPPED) state = State.IDLE;
        }
    }

    /** Ask the loop to stop between tool calls; also aborts the companion's background task. */
    public void stop() {
        state = State.STOPPED;
        try { actuator.stopTask(principal, companionId, null).get(5, TimeUnit.SECONDS); }
        catch (Exception ignored) { /* body may be idle */ }
    }

    /** Run one tool call through the actuator; for async dispatches, poll to terminal then perceive. */
    private String execTool(LlmToolCall call) throws Exception {
        JsonObject args;
        try {
            String raw = call.arguments();
            args = raw == null || raw.isBlank() ? new JsonObject() : JsonParser.parseString(raw).getAsJsonObject();
        } catch (RuntimeException ex) {
            return jsonResult(false, "bad tool arguments: " + ex.getMessage());
        }
        ToolResult r = await(actuator.invoke(principal, companionId, lease.leaseId(), call.name(), args));
        if (r.taskId() == null) return r.json();   // query / immediate result
        // Async action: wait for the body to go idle so the model sees the finished state.
        long deadline = System.currentTimeMillis() + invokeTimeoutSeconds * 1000L;
        while (System.currentTimeMillis() < deadline) {
            if (state == State.STOPPED) return jsonResult(false, "stopped");
            TaskStatus st = await(actuator.getTaskStatus(principal, companionId, r.taskId()));
            if ("running".equals(st.state()) || "queued".equals(st.state())) {
                Thread.sleep(pollMs);
                continue;
            }
            if ("done".equals(st.state()) || "failed".equals(st.state())
                    || "timeout".equals(st.state()) || "stopped".equals(st.state())) {
                if (st.resultJson() != null && !st.resultJson().isBlank()) return st.resultJson();
                return jsonResult("done".equals(st.state()), st.detail());
            }
            if ("idle".equals(st.state())) {
                JsonObject o = new JsonObject();
                o.addProperty("success", false);
                o.addProperty("message", "task " + r.taskId()
                        + " terminal result unavailable; body idle — perceive to verify");
                o.add("dispatch", JsonParser.parseString(r.json()));
                return o.toString();
            }
            if ("error".equals(st.state())) return jsonResult(false, st.detail());
            Thread.sleep(pollMs);
        }
        return jsonResult(false, "task " + r.taskId() + " still running at timeout");
    }

    /** Gson 构造的固定信封：message 永远被正确转义，模型读到的始终是合法 JSON。 */
    private static String jsonResult(boolean success, String message) {
        JsonObject o = new JsonObject();
        o.addProperty("success", success);
        if (message != null) o.addProperty("message", message);
        return o.toString();
    }

    private <T> T await(CompletableFuture<T> f) throws Exception {
        try {
            return f.get(invokeTimeoutSeconds, TimeUnit.SECONDS);
        } catch (java.util.concurrent.ExecutionException ee) {
            Throwable c = ee.getCause() != null ? ee.getCause() : ee;
            throw new Exception(c.getMessage() != null ? c.getMessage() : c.toString());
        }
    }

    /** Log-safe descriptor (never includes any key material). */
    @Override
    public String toString() {
        return "ServerApiAgent{companion=" + companionId + ", state=" + state + "}";
    }

    static { // sanity: this class must never see an API key — transport owns it
        Constants.LOG.debug("[numen-agent] ServerApiAgent loaded");
    }
}
