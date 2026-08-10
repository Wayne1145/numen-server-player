// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.mcp;

import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.agent.tool.ToolRegistry;
import com.dwinovo.numen.server.AuthTokens;
import com.dwinovo.numen.server.CompanionRef;
import com.dwinovo.numen.server.Lease;
import com.dwinovo.numen.server.McpPrincipal;
import com.dwinovo.numen.server.RateLimiter;
import com.dwinovo.numen.server.SecurityPolicy;
import com.dwinovo.numen.server.ServerNumenActuator;
import com.dwinovo.numen.server.TaskStatus;
import com.dwinovo.numen.server.ToolCapability;
import com.dwinovo.numen.server.ToolCatalog;
import com.dwinovo.numen.server.ToolResult;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * The server-side MCP (Model Context Protocol) server: hand-rolled JSON-RPC 2.0 over the JDK's HTTP
 * server, routing every call to {@link ServerNumenActuator}. This is the pure-server replacement for
 * the upstream client-side MCP server — no {@code Minecraft.getInstance()}, no client
 * {@code NumenActuator}. An external agent (Claude Desktop via {@code mcp-remote}, Hermes, or the
 * bundled {@link StdioMcpAdapter}) drives companions with <b>no model API key involved</b>.
 *
 * <p>Security: Bearer-only (no query-string token, constant-time compare via {@link AuthTokens});
 * loopback bind by default; refuses to bind a wildcard host, and refuses a non-loopback bind with no
 * tokens. Bounded body (64 KiB), batch (16), nesting depth, worker pool + queue, and a per-principal
 * request rate limit. Requests return quickly (the actuator hops to the main thread and returns a
 * task_id for long work) — a driver polls {@code task_status}; the request timeout is separate from
 * any task's own deadline.
 */
public final class ServerMcpServer {

    private static final Logger LOG = LoggerFactory.getLogger("numen-mcp");
    private static final String PROTOCOL_VERSION = "2025-06-18";
    private static final Set<String> MANAGEMENT_TOOL_NAMES = Set.of(
            "list_companions", "create_companion", "delete_companion",
            "acquire_control", "release_control", "task_status", "task_stop");

    private static final String INSTRUCTIONS = """
            Numen companions are player-like characters in a live Minecraft server. Through this MCP \
            server you take control of a companion's body and play as it — perceive, move, mine, build, \
            craft, fight. Loop: list_companions (create_companion / delete_companion to manage); \
            acquire_control <companion> to take the single write lease (returns lease_id); then call \
            action tools with {companion, lease_id, …}; action tools return a task_id — poll task_status \
            until idle, then perceive to confirm; release_control when done. Read tools (get_self_status, \
            scan_blocks, …) need no lease and run concurrently.""";

    private final McpNetConfig cfg;
    private final ServerNumenActuator actuator;
    private final AuthTokens tokens;
    private volatile SecurityPolicy policy;
    private final Gson gson = new Gson();
    private final RateLimiter reqLimiter;
    private HttpServer http;
    private ThreadPoolExecutor pool;

    public ServerMcpServer(McpNetConfig cfg, ServerNumenActuator actuator, AuthTokens tokens, SecurityPolicy policy) {
        this.cfg = cfg;
        this.actuator = actuator;
        this.tokens = tokens;
        this.policy = policy;
        this.reqLimiter = new RateLimiter(cfg.maxRequestsPerMinutePerPrincipal(), 60_000);
    }

    public void setPolicy(SecurityPolicy policy) { this.policy = policy; }

    public int boundPort() { return http == null ? -1 : http.getAddress().getPort(); }

    public void start() throws IOException {
        if (cfg.isWildcard()) {
            throw new IllegalStateException("refusing to bind wildcard host '" + cfg.host()
                    + "' — bind loopback and use an SSH tunnel / WireGuard / Tailscale");
        }
        if (!cfg.isLoopback() && tokens.isEmpty()) {
            throw new IllegalStateException("refusing a non-loopback bind (" + cfg.host() + ") with no tokens configured");
        }
        pool = new ThreadPoolExecutor(cfg.workerThreads(), cfg.workerThreads(), 0L, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(64), r -> {
            Thread t = new Thread(r, "numen-mcp-http");
            t.setDaemon(true);
            return t;
        }, new ThreadPoolExecutor.CallerRunsPolicy());
        http = HttpServer.create(new InetSocketAddress(cfg.host(), cfg.port()), 0);
        http.createContext("/mcp", this::handle);
        http.setExecutor(pool);
        http.start();
        LOG.info("[numen-mcp] server up on http://{}:{}/mcp (loopback={}, tokens={})",
                cfg.host(), boundPort(), cfg.isLoopback(), tokens.isEmpty() ? "none" : "set");
    }

