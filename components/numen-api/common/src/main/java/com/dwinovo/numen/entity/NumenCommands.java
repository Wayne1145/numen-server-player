package com.dwinovo.numen.entity;

import com.dwinovo.numen.network.payload.ClientUiActionPayload;
import com.dwinovo.numen.platform.Services;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.arguments.coordinates.Vec3Argument;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * The unified server-side {@code /numen} command tree — one root with
 * per-companion verbs. Lives entirely on the server so it never collides with
 * the client command dispatcher; the two inherently client-local verbs
 * ({@code settings}, {@code reset}) act on the caller's own client by firing a
 * {@link ClientUiActionPayload} back at them.
 *
 * <pre>
 *   /numen player summon &lt;name&gt;    summon the named companion (idempotent — reuses an existing one)
 *   /numen player despawn &lt;name&gt;   permanently dismiss the named companion (gone for good)
 *   /numen settings                  open the settings GUI on the caller's client
 *   /numen reset                     clear the caller's conversation loops
 * </pre>
 */
@com.dwinovo.numen.api.Internal
public final class NumenCommands {

    private NumenCommands() {}

    public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("numen")
                .then(Commands.literal("player")
                        .then(Commands.literal("summon")
                                .then(Commands.argument("name", StringArgumentType.word())
                                        .executes(ctx -> summon(ctx, StringArgumentType.getString(ctx, "name")))))
                        .then(Commands.literal("despawn")
                                .then(Commands.argument("name", StringArgumentType.word())
                                        .executes(ctx -> despawn(ctx, StringArgumentType.getString(ctx, "name"))))))
                .then(Commands.literal("settings")
                        .executes(ctx -> clientAction(ctx, ClientUiActionPayload.Action.OPEN_SETTINGS)))
                .then(Commands.literal("reset")
                        .executes(ctx -> clientAction(ctx, ClientUiActionPayload.Action.RESET_LOOPS)))
                // Pure server-side (no human owner, no client): summon/despawn/list/status a
                // SERVER-OWNED companion straight from the dedicated-server console. Ops only.
                .then(Commands.literal("server")
                        .requires(src -> src.hasPermission(2))
                        .then(Commands.literal("summon")
                                .then(Commands.argument("name", StringArgumentType.word())
                                        .executes(ctx -> serverSummon(ctx,
                                                StringArgumentType.getString(ctx, "name"), null))
                                        .then(Commands.argument("pos", Vec3Argument.vec3())
                                                .executes(ctx -> serverSummon(ctx,
                                                        StringArgumentType.getString(ctx, "name"),
                                                        Vec3Argument.getVec3(ctx, "pos"))))))
                        .then(Commands.literal("despawn")
                                .then(Commands.argument("name", StringArgumentType.word())
                                        .executes(ctx -> serverDespawn(ctx,
                                                StringArgumentType.getString(ctx, "name")))))
                        .then(Commands.literal("list")
                                .executes(NumenCommands::serverList))
                        .then(Commands.literal("status")
                                .then(Commands.argument("name", StringArgumentType.word())
                                        .executes(ctx -> serverStatus(ctx,
                                                StringArgumentType.getString(ctx, "name")))))));
    }

    // ---- server-owned companion admin (console-capable; no player required) ----

    private static int serverSummon(CommandContext<CommandSourceStack> ctx, String name, Vec3 posArg) {
        CommandSourceStack src = ctx.getSource();
        MinecraftServer server = src.getServer();
        ServerLevel level = src.getLevel();               // console → overworld
        Vec3 pos = posArg != null ? posArg
                : (src.getEntity() != null ? src.getEntity().position()
                : Vec3.atBottomCenterOf(level.getSharedSpawnPos()));
        NumenPlayer body = Companions.summonServerOwned(server, name, level, pos);
        Vec3 at = body.position();
        src.sendSuccess(() -> Component.literal(String.format(
                "Summoned server companion '%s' (%s) at %.1f %.1f %.1f in %s",
                name, body.getUUID(), at.x, at.y, at.z, level.dimension().location())), true);
        return 1;
    }

    private static int serverDespawn(CommandContext<CommandSourceStack> ctx, String name) {
        CommandSourceStack src = ctx.getSource();
        int n = Companions.dismissByName(src.getServer(), ServerOwner.ID, name);
        if (n == 0) {
            src.sendFailure(Component.literal("No server companion named '" + name + "'"));
            return 0;
        }
        src.sendSuccess(() -> Component.literal("Dismissed server companion '" + name + "' — gone for good"
                + (n > 1 ? " (cleaned up " + n + " duplicates)" : "")), true);
        return n;
    }

    private static int serverList(CommandContext<CommandSourceStack> ctx) {
        CommandSourceStack src = ctx.getSource();
        MinecraftServer server = src.getServer();
        List<Map.Entry<UUID, CompanionRegistry.Entry>> entries = new ArrayList<>();
        for (Map.Entry<UUID, CompanionRegistry.Entry> e :
                CompanionRegistry.get(server).ownedBy(ServerOwner.ID)) {
            entries.add(e);
        }
        if (entries.isEmpty()) {
            src.sendSuccess(() -> Component.literal("No server companions."), false);
            return 0;
        }
        src.sendSuccess(() -> Component.literal("Server companions (" + entries.size() + "):"), false);
        for (Map.Entry<UUID, CompanionRegistry.Entry> e : entries) {
            CompanionRegistry.Entry ent = e.getValue();
            NumenPlayer live = NumenPlayer.findByUuid(server, e.getKey());
            String state = live != null ? (live.isDegraded() ? "DEGRADED" : "live")
                    : (ent.diedAt() > 0L ? "dead" : "dormant");
            String hp = live != null
                    ? String.format("  hp=%.0f/%.0f", live.getHealth(), live.getMaxHealth()) : "";
            String line = String.format("  - %s  [%s]  %s @ %s%s  %s",
                    ent.name(), state, ent.pos().toShortString(), ent.dimension().location(), hp, e.getKey());
            src.sendSuccess(() -> Component.literal(line), false);
        }
        return entries.size();
    }

    private static int serverStatus(CommandContext<CommandSourceStack> ctx, String name) {
        CommandSourceStack src = ctx.getSource();
        MinecraftServer server = src.getServer();
        UUID id = null;
        CompanionRegistry.Entry ent = null;
        for (Map.Entry<UUID, CompanionRegistry.Entry> e :
                CompanionRegistry.get(server).ownedBy(ServerOwner.ID)) {
            if (e.getValue().name().equals(name)) {
                id = e.getKey();
                ent = e.getValue();
                break;
            }
        }
        if (ent == null) {
            src.sendFailure(Component.literal("No server companion named '" + name + "'"));
            return 0;
        }
        NumenPlayer live = NumenPlayer.findByUuid(server, id);
        StringBuilder sb = new StringBuilder();
        sb.append("Companion '").append(name).append("' ").append(id);
        if (live != null) {
            sb.append("\n  state: ").append(live.isDegraded() ? "DEGRADED" : "live");
            sb.append("\n  pos: ").append(live.blockPosition().toShortString())
                    .append(" @ ").append(live.level().dimension().location());
            sb.append("\n  hp: ").append(String.format("%.1f/%.1f", live.getHealth(), live.getMaxHealth()));
            sb.append("\n  food: ").append(live.getFoodData().getFoodLevel()).append("/20");
            if (live.isDegraded()) {
                sb.append("\n  lastError: ").append(live.lastTickError());
            }
        } else {
            sb.append("\n  state: ").append(ent.diedAt() > 0L ? "dead (awaiting respawn)" : "dormant");
            sb.append("\n  lastPos: ").append(ent.pos().toShortString())
                    .append(" @ ").append(ent.dimension().location());
            if (ent.diedAt() > 0L) {
                sb.append("\n  deathCause: ").append(ent.deathCause());
            }
        }
        String out = sb.toString();
        src.sendSuccess(() -> Component.literal(out), false);
        return 1;
    }

    private static int summon(CommandContext<CommandSourceStack> ctx, String name)
            throws CommandSyntaxException {
        ServerPlayer owner = ctx.getSource().getPlayerOrException();
        ServerLevel level = (ServerLevel) owner.level();
        NumenPlayer body = Companions.summon(
                level.getServer(), owner.getUUID(), name, level, owner.position());
        // Push the updated roster so the owner's G panel can reach the new companion.
        Companions.syncRosterToOwner(level.getServer(), owner);
        ctx.getSource().sendSuccess(() ->
                Component.literal("Summoned companion '" + name + "' (" + body.getUUID() + ")"), false);
        return 1;
    }

    private static int despawn(CommandContext<CommandSourceStack> ctx, String name)
            throws CommandSyntaxException {
        ServerPlayer owner = ctx.getSource().getPlayerOrException();
        var server = owner.level().getServer();
        // Permanent dismissal: removes the live body AND its registry entry (and any same-name
        // duplicates), so it does NOT come back on the next login. NOT dormancy.
        int dismissed = Companions.dismissByName(server, owner.getUUID(), name);
        if (dismissed == 0) {
            ctx.getSource().sendFailure(
                    Component.literal("No companion of yours named '" + name + "'"));
            return 0;
        }
        Companions.syncRosterToOwner(server, owner);
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Dismissed companion '" + name + "' — gone for good"
                        + (dismissed > 1 ? " (cleaned up " + dismissed + " duplicates)" : "")), false);
        return dismissed;
    }

    private static int clientAction(CommandContext<CommandSourceStack> ctx,
                                    ClientUiActionPayload.Action action)
            throws CommandSyntaxException {
        ServerPlayer caller = ctx.getSource().getPlayerOrException();
        Services.NETWORK.sendToPlayer(caller, new ClientUiActionPayload(action));
        return 1;
    }
}
