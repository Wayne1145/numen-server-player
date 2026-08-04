// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.dwinovo.numen.entity.CompanionRegistry;
import com.dwinovo.numen.entity.NumenPlayer;
import com.dwinovo.numen.entity.ServerOwner;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;

/**
 * {@code /numenctl …} — dedicated-server console harness driving {@link ServerNumenActuator} with
 * <em>simulated</em> principals, so S2/S3 can be exercised with no client, no MCP, no LLM.
 *
 * <p>Principals switch with {@code /numenctl principal <admin|adminB|ro|owner>} (default: admin),
 * which lets one console demonstrate lease contention (two admins), readonly denial, capability
 * gating, and the audit trail. Leases are cached per (principal, companion) so invoke/release need
 * no typing.
 *
 * <pre>
 *   /numenctl principal &lt;name&gt;    switch acting principal (admin|adminB|ro|owner)
 *   /numenctl whoami | policy | audit [n]
 *   /numenctl create|list|acquire|release|status|stop &lt;…&gt;
 *   /numenctl invoke &lt;companion&gt; &lt;tool&gt; [json]
 * </pre>
 */
public final class ServerActuatorCommands {

    private static final ServerNumenActuatorImpl ACTUATOR = ServerNumenActuatorImpl.INSTANCE;

    /** Fixed owner UUID for the simulated OWNER principal (distinct from ServerOwner.ID). */
    private static final UUID CLI_OWNER_UUID = UUID.fromString("11111111-1111-1111-1111-111111111111");

    private static final Map<String, McpPrincipal> PRINCIPALS = new LinkedHashMap<>();
    static {
        PRINCIPALS.put("admin", McpPrincipal.admin("cli-admin"));
        PRINCIPALS.put("adminB", McpPrincipal.admin("cli-adminB"));
        PRINCIPALS.put("ro", McpPrincipal.readonly("cli-ro"));
        PRINCIPALS.put("owner", McpPrincipal.owner("cli-owner", CLI_OWNER_UUID));
    }

    private static volatile String currentName = "admin";
    /** Cached leases, keyed principalId|companionId. */
    private static final Map<String, String> LEASES = new ConcurrentHashMap<>();

    private ServerActuatorCommands() {}

    private static McpPrincipal current() { return PRINCIPALS.get(currentName); }
    private static String leaseKey(McpPrincipal p, UUID id) { return p.id() + "|" + id; }

