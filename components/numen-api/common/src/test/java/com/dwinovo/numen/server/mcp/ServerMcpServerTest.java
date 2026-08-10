// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.mcp;

import com.dwinovo.numen.entity.ServerOwner;
import com.dwinovo.numen.server.AuthTokens;
import com.dwinovo.numen.server.CompanionRef;
import com.dwinovo.numen.server.Lease;
import com.dwinovo.numen.server.McpPrincipal;
import com.dwinovo.numen.server.SecurityPolicy;
import com.dwinovo.numen.server.ServerNumenActuator;
import com.dwinovo.numen.server.TaskStatus;
import com.dwinovo.numen.server.ToolResult;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Hermetic MCP protocol test: a mock actuator behind a real loopback {@link ServerMcpServer}, driven
 * over HTTP with JSON-RPC 2.0 — no Minecraft, no network beyond loopback, no model API key. Covers
 * the acceptance flow (initialize → tools/list → acquire_control → goto → task_status →
 * release_control) plus auth, error codes, and resource limits.
 */
class ServerMcpServerTest {

    /** A canned actuator: one companion "Bot", trivial leases, a goto that returns a task_id. */
    static class MockActuator implements ServerNumenActuator {
        final UUID bot = UUID.randomUUID();
        volatile String lastRequestedTaskId;
        volatile int invokeCount;

        @Override public CompletableFuture<List<CompanionRef>> listCompanions(McpPrincipal p) {
            return CompletableFuture.completedFuture(List.of(new CompanionRef(bot, "Bot", ServerOwner.ID, true)));
        }
        @Override public CompletableFuture<CompanionRef> createCompanion(McpPrincipal p, String name) {
            return CompletableFuture.completedFuture(new CompanionRef(UUID.randomUUID(), name, ServerOwner.ID, true));
        }
        @Override public CompletableFuture<Boolean> deleteCompanion(McpPrincipal p, UUID id) {
            return CompletableFuture.completedFuture(true);
        }
        @Override public CompletableFuture<Lease> acquireControl(McpPrincipal p, UUID id) {
            return CompletableFuture.completedFuture(new Lease("lease-xyz", id, p.id(), 7, 0, Long.MAX_VALUE));
        }
        @Override public CompletableFuture<Void> releaseControl(McpPrincipal p, UUID id, String leaseId) {
            return CompletableFuture.completedFuture(null);
        }
        @Override public CompletableFuture<ToolResult> invoke(McpPrincipal p, UUID id, String leaseId, String tool, JsonObject args) {
            invokeCount++;
            if ("goto".equals(tool)) {
                return CompletableFuture.completedFuture(ToolResult.of(
                        "{\"success\":true,\"data\":{\"task_id\":\"t1\",\"task\":\"goto\",\"async\":true}}"));
            }
            return CompletableFuture.completedFuture(ToolResult.of("{\"success\":true,\"message\":\"ok\"}"));
        }
        @Override public CompletableFuture<TaskStatus> getTaskStatus(McpPrincipal p, UUID id, String taskId) {
            lastRequestedTaskId = taskId;
            if ("t1".equals(taskId)) {
                return CompletableFuture.completedFuture(new TaskStatus("t1", "done", "goto: arrived",
                        "{\"success\":true,\"message\":\"arrived\"}"));
            }
            return CompletableFuture.completedFuture(new TaskStatus("t1", "running", "goto 0/1"));
        }
        @Override public CompletableFuture<Void> stopTask(McpPrincipal p, UUID id, String taskId) {
            return CompletableFuture.completedFuture(null);
        }
    }

    private static final String TOKEN = "secret-token";
    private ServerMcpServer server;
    private MockActuator mockActuator;
    private String url;
    private final HttpClient http = HttpClient.newHttpClient();

