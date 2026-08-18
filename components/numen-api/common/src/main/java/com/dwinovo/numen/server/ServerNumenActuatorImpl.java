// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.agent.tool.ToolRegistry;
import com.dwinovo.numen.entity.CompanionFactory;
import com.dwinovo.numen.entity.CompanionRegistry;
import com.dwinovo.numen.entity.Companions;
import com.dwinovo.numen.entity.NumenPlayer;
import com.dwinovo.numen.entity.ServerOwner;
import com.dwinovo.numen.task.CompanionTickDispatcher;
import com.dwinovo.numen.task.TaskRecord;
import com.dwinovo.numen.task.TaskState;
import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;
import java.util.function.Function;

/**
 * The one and only server-side actuator (singleton). Every call hops onto the server main thread and
 * does all its work there. S3 makes it the single security choke-point: every call passes through
 * {@link SecurityPolicy} (capability whitelist + role + dimension + namespace), the write-control
 * {@link LeaseRegistry} (single-writer + fencing), the {@link RateLimiter}s (per-principal and
 * per-companion), a global concurrent-task budget, and is written to the {@link AuditLog} — always,
 * on success and failure. Leases are swept on TTL and released on death/remove/shutdown.
 */
public final class ServerNumenActuatorImpl implements ServerNumenActuator {

    public static final ServerNumenActuatorImpl INSTANCE = new ServerNumenActuatorImpl();

    private volatile MinecraftServer server;
    private volatile SecurityPolicy policy = SecurityPolicy.defaults();
    private volatile AuthTokens tokens = AuthTokens.empty();
    private final LeaseRegistry leases = new LeaseRegistry();
    private final AuditLog audit = new AuditLog();
    /** 不占身体车道的延迟查询使用独立短 id，避免把内部 mcp UUID 暴露给模型。 */
    private static final AtomicLong QUERY_IDS = new AtomicLong();
    private volatile RateLimiter principalLimiter = new RateLimiter(policy.maxCallsPerMinutePerPrincipal(), 60_000);
    private volatile RateLimiter companionLimiter = new RateLimiter(policy.maxCallsPerMinutePerCompanion(), 60_000);

    private ServerNumenActuatorImpl() {}

    public void attach(MinecraftServer server) { this.server = server; }
    public void detach() { this.server = null; }

    /** Apply loaded config (policy + tokens) and rebuild the rate limiters. */
    public void configure(ServerControlConfig cfg) {
        this.policy = cfg.policy;
        this.tokens = cfg.tokens;
        this.principalLimiter = new RateLimiter(cfg.policy.maxCallsPerMinutePerPrincipal(), 60_000);
        this.companionLimiter = new RateLimiter(cfg.policy.maxCallsPerMinutePerCompanion(), 60_000);
    }

    public LeaseRegistry leases() { return leases; }
    public AuditLog auditLog() { return audit; }
    public SecurityPolicy policy() { return policy; }
    public AuthTokens tokens() { return tokens; }

    /** TTL sweep — call each server tick. Releases timed-out leases and audits each. */
    public void tick() {
        MinecraftServer s = this.server;
        if (s == null) return;
        for (UUID id : leases.sweepExpired(System.currentTimeMillis())) {
            audit.record(rec("system", "actuator", id, "lease_expired", null, null, 0, true, "ttl_release"));
        }
    }

    /** Release a companion's lease on death/remove/delete (called from lifecycle hooks). */
    public void onCompanionGone(UUID companionId) {
        leases.releaseAll(companionId);
    }

    // ---- main-thread hop ----

    private <T> CompletableFuture<T> onMainThread(Function<MinecraftServer, T> work) {
        CompletableFuture<T> f = new CompletableFuture<>();
        MinecraftServer s = this.server;
        if (s == null) {
            f.completeExceptionally(new IllegalStateException("server is not running"));
            return f;
        }
        s.execute(() -> {
            try { f.complete(work.apply(s)); }
            catch (Throwable t) { f.completeExceptionally(t); }
        });
        return f;
    }

    private static NumenPlayer resolve(MinecraftServer s, UUID companionId) {
        NumenPlayer self = NumenPlayer.findByUuid(s, companionId);
        if (self == null) self = Companions.respawn(s, companionId);
        return self;
    }