    public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("numenctl")
                .requires(src -> src.hasPermission(2))
                .then(Commands.literal("principal")
                        .then(Commands.argument("name", StringArgumentType.word())
                                .executes(ctx -> setPrincipal(ctx, StringArgumentType.getString(ctx, "name")))))
                .then(Commands.literal("whoami").executes(ServerActuatorCommands::whoami))
                .then(Commands.literal("policy").executes(ServerActuatorCommands::showPolicy))
                .then(Commands.literal("audit")
                        .executes(ctx -> audit(ctx, 12))
                        .then(Commands.argument("n", IntegerArgumentType.integer(1, 200))
                                .executes(ctx -> audit(ctx, IntegerArgumentType.getInteger(ctx, "n")))))
                .then(Commands.literal("list").executes(ServerActuatorCommands::list))
                .then(Commands.literal("create")
                        .then(Commands.argument("name", StringArgumentType.word())
                                .executes(ctx -> create(ctx, StringArgumentType.getString(ctx, "name")))))
                .then(Commands.literal("acquire")
                        .then(Commands.argument("companion", StringArgumentType.word())
                                .executes(ctx -> acquire(ctx, StringArgumentType.getString(ctx, "companion")))))
                .then(Commands.literal("release")
                        .then(Commands.argument("companion", StringArgumentType.word())
                                .executes(ctx -> release(ctx, StringArgumentType.getString(ctx, "companion")))))
                .then(Commands.literal("status")
                        .then(Commands.argument("companion", StringArgumentType.word())
                                .executes(ctx -> status(ctx, StringArgumentType.getString(ctx, "companion")))))
                .then(Commands.literal("stop")
                        .then(Commands.argument("companion", StringArgumentType.word())
                                .executes(ctx -> stop(ctx, StringArgumentType.getString(ctx, "companion")))))
                .then(Commands.literal("invoke")
                        .then(Commands.argument("companion", StringArgumentType.word())
                                .then(Commands.argument("tool", StringArgumentType.word())
                                        .executes(ctx -> invoke(ctx, StringArgumentType.getString(ctx, "companion"),
                                                StringArgumentType.getString(ctx, "tool"), "{}"))
                                        .then(Commands.argument("json", StringArgumentType.greedyString())
                                                .executes(ctx -> invoke(ctx, StringArgumentType.getString(ctx, "companion"),
                                                        StringArgumentType.getString(ctx, "tool"),
                                                        StringArgumentType.getString(ctx, "json")))))))
                // S5: the optional server-side API agent — same actuator, same lease as MCP.
                .then(Commands.literal("agent")
                        .then(Commands.literal("status").executes(ServerActuatorCommands::agentStatus))
                        .then(Commands.literal("stop")
                                .then(Commands.argument("companion", StringArgumentType.word())
                                        .executes(ctx -> agentStop(ctx, StringArgumentType.getString(ctx, "companion")))))
                        .then(Commands.literal("say")
                                .then(Commands.argument("companion", StringArgumentType.word())
                                        .then(Commands.argument("text", StringArgumentType.greedyString())
                                                .executes(ctx -> agentSay(ctx, StringArgumentType.getString(ctx, "companion"),
                                                        StringArgumentType.getString(ctx, "text"))))))));
    }

    // ---- S5 API-agent harness ----

    private static int agentStatus(CommandContext<CommandSourceStack> ctx) {
        var mgr = com.dwinovo.numen.server.agent.ServerApiAgentManager.INSTANCE;
        String reason = mgr.unusableReason();
        ok(ctx.getSource(), reason == null ? "api-agent: READY (key sourced server-side)" : "api-agent: UNAVAILABLE — " + reason);
        return 1;
    }

    private static int agentSay(CommandContext<CommandSourceStack> ctx, String raw, String text) {
        CommandSourceStack src = ctx.getSource();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        var mgr = com.dwinovo.numen.server.agent.ServerApiAgentManager.INSTANCE;
        String reason = mgr.unusableReason();
        if (reason != null) { src.sendFailure(Component.literal("api-agent unavailable: " + reason)); return 0; }
        boolean accepted = mgr.submit(id, text, msg -> src.getServer().execute(() -> ok(src, msg)));
        if (!accepted) { src.sendFailure(Component.literal("agent busy (a request is already running for " + raw + ")")); return 0; }
        ok(src, "api-agent working on '" + raw + "' — result will follow");
        return 1;
    }

    private static int agentStop(CommandContext<CommandSourceStack> ctx, String raw) {
        CommandSourceStack src = ctx.getSource();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        com.dwinovo.numen.server.agent.ServerApiAgentManager.INSTANCE.stop(id);
        ok(src, "api-agent stop requested for " + raw);
        return 1;
    }

    private static int setPrincipal(CommandContext<CommandSourceStack> ctx, String name) {
        if (!PRINCIPALS.containsKey(name)) {
            ctx.getSource().sendFailure(Component.literal("unknown principal '" + name + "' (admin|adminB|ro|owner)"));
            return 0;
        }
        currentName = name;
        ok(ctx.getSource(), "acting principal = " + name + " (" + current().role() + ")");
        return 1;
    }

    private static int whoami(CommandContext<CommandSourceStack> ctx) {
        McpPrincipal p = current();
        ok(ctx.getSource(), "principal " + currentName + " id=" + p.id() + " role=" + p.role() + " owner=" + p.ownerUuid());
        return 1;
    }

    private static int showPolicy(CommandContext<CommandSourceStack> ctx) {
        SecurityPolicy p = ACTUATOR.policy();
        ok(ctx.getSource(), "policy: read=" + p.capabilityEnabled(ToolCapability.SERVER_READ_ONLY)
                + " body=" + p.capabilityEnabled(ToolCapability.SERVER_BODY_ACTION)
                + " destructive=" + p.capabilityEnabled(ToolCapability.DESTRUCTIVE)
                + " admin=" + p.capabilityEnabled(ToolCapability.ADMIN_ONLY)
                + " leaseTtlMs=" + p.leaseTtlMs()
                + " rate/min p=" + p.maxCallsPerMinutePerPrincipal() + " c=" + p.maxCallsPerMinutePerCompanion());
        return 1;
    }

    private static int audit(CommandContext<CommandSourceStack> ctx, int n) {
        CommandSourceStack src = ctx.getSource();
        var recs = ACTUATOR.auditLog().tail(n);
        ok(src, "audit (last " + recs.size() + "):");
        for (AuditLog.Record r : recs) ok(src, "  " + r.toLine());
        return recs.size();
    }

    private static int list(CommandContext<CommandSourceStack> ctx) {
        CommandSourceStack src = ctx.getSource();
        McpPrincipal p = current();
        ACTUATOR.listCompanions(p).whenComplete((l, err) -> {
            if (err != null) { fail(src, "list", err); return; }
            if (l.isEmpty()) { ok(src, "no server companions"); return; }
            ok(src, "server companions (" + l.size() + "):");
            for (CompanionRef r : l) ok(src, "  - " + r.name() + (r.alive() ? " [live]" : " [dormant]") + "  " + r.id());
        });
        return 1;
    }

    private static int create(CommandContext<CommandSourceStack> ctx, String name) {
        CommandSourceStack src = ctx.getSource();
        ACTUATOR.createCompanion(current(), name).whenComplete((ref, err) -> {
            if (err != null) fail(src, "create", err);
            else ok(src, "created '" + ref.name() + "' " + ref.id());
        });
        return 1;
    }

    private static int acquire(CommandContext<CommandSourceStack> ctx, String raw) {
        CommandSourceStack src = ctx.getSource();
        McpPrincipal p = current();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        ACTUATOR.acquireControl(p, id).whenComplete((lease, err) -> {
            if (err != null) { fail(src, "acquire", err); return; }
            LEASES.put(leaseKey(p, id), lease.leaseId());
            ok(src, "[" + currentName + "] acquired lease " + lease.leaseId() + " (fence " + lease.fencingToken() + ") for " + raw);
        });
        return 1;
    }

    private static int release(CommandContext<CommandSourceStack> ctx, String raw) {
        CommandSourceStack src = ctx.getSource();
        McpPrincipal p = current();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        String lease = LEASES.remove(leaseKey(p, id));
        ACTUATOR.releaseControl(p, id, lease).whenComplete((v, err) -> {
            if (err != null) fail(src, "release", err);
            else ok(src, "[" + currentName + "] released lease for " + raw);
        });
        return 1;
    }

    private static int invoke(CommandContext<CommandSourceStack> ctx, String raw, String tool, String json) {
        CommandSourceStack src = ctx.getSource();
        McpPrincipal p = current();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        String lease = LEASES.get(leaseKey(p, id));   // may be null (read tools / no lease held)
        JsonObject args;
        try {
            args = json == null || json.isBlank() ? new JsonObject() : JsonParser.parseString(json).getAsJsonObject();
        } catch (RuntimeException ex) {
            src.sendFailure(Component.literal("bad JSON args: " + ex.getMessage()));
            return 0;
        }
        ACTUATOR.invoke(p, id, lease, tool, args).whenComplete((res, err) -> {
            if (err != null) { fail(src, "invoke", err); return; }
            ok(src, "[" + currentName + "] [" + (res.ok() ? "ok" : "FAIL")
                    + (res.taskId() != null ? " task=" + res.taskId() : "") + "] " + res.json());
        });
        return 1;
    }

    private static int status(CommandContext<CommandSourceStack> ctx, String raw) {
        CommandSourceStack src = ctx.getSource();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        ACTUATOR.getTaskStatus(current(), id, null).whenComplete((st, err) -> {
            if (err != null) fail(src, "status", err);
            else ok(src, "status: " + st.state() + (st.taskId() != null ? " " + st.taskId() : "") + " — " + st.detail());
        });
        return 1;
    }

    private static int stop(CommandContext<CommandSourceStack> ctx, String raw) {
        CommandSourceStack src = ctx.getSource();
        UUID id = resolve(src.getServer(), raw);
        if (id == null) { src.sendFailure(Component.literal("no such companion: " + raw)); return 0; }
        ACTUATOR.stopTask(current(), id, null).whenComplete((v, err) -> {
            if (err != null) fail(src, "stop", err);
            else ok(src, "stopped background task for " + raw);
        });
        return 1;
    }

    private static UUID resolve(MinecraftServer s, String raw) {
        try {
            UUID u = UUID.fromString(raw);
            if (NumenPlayer.findByUuid(s, u) != null || CompanionRegistry.get(s).find(u) != null) return u;
        } catch (IllegalArgumentException ignored) {
            // not a UUID — name lookup
        }
        for (Map.Entry<UUID, CompanionRegistry.Entry> e : CompanionRegistry.get(s).ownedBy(ServerOwner.ID)) {
            if (e.getValue().name().equals(raw)) return e.getKey();
        }
        for (Map.Entry<UUID, CompanionRegistry.Entry> e : CompanionRegistry.get(s).ownedBy(ServerOwner.ID)) {
            if (e.getValue().name().equalsIgnoreCase(raw)) return e.getKey();
        }
        return null;
    }

    private static void ok(CommandSourceStack src, String msg) {
        src.sendSuccess(() -> Component.literal(msg), false);
    }

    private static void fail(CommandSourceStack src, String op, Throwable err) {
        src.sendFailure(Component.literal(op + " failed: " + rootMsg(err)));
    }

    private static String rootMsg(Throwable t) {
        Throwable c = t;
        while (c instanceof CompletionException && c.getCause() != null) c = c.getCause();
        return c.getMessage() != null ? c.getMessage() : c.toString();
    }
}
