// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.agent;

import com.dwinovo.numen.Constants;
import com.dwinovo.numen.agent.llm.LlmEndpoint;
import com.dwinovo.numen.agent.llm.NumenLlmClient;
import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.agent.tool.ToolRegistry;
import com.dwinovo.numen.server.McpPrincipal;
import com.dwinovo.numen.server.SecurityPolicy;
import com.dwinovo.numen.server.ServerNumenActuator;
import com.dwinovo.numen.server.ToolCapability;
import com.dwinovo.numen.server.ToolCatalog;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Owns the optional server-side API agents: config gate (no key → gracefully disabled while MCP
 * keeps working), the real {@link NumenLlmClient} transport wiring, one worker thread per request,
 * and one {@link ServerApiAgent} per companion. The API key is resolved inside
 * {@link ApiAgentConfig#resolveApiKey()} at call time and handed only to the LLM transport — never
 * logged, never stored here.
 */
public final class ServerApiAgentManager {

    public static final ServerApiAgentManager INSTANCE = new ServerApiAgentManager();

    private volatile ApiAgentConfig config = ApiAgentConfig.disabledDefault();
    private volatile SecurityPolicy policy = SecurityPolicy.defaults();
    private volatile ServerNumenActuator actuator;
    private final Map<UUID, ServerApiAgent> agents = new ConcurrentHashMap<>();
    private final ExecutorService workers = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "numen-api-agent");
        t.setDaemon(true);
        return t;
    });

    private ServerApiAgentManager() {}

    public void configure(ApiAgentConfig config, SecurityPolicy policy, ServerNumenActuator actuator) {
        this.config = config;
        this.policy = policy;
        this.actuator = actuator;
        if (config.enabled() && config.resolveApiKey() == null) {
            Constants.LOG.warn("[numen-agent] api_agent enabled but NO key resolvable (env {} / file '{}') — "
                    + "agent stays disabled; MCP is unaffected", config.apiKeyEnv(), config.apiKeyFile());
        } else if (config.isUsable()) {
            Constants.LOG.info("[numen-agent] api_agent ready (provider={}, model={}) — key sourced server-side only",
                    config.provider(), config.model());
        }
    }

    public void shutdown() {
        for (ServerApiAgent a : agents.values()) a.stop();
        agents.clear();
    }

    /** null when disabled or no key — the caller reports the reason without ever seeing the key. */
    public String unusableReason() {
        if (!config.enabled()) return "api_agent disabled in config";
        if (config.resolveApiKey() == null) return "no API key resolvable (env/file) — agent disabled, MCP unaffected";
        return null;
    }

    public ServerApiAgent agentFor(UUID companionId) {
        return agents.get(companionId);
    }

    /**
     * Start one request on a companion's agent (worker thread). Fails fast when unusable or when the
     * companion's agent is already mid-request. The lease is acquired inside the run (shared
     * single-writer semantics with MCP).
     */
    public boolean submit(UUID companionId, String userText, java.util.function.Consumer<String> onDone) {
        if (unusableReason() != null || actuator == null) return false;
        ServerApiAgent existing = agents.get(companionId);
        if (existing != null && existing.state() == ServerApiAgent.State.RUNNING) return false;

        ApiAgentConfig cfg = this.config;
        ServerApiAgent agent = new ServerApiAgent(actuator, McpPrincipal.admin("api-agent"), companionId,
                realLlm(cfg), cfg.maxTurns(), 1000, cfg.requestTimeoutSeconds());
        agents.put(companionId, agent);
        workers.submit(() -> {
            try {
                String out = agent.run(userText);
                onDone.accept("[api-agent] " + (out == null || out.isBlank() ? "(done)" : out));
            } catch (Exception ex) {
                onDone.accept("[api-agent] failed: " + ex.getMessage());
            }
        });
        return true;
    }

    public void stop(UUID companionId) {
        ServerApiAgent a = agents.get(companionId);
        if (a != null) a.stop();
    }

    /** The real transport: NumenLlmClient with the key resolved at call time, tools filtered by policy. */
    private ServerApiAgent.LlmFn realLlm(ApiAgentConfig cfg) {
        return (messages, systemPrompt) -> {
            String key = cfg.resolveApiKey();   // resolved per call; never cached or logged here
            LlmEndpoint ep = new LlmEndpoint(cfg.provider(), cfg.model(), key, cfg.baseUrl(), "",
                    cfg.reasoningEffort());
            List<NumenTool> tools = new ArrayList<>();
            for (NumenTool t : ToolRegistry.all()) {
                ToolCapability cap = ToolCatalog.of(t.name());
                if (cap.isServerExposable() && policy.capabilityEnabled(cap)) tools.add(t);
            }
            return NumenLlmClient.forEndpoint(ep)
                    .chatStreaming(messages, tools, systemPrompt, null)
                    .thenApply(NumenLlmClient.ChatResult::turn);
        };
    }
}