    private static UUID ownerOf(MinecraftServer s, UUID companionId) {
        NumenPlayer live = NumenPlayer.findByUuid(s, companionId);
        if (live != null) return live.getOwnerUuid();
        CompanionRegistry.Entry e = CompanionRegistry.get(s).find(companionId);
        return e == null ? null : e.owner();
    }

    private static int countActiveTasks(MinecraftServer s) {
        int n = 0;
        for (ServerPlayer p : s.getPlayerList().getPlayers()) {
            if (p instanceof NumenPlayer ap && CompanionTickDispatcher.asyncTaskFor(ap.getUUID()) != null) n++;
        }
        return n;
    }

    private AuditLog.Record rec(String principalId, String source, UUID companion, String op,
                                String taskId, String leaseId, long durMs, boolean ok, String code) {
        return new AuditLog.Record(System.currentTimeMillis(), principalId, source,
                companion == null ? null : companion.toString(), op, taskId, leaseId, durMs, ok, code);
    }

    // ---- roster ----

    @Override
    public CompletableFuture<List<CompanionRef>> listCompanions(McpPrincipal principal) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            List<CompanionRef> out = new ArrayList<>();
            UUID filterOwner = principal.role() == McpPrincipal.Role.OWNER ? principal.ownerUuid() : ServerOwner.ID;
            for (Map.Entry<UUID, CompanionRegistry.Entry> e : CompanionRegistry.get(s).ownedBy(filterOwner)) {
                if (!principal.canRead(e.getValue().owner())) continue;
                boolean alive = NumenPlayer.findByUuid(s, e.getKey()) != null;
                out.add(new CompanionRef(e.getKey(), e.getValue().name(), e.getValue().owner(), alive));
            }
            audit.record(rec(principal.id(), "actuator", null, "list_companions", null, null,
                    System.currentTimeMillis() - t0, true, "ok"));
            return out;
        });
    }

    @Override
    public CompletableFuture<CompanionRef> createCompanion(McpPrincipal principal, String name) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            if (!principal.canManage()) {
                audit.record(rec(principal.id(), "actuator", null, "create_companion", null, null,
                        System.currentTimeMillis() - t0, false, "not_permitted"));
                throw new IllegalStateException("principal not permitted to create companions");
            }
            String n = name == null ? "" : name.trim();
            if (!n.matches("[A-Za-z0-9_]{3,16}")) {
                audit.record(rec(principal.id(), "actuator", null, "create_companion", null, null,
                        System.currentTimeMillis() - t0, false, "bad_name"));
                throw new IllegalStateException("invalid name (3-16 letters, digits, or underscore): '" + n + "'");
            }
            ServerLevel level = s.overworld();
            Vec3 pos = Vec3.atBottomCenterOf(level.getSharedSpawnPos());
            NumenPlayer body = Companions.summonServerOwned(s, n, level, pos);
            audit.record(rec(principal.id(), "actuator", body.getUUID(), "create_companion", null, null,
                    System.currentTimeMillis() - t0, true, "ok"));
            return new CompanionRef(body.getUUID(), n, body.getOwnerUuid(), true);
        });
    }

    @Override
    public CompletableFuture<Boolean> deleteCompanion(McpPrincipal principal, UUID companionId) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            CompanionRegistry.Entry e = CompanionRegistry.get(s).find(companionId);
            boolean ok = principal.canManage() && e != null && principal.canControl(e.owner());
            if (ok) {
                NumenPlayer live = NumenPlayer.findByUuid(s, companionId);
                if (live != null) CompanionFactory.despawn(s, live);
                CompanionRegistry.get(s).remove(companionId);
                leases.releaseAll(companionId);
                ExternalTaskResultStore.clear(companionId);
            }
            audit.record(rec(principal.id(), "actuator", companionId, "delete_companion", null, null,
                    System.currentTimeMillis() - t0, ok, ok ? "ok" : "denied_or_missing"));
            return ok;
        });
    }

    // ---- control lease ----

    @Override
    public CompletableFuture<Lease> acquireControl(McpPrincipal principal, UUID companionId) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            UUID owner = ownerOf(s, companionId);
            if (owner == null) {
                audit.record(rec(principal.id(), "actuator", companionId, "acquire_control", null, null,
                        System.currentTimeMillis() - t0, false, "no_companion"));
                throw new IllegalStateException("no such companion: " + companionId);
            }
            if (!principal.canControl(owner)) {
                audit.record(rec(principal.id(), "actuator", companionId, "acquire_control", null, null,
                        System.currentTimeMillis() - t0, false, "not_permitted"));
                throw new IllegalStateException("principal not permitted to control this companion");
            }
            Lease lease = leases.acquire(principal, companionId, System.currentTimeMillis(), policy.leaseTtlMs());
            if (lease == null) {
                audit.record(rec(principal.id(), "actuator", companionId, "acquire_control", null, null,
                        System.currentTimeMillis() - t0, false, "contended"));
                throw new IllegalStateException("companion is already controlled by another principal");
            }
            audit.record(rec(principal.id(), "actuator", companionId, "acquire_control", null, lease.leaseId(),
                    System.currentTimeMillis() - t0, true, "ok"));
            return lease;
        });
    }

    @Override
    public CompletableFuture<Void> releaseControl(McpPrincipal principal, UUID companionId, String leaseId) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            boolean released = leases.release(companionId, principal.id(), leaseId);
            audit.record(rec(principal.id(), "actuator", companionId, "release_control", null, leaseId,
                    System.currentTimeMillis() - t0, released, released ? "ok" : "noop"));
            return null;
        });
    }

    // ---- act ----

    @Override
    public CompletableFuture<ToolResult> invoke(McpPrincipal principal, UUID companionId, String leaseId,
                                                String toolName, JsonObject arguments) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            NumenPlayer self = resolve(s, companionId);
            if (self == null) return fail(principal, companionId, toolName, leaseId, t0, "no_companion",
                    "no such companion (never summoned, or its data is gone)");
            String dim = self.level().dimension().location().toString();

            // 1) policy: capability whitelist + role + dimension
            PolicyDecision d = policy.permitInvoke(principal, toolName, dim);
            if (!d.allowed()) return fail(principal, companionId, toolName, leaseId, t0, d.code(), "denied: " + d.reason());
            ToolCapability cap = d.capability();

            // 2) namespace of args (no modded ids unless allowed)
            String badNs = policy.namespaceViolation(arguments);
            if (badNs != null) return fail(principal, companionId, toolName, leaseId, t0, "namespace",
                    "namespace '" + badNs + "' not allowed by policy");

            // 3) control tools need canControl + a valid lease; read tools need only canRead (concurrent)
            if (cap.requiresControl()) {
                if (!principal.canControl(self.getOwnerUuid())) return fail(principal, companionId, toolName, leaseId, t0,
                        "not_owner", "principal not permitted to control this companion");
                if (!leases.isValidForWrite(companionId, principal, leaseId, System.currentTimeMillis()))
                    return fail(principal, companionId, toolName, leaseId, t0, "no_lease",
                            "invalid or missing control lease — acquire_control first");
            } else if (!principal.canRead(self.getOwnerUuid())) {
                return fail(principal, companionId, toolName, leaseId, t0, "not_reader",
                        "principal not permitted to read this companion");
            }

            // 4) rate limits (per principal + per companion)
            long now = System.currentTimeMillis();
            if (!principalLimiter.tryAcquire(principal.id(), now))
                return fail(principal, companionId, toolName, leaseId, t0, "rate_principal", "rate limit exceeded (principal)");
            if (!companionLimiter.tryAcquire(companionId.toString(), now))
                return fail(principal, companionId, toolName, leaseId, t0, "rate_companion", "rate limit exceeded (companion)");

            // 5) global concurrent-task budget (for control tools that may dispatch async work)
            if (cap.requiresControl() && countActiveTasks(s) >= policy.globalMaxConcurrentTasks())
                return fail(principal, companionId, toolName, leaseId, t0, "budget_tasks", "global concurrent-task budget reached");

            NumenTool tool = ToolRegistry.get(toolName);
            if (tool == null) return fail(principal, companionId, toolName, leaseId, t0, "unknown_tool", "unknown tool: " + toolName);

            String toolCallId = "mcp-" + UUID.randomUUID();
            String[] captured = new String[1];
            String queryId = "q" + QUERY_IDS.incrementAndGet();
            AtomicBoolean invokeReturned = new AtomicBoolean(false);
            Consumer<String> reply = json -> {
                if (!invokeReturned.get() && captured[0] == null) {
                    captured[0] = json;
                } else {
                    // scan_blocks 等查询在后续 tick 才回调。保存真实结构化结果，
                    // task_status(queryId) 可稳定取回，不再丢成 queued 空包。
                    ExternalTaskResultStore.recordQuery(companionId, queryId, toolName, json);
                }
            };
            try {
                tool.onServerCall(toolCallId, arguments == null ? new JsonObject() : arguments, self, reply);
            } catch (RuntimeException ex) {
                return fail(principal, companionId, toolName, leaseId, t0, "tool_error", "tool rejected the call: " + ex.getMessage());
            }
            invokeReturned.set(true);
            if (captured[0] == null) {
                // 先登记 qN，再把它返回客户端；否则立即轮询可能早于晚到回调，
                // 被误报 unknown task_id。回调完成后 recordQuery 会覆盖 queued。
                ExternalTaskResultStore.registerQuery(companionId, queryId, toolName);
            }
            ToolResult tr = captured[0] == null
                    ? ToolResult.of("{\"success\":true,\"message\":\"查询已受理，后台执行中；请用 task_status 轮询最终结果。\",\"data\":{\"task_id\":\""
                            + queryId + "\",\"task\":\"" + toolName + "\",\"async\":true}}")
                    : ToolResult.of(captured[0]);
            audit.record(rec(principal.id(), "actuator", companionId, toolName, tr.taskId(), leaseId,
                    System.currentTimeMillis() - t0, tr.ok(), tr.ok() ? "ok" : "tool_fail"));
            return tr;
        });
    }

    private ToolResult fail(McpPrincipal p, UUID companion, String tool, String leaseId, long t0,
                            String code, String message) {
        audit.record(rec(p.id(), "actuator", companion, tool, null, leaseId,
                System.currentTimeMillis() - t0, false, code));
        return ToolResult.fail(message);
    }

    @Override
    public CompletableFuture<TaskStatus> getTaskStatus(McpPrincipal principal, UUID companionId, String taskId) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            NumenPlayer self = NumenPlayer.findByUuid(s, companionId);
            if (self == null) {
                audit.record(rec(principal.id(), "actuator", companionId, "task_status", taskId, null,
                        System.currentTimeMillis() - t0, false, "no_companion"));
                return TaskStatus.error("no such live companion");
            }
            if (!principal.canRead(self.getOwnerUuid())) {
                audit.record(rec(principal.id(), "actuator", companionId, "task_status", taskId, null,
                        System.currentTimeMillis() - t0, false, "unauthorized"));
                return TaskStatus.error("unauthorized");
            }
            TaskRecord rc = CompanionTickDispatcher.asyncTaskFor(companionId);
            TaskStatus st;
            if (taskId != null && !taskId.isBlank()) {
                if (rc != null && taskId.equals(rc.publicId())) {
                    st = new TaskStatus(rc.publicId(),
                            rc.getState() == TaskState.RUNNING ? "running" : "queued", rc.describe());
                } else {
                    TaskStatus terminal = ExternalTaskResultStore.get(companionId, taskId);
                    st = terminal == null
                            ? TaskStatus.error("unknown task_id: " + taskId)
                            : terminal;
                }
            } else {
                st = rc == null ? TaskStatus.idle()
                        : new TaskStatus(rc.publicId(),
                        rc.getState() == TaskState.RUNNING ? "running" : "queued", rc.describe());
            }
            audit.record(rec(principal.id(), "actuator", companionId, "task_status", st.taskId(), null,
                    System.currentTimeMillis() - t0, true, "ok"));
            return st;
        });
    }

    @Override
    public CompletableFuture<Void> stopTask(McpPrincipal principal, UUID companionId, String taskId) {
        long t0 = System.currentTimeMillis();
        return onMainThread(s -> {
            NumenPlayer self = NumenPlayer.findByUuid(s, companionId);
            if (self == null) {
                audit.record(rec(principal.id(), "actuator", companionId, "task_stop", taskId, null,
                        System.currentTimeMillis() - t0, false, "no_companion"));
                throw new IllegalStateException("no such live companion");
            }
            if (!principal.canControl(self.getOwnerUuid())) {
                audit.record(rec(principal.id(), "actuator", companionId, "task_stop", taskId, null,
                        System.currentTimeMillis() - t0, false, "not_permitted"));
                throw new IllegalStateException("not authorized");
            }
            CompanionTickDispatcher.stopActive(self, "stopped by actuator (" + principal.id() + ")");
            audit.record(rec(principal.id(), "actuator", companionId, "task_stop", taskId, null,
                    System.currentTimeMillis() - t0, true, "ok"));
            return null;
        });
    }
}