    @BeforeEach
    void setUp() throws Exception {
        McpNetConfig cfg = new McpNetConfig(true, "127.0.0.1", 0, 64 * 1024, 16, 32, 4, 10, 1000);
        AuthTokens tokens = new AuthTokens(Map.of(TOKEN, McpPrincipal.admin("mcp-admin")));
        mockActuator = new MockActuator();
        server = new ServerMcpServer(cfg, mockActuator, tokens, SecurityPolicy.defaults());
        server.start();
        url = "http://127.0.0.1:" + server.boundPort() + "/mcp";
    }

    @AfterEach
    void tearDown() {
        server.stop();
    }

    private HttpResponse<String> post(String body, String token) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8));
        if (token != null) b.header("Authorization", "Bearer " + token);
        return http.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private JsonObject rpc(String method, String params) throws Exception {
        String body = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"" + method + "\""
                + (params == null ? "" : ",\"params\":" + params) + "}";
        HttpResponse<String> r = post(body, TOKEN);
        assertEquals(200, r.statusCode(), "body=" + r.body());
        return JsonParser.parseString(r.body()).getAsJsonObject();
    }

    @Test
    void fullAcceptanceFlow() throws Exception {
        // initialize
        JsonObject init = rpc("initialize", "{\"protocolVersion\":\"2025-06-18\"}");
        assertEquals("numen-server-mcp", init.getAsJsonObject("result").getAsJsonObject("serverInfo").get("name").getAsString());

        // notifications/initialized (as a notification: no id -> 202, no body)
        HttpResponse<String> note = post("{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}", TOKEN);
        assertEquals(202, note.statusCode());

        // tools/list contains the management tools
        JsonObject list = rpc("tools/list", null);
        JsonArray listedTools = list.getAsJsonObject("result").getAsJsonArray("tools");
        String toolsJson = listedTools.toString();
        assertTrue(toolsJson.contains("acquire_control"), toolsJson);
        assertTrue(toolsJson.contains("list_companions"));
        assertTrue(toolsJson.contains("task_status"));

        // 管理工具不能再被 ToolRegistry 中的同名引擎工具重复暴露，否则客户端可能选到旧 Schema。
        int taskStatusCount = 0;
        int taskStopCount = 0;
        JsonObject taskStatusTool = null;
        for (var element : listedTools) {
            JsonObject listed = element.getAsJsonObject();
            String listedName = listed.get("name").getAsString();
            if ("task_status".equals(listedName)) {
                taskStatusCount++;
                taskStatusTool = listed;
            }
            if ("task_stop".equals(listedName)) taskStopCount++;
        }
        assertEquals(1, taskStatusCount, toolsJson);
        assertEquals(1, taskStopCount, toolsJson);
        assertTrue(taskStatusTool.getAsJsonObject("inputSchema").getAsJsonObject("properties").has("task_id"));

        // acquire_control -> lease_id
        JsonObject acq = rpc("tools/call", "{\"name\":\"acquire_control\",\"arguments\":{\"companion\":\"Bot\"}}");
        String acqText = acq.getAsJsonObject("result").getAsJsonArray("content").get(0).getAsJsonObject().get("text").getAsString();
        assertTrue(acqText.contains("lease-xyz"), acqText);

        // goto -> task_id
        JsonObject go = rpc("tools/call",
                "{\"name\":\"goto\",\"arguments\":{\"companion\":\"Bot\",\"lease_id\":\"lease-xyz\",\"x\":-319,\"z\":180}}");
        JsonObject goResult = go.getAsJsonObject("result");
        assertFalse(goResult.get("isError").getAsBoolean());
        assertTrue(goResult.getAsJsonArray("content").get(0).getAsJsonObject().get("text").getAsString().contains("t1"));

        // task_status without id -> running
        JsonObject st = rpc("tools/call", "{\"name\":\"task_status\",\"arguments\":{\"companion\":\"Bot\"}}");
        assertTrue(st.getAsJsonObject("result").getAsJsonArray("content").get(0).getAsJsonObject()
                .get("text").getAsString().contains("running"));

        // task_status with id -> retained terminal result; task_id must reach the actuator.
        JsonObject done = rpc("tools/call",
                "{\"name\":\"task_status\",\"arguments\":{\"companion\":\"Bot\",\"task_id\":\"t1\"}}");
        String doneText = done.getAsJsonObject("result").getAsJsonArray("content").get(0)
                .getAsJsonObject().get("text").getAsString();
        assertTrue(doneText.contains("\"state\":\"done\""), doneText);
        assertTrue(doneText.contains("\"result\":{\"success\":true"), doneText);
        assertEquals("t1", mockActuator.lastRequestedTaskId);

        // release_control
        JsonObject rel = rpc("tools/call",
                "{\"name\":\"release_control\",\"arguments\":{\"companion\":\"Bot\",\"lease_id\":\"lease-xyz\"}}");
        assertFalse(rel.getAsJsonObject("result").get("isError").getAsBoolean());
    }

    @Test
    void rejectsMissingAndBadBearer() throws Exception {
        String body = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}";
        assertEquals(401, post(body, null).statusCode(), "no token");
        assertEquals(401, post(body, "wrong").statusCode(), "bad token");
        assertEquals(200, post(body, TOKEN).statusCode(), "good token");
    }

    @Test
    void rejectsAmbiguousCompanionName() throws Exception {
        ServerMcpServer ambiguousServer = new ServerMcpServer(
                new McpNetConfig(true, "127.0.0.1", 0, 64 * 1024, 16, 32, 4, 10, 1000),
                new MockActuator() {
                    @Override public CompletableFuture<List<CompanionRef>> listCompanions(McpPrincipal p) {
                        return CompletableFuture.completedFuture(List.of(
                                new CompanionRef(UUID.randomUUID(), "Bot", ServerOwner.ID, true),
                                new CompanionRef(UUID.randomUUID(), "Bot", ServerOwner.ID, true)));
                    }
                },
                new AuthTokens(Map.of(TOKEN, McpPrincipal.admin("mcp-admin"))),
                SecurityPolicy.defaults());
        ambiguousServer.start();
        try {
            String u = "http://127.0.0.1:" + ambiguousServer.boundPort() + "/mcp";
            HttpRequest req = HttpRequest.newBuilder(URI.create(u))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + TOKEN)
                    .POST(HttpRequest.BodyPublishers.ofString(
                            "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":"
                                    + "{\"name\":\"acquire_control\",\"arguments\":{\"companion\":\"Bot\"}}}"))
                    .build();
            JsonObject r = JsonParser.parseString(
                    http.send(req, HttpResponse.BodyHandlers.ofString()).body()).getAsJsonObject();
            assertTrue(r.getAsJsonObject("result").get("isError").getAsBoolean());
            String text = r.getAsJsonObject("result").getAsJsonArray("content").get(0)
                    .getAsJsonObject().get("text").getAsString();
            assertTrue(text.contains("no such companion"), text);
        } finally {
            ambiguousServer.stop();
        }
    }

    @Test
    void sameClientActionIdDispatchesOnlyOnce() throws Exception {
        ServerMcpServer idempotentServer = new ServerMcpServer(
                new McpNetConfig(true, "127.0.0.1", 0, 64 * 1024, 16, 32, 4, 10, 1000),
                mockActuator,
                new AuthTokens(Map.of(TOKEN, McpPrincipal.admin("mcp-admin"))),
                SecurityPolicy.defaults());
        idempotentServer.start();
        try {
            String u = "http://127.0.0.1:" + idempotentServer.boundPort() + "/mcp";
            String call = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":"
                    + "{\"name\":\"goto\",\"arguments\":{\"companion\":\"Bot\",\"lease_id\":\"L\","
                    + "\"x\":1,\"z\":2,\"client_action_id\":\"act-1\"}}}";
            HttpRequest req = HttpRequest.newBuilder(URI.create(u))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + TOKEN)
                    .POST(HttpRequest.BodyPublishers.ofString(call))
                    .build();

            String first = http.send(req, HttpResponse.BodyHandlers.ofString()).body();
            String second = http.send(req, HttpResponse.BodyHandlers.ofString()).body();

            JsonObject firstResult = JsonParser.parseString(first).getAsJsonObject().getAsJsonObject("result");
            JsonObject secondResult = JsonParser.parseString(second).getAsJsonObject().getAsJsonObject("result");
            assertEquals(1, mockActuator.invokeCount, "同一 client_action_id 只能派发一次");
            assertTrue(firstResult.getAsJsonArray("content").get(0).getAsJsonObject()
                    .get("text").getAsString().contains("t1"));
            assertTrue(secondResult.getAsJsonArray("content").get(0).getAsJsonObject()
                    .get("text").getAsString().contains("t1"));
        } finally {
            idempotentServer.stop();
        }
    }

    @Test
    void rejectsQueryStringToken() throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url + "?token=" + TOKEN))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}"))
                .build();
        assertEquals(401, http.send(req, HttpResponse.BodyHandlers.ofString()).statusCode(),
                "query-string tokens are never accepted");
    }

    @Test
    void strictProtocolErrors() throws Exception {
        // bad jsonrpc version
        JsonObject bad = JsonParser.parseString(post("{\"jsonrpc\":\"1.0\",\"id\":1,\"method\":\"ping\"}", TOKEN).body()).getAsJsonObject();
        assertEquals(-32600, bad.getAsJsonObject("error").get("code").getAsInt());
        // unknown method
        JsonObject unk = rpc("does/not/exist", null);
        assertEquals(-32601, unk.getAsJsonObject("error").get("code").getAsInt());
        // parse error
        JsonObject parse = JsonParser.parseString(post("not json", TOKEN).body()).getAsJsonObject();
        assertEquals(-32700, parse.getAsJsonObject("error").get("code").getAsInt());
    }

    @Test
    void enforcesBodyAndBatchLimits() throws Exception {
        // oversized body (> 64 KiB)
        String big = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\",\"pad\":\"" + "x".repeat(70 * 1024) + "\"}";
        assertEquals(413, post(big, TOKEN).statusCode());
        // batch too large (> 16)
        StringBuilder batch = new StringBuilder("[");
        for (int i = 0; i < 20; i++) batch.append(i > 0 ? "," : "").append("{\"jsonrpc\":\"2.0\",\"id\":").append(i).append(",\"method\":\"ping\"}");
        batch.append("]");
        JsonObject b = JsonParser.parseString(post(batch.toString(), TOKEN).body()).getAsJsonObject();
        assertEquals(-32600, b.getAsJsonObject("error").get("code").getAsInt());
    }

    @Test
    void refusesWildcardBind() {
        McpNetConfig wild = new McpNetConfig(true, "0.0.0.0", 0, 64 * 1024, 16, 32, 4, 10, 1000);
        ServerMcpServer s = new ServerMcpServer(wild, new MockActuator(),
                new AuthTokens(Map.of("t", McpPrincipal.admin("a"))), SecurityPolicy.defaults());
        try {
            s.start();
            org.junit.jupiter.api.Assertions.fail("should refuse wildcard bind");
        } catch (Exception expected) {
            assertTrue(expected.getMessage().contains("wildcard"), expected.getMessage());
        }
    }

    @Test
    void refusesNonLoopbackWithoutTokens() {
        McpNetConfig lan = new McpNetConfig(true, "10.0.0.5", 0, 64 * 1024, 16, 32, 4, 10, 1000);
        ServerMcpServer s = new ServerMcpServer(lan, new MockActuator(), AuthTokens.empty(), SecurityPolicy.defaults());
        try {
            s.start();
            org.junit.jupiter.api.Assertions.fail("should refuse non-loopback without tokens");
        } catch (Exception expected) {
            assertTrue(expected.getMessage().contains("non-loopback"), expected.getMessage());
        }
    }
}
