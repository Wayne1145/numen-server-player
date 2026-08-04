// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.dwinovo.numen.Constants;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * The server control layer's config: the {@link SecurityPolicy} and the {@link AuthTokens}
 * (token → principal). Loaded from {@code config/numen/server_control.json}; if the file is absent
 * or malformed, safe defaults are used (restrictive policy, no tokens → the MCP surface stays shut).
 * The token→principal map is decided <b>here</b>, on the server — never from a request body.
 */
public final class ServerControlConfig {

    public final SecurityPolicy policy;
    public final AuthTokens tokens;
    public final com.dwinovo.numen.server.mcp.McpNetConfig mcp;
    public final com.dwinovo.numen.server.agent.ApiAgentConfig apiAgent;

    public ServerControlConfig(SecurityPolicy policy, AuthTokens tokens,
                               com.dwinovo.numen.server.mcp.McpNetConfig mcp,
                               com.dwinovo.numen.server.agent.ApiAgentConfig apiAgent) {
        this.policy = policy;
        this.tokens = tokens;
        this.mcp = mcp;
        this.apiAgent = apiAgent;
    }

    public static ServerControlConfig defaults() {
        return new ServerControlConfig(SecurityPolicy.defaults(), AuthTokens.empty(),
                com.dwinovo.numen.server.mcp.McpNetConfig.disabledDefault(),
                com.dwinovo.numen.server.agent.ApiAgentConfig.disabledDefault());
    }

    public static ServerControlConfig load(Path file) {
        try {
            if (file == null || !Files.isRegularFile(file)) {
                Constants.LOG.info("[numen-control] no {} — using restrictive defaults", file);
                return defaults();
            }
            return fromJson(Files.readString(file, StandardCharsets.UTF_8));
        } catch (Exception ex) {
            Constants.LOG.error("[numen-control] failed to read {} — using defaults: {}", file, ex.toString());
            return defaults();
        }
    }

    public static ServerControlConfig fromJson(String json) {
        JsonObject root = JsonParser.parseString(json).getAsJsonObject();
        return new ServerControlConfig(parsePolicy(root.getAsJsonObject("policy")),
                parseTokens(root.get("principals")),
                parseMcp(root.has("mcp") && root.get("mcp").isJsonObject() ? root.getAsJsonObject("mcp") : null),
                parseApiAgent(root.has("api_agent") && root.get("api_agent").isJsonObject()
                        ? root.getAsJsonObject("api_agent") : null));
    }

    private static com.dwinovo.numen.server.agent.ApiAgentConfig parseApiAgent(JsonObject a) {
        com.dwinovo.numen.server.agent.ApiAgentConfig d = com.dwinovo.numen.server.agent.ApiAgentConfig.disabledDefault();
        if (a == null) return d;
        return new com.dwinovo.numen.server.agent.ApiAgentConfig(
                a.has("enabled") && a.get("enabled").getAsBoolean(),
                a.has("provider") ? a.get("provider").getAsString() : d.provider(),
                a.has("model") ? a.get("model").getAsString() : d.model(),
                a.has("base_url") ? a.get("base_url").getAsString() : d.baseUrl(),
                a.has("reasoning_effort") ? a.get("reasoning_effort").getAsString() : d.reasoningEffort(),
                a.has("api_key_env") ? a.get("api_key_env").getAsString() : d.apiKeyEnv(),
                a.has("api_key_file") ? a.get("api_key_file").getAsString() : d.apiKeyFile(),
                a.has("max_turns") ? a.get("max_turns").getAsInt() : d.maxTurns(),
                a.has("request_timeout_seconds") ? a.get("request_timeout_seconds").getAsInt() : d.requestTimeoutSeconds());
    }

    private static com.dwinovo.numen.server.mcp.McpNetConfig parseMcp(JsonObject m) {
        com.dwinovo.numen.server.mcp.McpNetConfig d = com.dwinovo.numen.server.mcp.McpNetConfig.disabledDefault();
        if (m == null) return d;
        boolean enabled = m.has("enabled") && m.get("enabled").getAsBoolean();
        String host = m.has("host") ? m.get("host").getAsString() : d.host();
        int port = m.has("port") ? m.get("port").getAsInt() : d.port();
        int body = m.has("max_body_bytes") ? m.get("max_body_bytes").getAsInt() : d.maxBodyBytes();
        int batch = m.has("max_batch") ? m.get("max_batch").getAsInt() : d.maxBatch();
        int depth = m.has("max_depth") ? m.get("max_depth").getAsInt() : d.maxDepth();
        int workers = m.has("worker_threads") ? m.get("worker_threads").getAsInt() : d.workerThreads();
        int reqTo = m.has("request_timeout_seconds") ? m.get("request_timeout_seconds").getAsInt() : d.requestTimeoutSeconds();
        int rate = m.has("max_requests_per_minute_per_principal") ? m.get("max_requests_per_minute_per_principal").getAsInt() : d.maxRequestsPerMinutePerPrincipal();
        return new com.dwinovo.numen.server.mcp.McpNetConfig(enabled, host, port, body, batch, depth, workers, reqTo, rate);
    }

