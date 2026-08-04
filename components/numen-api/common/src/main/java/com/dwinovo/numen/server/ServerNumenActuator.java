// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.google.gson.JsonObject;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * The pure server-side action entry point. Replaces the client-side {@code NumenActuator} and the
 * C→S {@code ExecuteToolPayload} round-trip: every API-agent and MCP call funnels here, and every
 * method funnels into one server main-thread step (via {@code MinecraftServer.execute}) that
 * resolves the companion, checks the {@link McpPrincipal}, and drives it through
 * {@code NumenTool.onServerCall} + the {@code TaskQueue}.
 *
 * <p>Threading contract: callers (HTTP/MCP workers, an API agent, a test command) may invoke these
 * from any thread. The returned {@link CompletableFuture} completes when the main-thread step runs.
 * Implementations MUST NOT touch world/entity/registry/menu/task state off the main thread.
 *
 * <p>This is the shared seam the whole control layer builds on: S3 wraps it with token→principal
 * auth, capability whitelist, rate limits, action budgets, and audit; S4 exposes it over MCP;
 * S5 drives it from an API agent — all through this one interface, never bypassing it.
 */
public interface ServerNumenActuator {

    /** Companions this principal may see (admin: all server-owned; owner: its own). */
    CompletableFuture<List<CompanionRef>> listCompanions(McpPrincipal principal);

    /** Summon a fresh server-owned companion by name. Fails (exceptionally) on bad name / no permission. */
    CompletableFuture<CompanionRef> createCompanion(McpPrincipal principal, String name);

    /** Permanently dismiss a companion. Returns false if not found / not permitted. */
    CompletableFuture<Boolean> deleteCompanion(McpPrincipal principal, UUID companionId);

    /** Acquire the single write lease over a companion. Fails (exceptionally) on contention / no permission. */
    CompletableFuture<Lease> acquireControl(McpPrincipal principal, UUID companionId);

    /** Release a previously-acquired lease. Idempotent. */
    CompletableFuture<Void> releaseControl(McpPrincipal principal, UUID companionId, String leaseId);

    /**
     * Run a tool against a companion. Read-only tools reply immediately; world actions dispatch a
     * background task and reply with a {@code task_id} — poll {@link #getTaskStatus} until idle.
     */
    CompletableFuture<ToolResult> invoke(McpPrincipal principal, UUID companionId, String leaseId,
                                         String toolName, JsonObject arguments);

    /** Snapshot of the companion's current background task (running / queued / idle). */
    CompletableFuture<TaskStatus> getTaskStatus(McpPrincipal principal, UUID companionId, String taskId);

    /** Abort the companion's current background task. */
    CompletableFuture<Void> stopTask(McpPrincipal principal, UUID companionId, String taskId);
}