    public void stop() {
        if (http != null) http.stop(0);
        if (pool != null) pool.shutdownNow();
    }

    // ---- HTTP + auth ----

    private void handle(HttpExchange ex) {
        try {
            if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) { respond(ex, 405, ""); return; }
            McpPrincipal principal = authenticate(ex);
            if (principal == null) { respond(ex, 401, "Unauthorized"); return; }
            if (!reqLimiter.tryAcquire(principal.id(), System.currentTimeMillis())) { respond(ex, 429, "Too Many Requests"); return; }

            byte[] body = readCapped(ex.getRequestBody(), cfg.maxBodyBytes());
            if (body == null) { respond(ex, 413, "Payload Too Large"); return; }

            JsonElement parsed;
            try {
                parsed = JsonParser.parseString(new String(body, StandardCharsets.UTF_8));
            } catch (RuntimeException e) {
                respondJson(ex, error(null, -32700, "parse error")); return;
            }
            if (depth(parsed, 0) > cfg.maxDepth()) { respondJson(ex, error(null, -32600, "request too deeply nested")); return; }

            if (parsed.isJsonArray()) {
                JsonArray arr = parsed.getAsJsonArray();
                if (arr.isEmpty() || arr.size() > cfg.maxBatch()) { respondJson(ex, error(null, -32600, "invalid batch size")); return; }
                JsonArray out = new JsonArray();
                for (JsonElement el : arr) {
                    if (!el.isJsonObject()) { out.add(error(null, -32600, "invalid batch element")); continue; }
                    JsonObject r = dispatch(el.getAsJsonObject(), principal);
                    if (r != null) out.add(r);
                }
                if (out.isEmpty()) respond(ex, 202, ""); else respondJson(ex, out);
            } else if (parsed.isJsonObject()) {
                JsonObject r = dispatch(parsed.getAsJsonObject(), principal);
                if (r == null) respond(ex, 202, ""); else respondJson(ex, r);
            } else {
                respondJson(ex, error(null, -32600, "invalid request"));
            }
        } catch (Exception fatal) {
            LOG.warn("[numen-mcp] request failed: {}", fatal.toString());
            try { respond(ex, 500, ""); } catch (IOException ignored) {}
        } finally {
            ex.close();
        }
    }

    /** Bearer-only, constant-time. Query-string tokens are rejected outright. */
    private McpPrincipal authenticate(HttpExchange ex) {
        String q = ex.getRequestURI().getRawQuery();
        if (q != null && q.contains("token=")) return null;   // no query-string tokens, ever
        String auth = ex.getRequestHeaders().getFirst("Authorization");
        if (auth == null || !auth.startsWith("Bearer ")) return null;
        return tokens.resolve(auth.substring("Bearer ".length()).trim());
    }

    private static byte[] readCapped(InputStream in, int max) throws IOException {
        byte[] buf = new byte[Math.min(max, 8192)];
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        int n, total = 0;
        while ((n = in.read(buf)) != -1) {
            total += n;
            if (total > max) return null;
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }

    private static int depth(JsonElement e, int d) {
        if (d > 64) return d;
        if (e.isJsonObject()) {
            int m = d;
            for (var en : e.getAsJsonObject().entrySet()) m = Math.max(m, depth(en.getValue(), d + 1));
            return m;
        }
        if (e.isJsonArray()) {
            int m = d;
            for (JsonElement el : e.getAsJsonArray()) m = Math.max(m, depth(el, d + 1));
            return m;
        }
        return d;
    }

    // ---- JSON-RPC dispatch ----

    private JsonObject dispatch(JsonObject req, McpPrincipal principal) {
        JsonElement id = req.get("id");
        boolean notification = id == null || id.isJsonNull();
        if (!req.has("jsonrpc") || !"2.0".equals(asString(req.get("jsonrpc")))) {
            return notification ? null : error(id, -32600, "invalid request: jsonrpc must be \"2.0\"");
        }
        String method = req.has("method") ? asString(req.get("method")) : null;
        if (method == null) return notification ? null : error(id, -32600, "invalid request: missing method");
        if (notification) return null;   // notifications (e.g. notifications/initialized) expect no reply
        if (req.has("params") && !req.get("params").isJsonObject()) {
            return error(id, -32602, "invalid params: must be an object");
        }
        JsonObject params = req.has("params") ? req.getAsJsonObject("params") : new JsonObject();
        try {
            return switch (method) {
                case "initialize" -> ok(id, initializeResult(params));
                case "notifications/initialized", "ping" -> ok(id, new JsonObject());
                case "tools/list" -> ok(id, toolsList());
                case "tools/call" -> ok(id, toolsCall(params, principal));
                default -> error(id, -32601, "method not found: " + method);
            };
        } catch (InvalidParams ip) {
            return error(id, -32602, ip.getMessage());
        } catch (RuntimeException ex) {
            return error(id, -32603, "internal error: " + ex.getMessage());
        }
    }

    private JsonObject initializeResult(JsonObject params) {
        String proto = params.has("protocolVersion") ? params.get("protocolVersion").getAsString() : PROTOCOL_VERSION;
        JsonObject r = new JsonObject();
        r.addProperty("protocolVersion", proto);
        JsonObject caps = new JsonObject();
        caps.add("tools", new JsonObject());
        r.add("capabilities", caps);
        JsonObject info = new JsonObject();
        info.addProperty("name", "numen-server-mcp");
        info.addProperty("version", "0.1.0");
        r.add("serverInfo", info);
        r.addProperty("instructions", INSTRUCTIONS);
        return r;
    }

    // ---- tools/list ----

    private JsonObject toolsList() {
        JsonArray tools = new JsonArray();
        tools.add(tool("list_companions", "List companions you may control.", objSchema(false, false)));
        tools.add(tool("create_companion", "Summon a new server companion by name (3-16 [A-Za-z0-9_]).",
                stringSchema("name", "New companion name.")));
        tools.add(tool("delete_companion", "Permanently dismiss a companion.", objSchema(true, false)));
        tools.add(tool("acquire_control", "Take the single write lease over a companion. Returns lease_id.",
                objSchema(true, false)));
        tools.add(tool("release_control", "Release a previously-acquired lease.", objSchema(true, true)));
        tools.add(tool("task_status", "Read the companion's active task, or query a retained terminal result by task_id.",
                taskStatusSchema()));
        tools.add(tool("task_stop", "Abort the companion's background task.", objSchema(true, true)));
        // Engine tools, filtered by capability policy (whitelist only; destructive only if enabled).
        for (NumenTool t : ToolRegistry.all()) {
            // 管理工具由上方 MCP 层提供；同名引擎工具会制造重复 Schema，并让客户端随机选中旧契约。
            if (MANAGEMENT_TOOL_NAMES.contains(t.name())) continue;
            ToolCapability cap = ToolCatalog.of(t.name());
            if (!cap.isServerExposable() || !policy.capabilityEnabled(cap)) continue;
            tools.add(tool(t.name(), t.description(), withCompanionAndLease(t.parameterSchema(), cap.requiresControl())));
        }
        JsonObject r = new JsonObject();
        r.add("tools", tools);
        return r;
    }

    // ---- tools/call ----

    private JsonObject toolsCall(JsonObject params, McpPrincipal principal) {
        String name = asString(params.get("name"));
        if (name == null) throw new InvalidParams("missing tool name");
        JsonObject args = params.has("arguments") && params.get("arguments").isJsonObject()
                ? params.getAsJsonObject("arguments") : new JsonObject();
        return switch (name) {
            case "list_companions" -> content(listCompanionsText(principal), false);
            case "create_companion" -> {
                String n = asString(args.get("name"));
                CompanionRef ref = await(actuator.createCompanion(principal, n == null ? "" : n));
                JsonObject o = new JsonObject();
                o.addProperty("companion", ref.id().toString());
                o.addProperty("name", ref.name());
                yield content(o.toString(), false);
            }
            case "delete_companion" -> {
                UUID id = resolveCompanion(principal, args);
                if (id == null) yield content("no such companion", true);
                boolean ok = await(actuator.deleteCompanion(principal, id));
                yield content(ok ? "deleted " + id : "could not delete " + id, !ok);
            }
            case "acquire_control" -> {
                UUID id = resolveCompanion(principal, args);
                if (id == null) yield content("no such companion", true);
                Lease l = await(actuator.acquireControl(principal, id));
                JsonObject o = new JsonObject();
                o.addProperty("lease_id", l.leaseId());
                o.addProperty("fencing_token", l.fencingToken());
                o.addProperty("companion", id.toString());
                yield content(o.toString(), false);
            }
            case "release_control" -> {
                UUID id = resolveCompanion(principal, args);
                if (id == null) yield content("no such companion", true);
                await(actuator.releaseControl(principal, id, asString(args.get("lease_id"))));
                yield content("released", false);
            }
            case "task_status" -> {
                UUID id = resolveCompanion(principal, args);
                if (id == null) yield content("no such companion", true);
                String requestedTaskId = asString(args.get("task_id"));
                TaskStatus st = await(actuator.getTaskStatus(principal, id, requestedTaskId));
                JsonObject o = new JsonObject();
                o.addProperty("state", st.state());
                o.addProperty("task_id", st.taskId());
                o.addProperty("detail", st.detail());
                if (st.resultJson() != null) {
                    o.add("result", JsonParser.parseString(st.resultJson()));
                } else {
                    o.add("result", com.google.gson.JsonNull.INSTANCE);
                }
                yield content(o.toString(),
                        "error".equals(st.state()));
            }
            case "task_stop" -> {
                UUID id = resolveCompanion(principal, args);
                if (id == null) yield content("no such companion", true);
                await(actuator.stopTask(principal, id, asString(args.get("task_id"))));
                yield content("stopped", false);
            }
            default -> invokeTool(principal, name, args);
        };
    }

    private JsonObject invokeTool(McpPrincipal principal, String tool, JsonObject args) {
        UUID id = resolveCompanion(principal, args);
        if (id == null) return content("this tool needs a 'companion' (name or id from list_companions)", true);
        String lease = asString(args.get("lease_id"));
        JsonObject toolArgs = args.deepCopy();
        toolArgs.remove("companion");
        toolArgs.remove("lease_id");
        ToolResult r = await(actuator.invoke(principal, id, lease, tool, toolArgs));
        return content(r.json(), !r.ok());
    }

    private String listCompanionsText(McpPrincipal principal) {
        List<CompanionRef> list = await(actuator.listCompanions(principal));
        if (list.isEmpty()) return "no companions";
        StringBuilder sb = new StringBuilder();
        for (CompanionRef c : list) {
            sb.append(c.name()).append("  id=").append(c.id()).append(c.alive() ? "  [live]" : "  [dormant]").append('\n');
        }
        return sb.toString().stripTrailing();
    }

    private UUID resolveCompanion(McpPrincipal principal, JsonObject args) {
        String raw = asString(args.get("companion"));
        if (raw == null || raw.isBlank()) return null;
        try {
            return UUID.fromString(raw.trim());
        } catch (IllegalArgumentException ignored) { /* name lookup below */ }
        List<CompanionRef> all = await(actuator.listCompanions(principal));
        // 精确匹配必须唯一；否则拒绝(名字歧义时宁可失败，不静默选第一个)。
        CompanionRef exact = null;
        for (CompanionRef c : all) {
            if (c.name().equals(raw)) {
                if (exact != null) return null;
                exact = c;
            }
        }
        if (exact != null) return exact.id();
        CompanionRef folded = null;
        for (CompanionRef c : all) {
            if (c.name().equalsIgnoreCase(raw)) {
                if (folded != null) return null;
                folded = c;
            }
        }
        return folded == null ? null : folded.id();
    }

    private <T> T await(CompletableFuture<T> f) {
        try {
            return f.get(cfg.requestTimeoutSeconds(), TimeUnit.SECONDS);
        } catch (ExecutionException ee) {
            Throwable c = ee.getCause() != null ? ee.getCause() : ee;
            throw new RuntimeException(c.getMessage() != null ? c.getMessage() : c.toString());
        } catch (TimeoutException te) {
            throw new RuntimeException("request timed out");
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted");
        }
    }

    // ---- schema + envelope helpers ----

    private JsonObject tool(String name, String description, JsonObject schema) {
        JsonObject t = new JsonObject();
        t.addProperty("name", name);
        t.addProperty("description", description);
        t.add("inputSchema", schema);
        return t;
    }

    private JsonObject objSchema(boolean companion, boolean lease) {
        JsonObject s = new JsonObject();
        s.addProperty("type", "object");
        JsonObject props = new JsonObject();
        JsonArray required = new JsonArray();
        if (companion) { props.add("companion", strProp("Companion name or id.")); required.add("companion"); }
        if (lease) { props.add("lease_id", strProp("The lease_id from acquire_control.")); }
        s.add("properties", props);
        if (!required.isEmpty()) s.add("required", required);
        return s;
    }

    private JsonObject taskStatusSchema() {
        JsonObject s = objSchema(true, false);
        s.getAsJsonObject("properties").add("task_id",
                strProp("Optional task_id returned by an action. When supplied, terminal done/failed/timeout/stopped result is retained and returned."));
        return s;
    }

    private JsonObject stringSchema(String key, String desc) {
        JsonObject s = new JsonObject();
        s.addProperty("type", "object");
        JsonObject props = new JsonObject();
        props.add(key, strProp(desc));
        s.add("properties", props);
        JsonArray req = new JsonArray();
        req.add(key);
        s.add("required", req);
        return s;
    }

    private JsonObject withCompanionAndLease(java.util.Map<String, Object> schema, boolean needsLease) {
        JsonObject s = schema == null ? new JsonObject() : gson.toJsonTree(schema).getAsJsonObject();
        s.addProperty("type", "object");
        JsonObject props = s.has("properties") && s.get("properties").isJsonObject()
                ? s.getAsJsonObject("properties") : new JsonObject();
        props.add("companion", strProp("Companion name or id."));
        if (needsLease) props.add("lease_id", strProp("The lease_id from acquire_control."));
        s.add("properties", props);
        JsonArray required = s.has("required") && s.get("required").isJsonArray() ? s.getAsJsonArray("required") : new JsonArray();
        required.add("companion");
        s.add("required", required);
        return s;
    }

    private JsonObject strProp(String desc) {
        JsonObject p = new JsonObject();
        p.addProperty("type", "string");
        p.addProperty("description", desc);
        return p;
    }

    private JsonObject content(String text, boolean isError) {
        JsonObject item = new JsonObject();
        item.addProperty("type", "text");
        item.addProperty("text", text);
        JsonArray arr = new JsonArray();
        arr.add(item);
        JsonObject r = new JsonObject();
        r.add("content", arr);
        r.addProperty("isError", isError);
        return r;
    }

    private JsonObject ok(JsonElement id, JsonObject result) {
        JsonObject r = new JsonObject();
        r.addProperty("jsonrpc", "2.0");
        r.add("id", id);
        r.add("result", result);
        return r;
    }

    private JsonObject error(JsonElement id, int code, String message) {
        JsonObject err = new JsonObject();
        err.addProperty("code", code);
        err.addProperty("message", message);
        JsonObject r = new JsonObject();
        r.addProperty("jsonrpc", "2.0");
        r.add("id", id == null ? com.google.gson.JsonNull.INSTANCE : id);
        r.add("error", err);
        return r;
    }

    private static String asString(JsonElement e) {
        return e != null && e.isJsonPrimitive() && e.getAsJsonPrimitive().isString() ? e.getAsString() : null;
    }


    private void respondJson(HttpExchange ex, JsonElement json) throws IOException {
        byte[] bytes = gson.toJson(json).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(200, bytes.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(bytes); }
    }

    private void respond(HttpExchange ex, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        if (bytes.length > 0) { try (OutputStream os = ex.getResponseBody()) { os.write(bytes); } }
    }

    private static final class InvalidParams extends RuntimeException {
        InvalidParams(String m) { super(m); }
    }
}