    /** Generate a high-entropy 256-bit bearer token (URL-safe base64). */
    public static String generateToken() {
        byte[] b = new byte[32];
        new java.security.SecureRandom().nextBytes(b);
        return java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(b);
    }

    /**
     * If MCP is enabled but no tokens are configured, mint an admin token (256-bit) so the endpoint is
     * usable; log it once. Returns a config carrying the new token. (For a non-loopback bind the caller
     * should require an explicit token instead — {@link ServerMcpServer#start} refuses that case.)
     */
    public ServerControlConfig withGeneratedAdminTokenIfNeeded() {
        if (!mcp.enabled() || !tokens.isEmpty()) return this;
        String token = generateToken();
        Constants.LOG.warn("[numen-control] MCP enabled with no tokens — generated an ADMIN bearer token "
                + "for this session (add it to config/numen/server_control.json to persist):\n    {}", token);
        AuthTokens gen = new AuthTokens(java.util.Map.of(token, McpPrincipal.admin("mcp-admin")));
        return new ServerControlConfig(policy, gen, mcp, apiAgent);
    }

    private static SecurityPolicy parsePolicy(JsonObject p) {
        SecurityPolicy d = SecurityPolicy.defaults();
        if (p == null) return d;
        Set<ToolCapability> caps = EnumSet.noneOf(ToolCapability.class);
        if (p.has("enabled_capabilities") && p.get("enabled_capabilities").isJsonArray()) {
            for (JsonElement e : p.getAsJsonArray("enabled_capabilities")) {
                try {
                    caps.add(ToolCapability.valueOf(e.getAsString()));
                } catch (IllegalArgumentException ignored) {
                    Constants.LOG.warn("[numen-control] unknown capability in config: {}", e);
                }
            }
        } else {
            caps = EnumSet.of(ToolCapability.SERVER_READ_ONLY, ToolCapability.SERVER_BODY_ACTION);
        }
        Set<String> dims = strSet(p, "allowed_dimensions", Set.of("minecraft:overworld"));
        Set<String> ns = strSet(p, "allowed_namespaces", Set.of("minecraft"));
        double dist = p.has("max_action_distance") ? p.get("max_action_distance").getAsDouble() : d.maxActionDistance();
        long ttl = p.has("lease_ttl_ms") ? p.get("lease_ttl_ms").getAsLong() : d.leaseTtlMs();
        int gtasks = p.has("global_max_concurrent_tasks") ? p.get("global_max_concurrent_tasks").getAsInt() : d.globalMaxConcurrentTasks();
        int rp = p.has("max_calls_per_minute_per_principal") ? p.get("max_calls_per_minute_per_principal").getAsInt() : d.maxCallsPerMinutePerPrincipal();
        int rc = p.has("max_calls_per_minute_per_companion") ? p.get("max_calls_per_minute_per_companion").getAsInt() : d.maxCallsPerMinutePerCompanion();
        return new SecurityPolicy(caps, dims, ns, dist, ttl, gtasks, rp, rc);
    }

    private static AuthTokens parseTokens(JsonElement principals) {
        Map<String, McpPrincipal> byToken = new HashMap<>();
        if (principals != null && principals.isJsonArray()) {
            for (JsonElement e : (JsonArray) principals) {
                JsonObject o = e.getAsJsonObject();
                String token = o.has("token") ? o.get("token").getAsString() : null;
                String id = o.has("id") ? o.get("id").getAsString() : null;
                if (token == null || token.isBlank() || id == null || id.isBlank()) continue;
                McpPrincipal.Role role = McpPrincipal.Role.valueOf(
                        o.has("role") ? o.get("role").getAsString().toUpperCase() : "READONLY");
                UUID owner = o.has("owner_uuid") && !o.get("owner_uuid").isJsonNull()
                        ? UUID.fromString(o.get("owner_uuid").getAsString())
                        : com.dwinovo.numen.entity.ServerOwner.ID;
                byToken.put(token, new McpPrincipal(id, role, owner));
            }
        }
        return new AuthTokens(byToken);
    }

    private static Set<String> strSet(JsonObject p, String key, Set<String> fallback) {
        if (!p.has(key) || !p.get(key).isJsonArray()) return fallback;
        Set<String> out = new LinkedHashSet<>();
        for (JsonElement e : p.getAsJsonArray(key)) out.add(e.getAsString());
        return out.isEmpty() ? fallback : out;
    }
}
