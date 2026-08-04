package com.dwinovo.numen.entity;

import com.mojang.authlib.GameProfile;
import net.minecraft.core.UUIDUtil;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

import java.util.UUID;

/**
 * The companion body: a server-side fake {@link ServerPlayer}. Replaces the old
 * custom {@code NumenEntity} Mob so the companion is a first-class player —
 * native interaction/combat code paths (universal mod compatibility), its own
 * player inventory, and free chunk loading + playerdata persistence by virtue of
 * being a list-resident player.
 *
 * <h2>Identity &amp; ownership</h2>
 * Created by {@link CompanionFactory} with a stable per-companion UUID (carried
 * in the {@link GameProfile}); the enumerable index lives in
 * {@link CompanionRegistry}. Unlike the Mob, a fake player cannot carry custom
 * {@code SynchedEntityData}, so the owner is a plain server-side field persisted
 * to the companion's own playerdata {@code .dat} via
 * {@link #addAdditionalSaveData}. Owner checks are UUID comparisons — never
 * vanilla {@code isOwnedBy} (which resolves through a level and breaks across
 * dimensions).
 */
public final class NumenPlayer extends ServerPlayer {

    private static final String NBT_KEY_OWNER = "NumenOwner";

    /** Owner's player UUID. Null only transiently before the first assignment. */
    private UUID ownerUuid;

    /** Latched once we've handled this body's death, so the post-death routine runs exactly once. */
    private boolean deathHandled;

    /** Consecutive failed ticks before a body is paused (degraded) — ~1s of solid failures. */
    private static final int TICK_DEGRADE_THRESHOLD = 20;
    /** Min ticks between logged tick errors while failing (~5s), so a broken body can't flood the log. */
    private static final long TICK_LOG_INTERVAL = 100L;

    /** Rate-limited tick-failure accounting — replaces the old silent {@code catch (Exception ignored)}. */
    private final TickWatchdog tickWatchdog = new TickWatchdog(TICK_DEGRADE_THRESHOLD, TICK_LOG_INTERVAL);

    public NumenPlayer(MinecraftServer server, ServerLevel level, GameProfile profile) {
        super(server, level, profile);   // 1.20.1: no ClientInformation (pre-configuration-phase)
    }

    /**
     * 点亮全部皮肤覆盖层(帽子/夹克/左右袖/左右裤腿)与披风。假玩家没有客户端上报的
     * 模型定制,不设这个字节客户端只渲染单层基础皮肤。该字节是同步实体数据、不随 .dat
     * 存取,故每次进世界都要重设一次(经 {@code protected} 的 DATA_PLAYER_MODE_CUSTOMISATION
     * 访问,子类内可见)。
     */
    public void showAllSkinLayers() {
        getEntityData().set(DATA_PLAYER_MODE_CUSTOMISATION, (byte) 0x7f);
    }

    /** The loaded companion body with this UUID, or {@code null} if not spawned. */
    public static NumenPlayer findByUuid(MinecraftServer server, UUID uuid) {
        return server.getPlayerList().getPlayer(uuid) instanceof NumenPlayer ap ? ap : null;
    }

    public UUID getOwnerUuid() {
        return ownerUuid;
    }

    public void setOwnerUuid(UUID ownerUuid) {
        this.ownerUuid = ownerUuid;
    }

    /** Cross-dimension safe owner check — UUID comparison, not level-scoped lookup. */
    public boolean isOwnedByPlayer(UUID playerUuid) {
        return ownerUuid != null && ownerUuid.equals(playerUuid);
    }

    /** The owner as an online player, server-wide; null when offline. */
    public ServerPlayer resolveOwnerPlayer() {
        return ownerUuid == null ? null : level().getServer().getPlayerList().getPlayer(ownerUuid);
    }


    /** True if {@code item} sits anywhere in the inventory (hotbar/main/offhand all count). */
    public boolean ensureInInventory(Item item) {
        var inv = getInventory();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            if (inv.getItem(i).is(item)) return true;
        }
        return false;
    }

    /**
     * Hold the item in inventory slot {@code slot} in the main hand the way a real player
     * does — a hotbar slot is simply SELECTED (number-key); a main-inventory slot is SWAPPED
     * into the currently selected hotbar slot (item-conserving). This is the only correct way
     * to "switch to hand": calling {@code setItemInHand(MAIN_HAND, stack)} overwrites the held
     * item (losing it) and aliases ONE {@link net.minecraft.world.item.ItemStack} across two
     * slots, which corrupts the inventory once the stack is consumed. No-op for {@code slot < 0}.
     */
    public void holdInHand(int slot) {
        if (slot < 0) {
            return;
        }
        var inv = getInventory();
        if (net.minecraft.world.entity.player.Inventory.isHotbarSlot(slot)) {
            inv.selected = slot;
            return;
        }
        int selected = inv.selected;
        net.minecraft.world.item.ItemStack held = inv.getItem(selected);
        inv.setItem(selected, inv.getItem(slot));
        inv.setItem(slot, held);
    }

    // ---- server tick (restore the movement pass a fake connection skips) ----

    /**
     * Drive the body's own movement physics. A real {@link ServerPlayer} runs
     * {@code travel} (against {@code zza}/{@code xxa}), food, air and pose inside
     * {@link #doTick()}, which the network layer invokes via
     * {@code connection.tick()}. A fake player's connection is a no-op, so
     * {@code doTick()} never fires and the body would only ever turn (a direct
     * {@code setYRot} write) without walking. The entity system already calls
     * {@code super.tick()} (menus / container / position sync), so we add the
     * missing {@code doTick()} movement pass here in our own {@code tick()}
     * override. Every 10 ticks we resync the
     * connection position and let chunk loading follow the body so it never
     * walks out of its loaded area.
     */
    @Override
    public void tick() {
        // A fake player isn't auto-removed on death (no client to send a respawn packet), so it would
        // sit at 0 HP forever. Detect death once, hand off to the recoverable-death routine (stop the
        // brain, schedule a respawn at the owner), and skip the normal movement/AI tick for this corpse.
        if (!deathHandled && (getHealth() <= 0.0f || isDeadOrDying())) {
            deathHandled = true;
            Companions.onDeath(this);
            return;
        }
        if (level() instanceof ServerLevel sl && sl.getGameTime() % 10 == 0) {
            this.connection.resetPosition();
            sl.getChunkSource().move(this);
        }
        super.tick();
        // A degraded body is PAUSED: it stays in-world (list-resident, persisted) but skips its
        // movement pass here and its tasks (see CompanionTickDispatcher.tick) so one broken
        // companion can neither flood the log nor wedge the server.
        if (tickWatchdog.isDegraded()) {
            return;
        }
        // Restore the movement pass a fake connection skips. Unlike the old silent
        // `catch (Exception ignored)`, failures are rate-limit-logged (companion UUID + exception
        // type) and, after a run of consecutive failures, flip the body to DEGRADED (paused, tasks
        // stopped, visible to get_self_status) — never hidden, never a whole-server crash.
        try {
            this.doTick();
            tickWatchdog.recordSuccess();
        } catch (Exception ex) {
            handleTickError(ex);
        }
    }

    /** Rate-limited logging + degrade-on-repeated-failure for {@link #tick()}'s movement pass. */
    private void handleTickError(Exception ex) {
        long tick = level() instanceof ServerLevel sl ? sl.getGameTime() : 0L;
        String label = ex.getClass().getName() + ": " + ex.getMessage();
        TickWatchdog.Decision d = tickWatchdog.recordError(label, tick);
        if (d.shouldLog()) {
            com.dwinovo.numen.Constants.LOG.warn(
                    "[numen] companion '{}' ({}) tick error #{}: {}",
                    getName().getString(), getUUID(), tickWatchdog.consecutiveErrors(), label, ex);
        }
        if (d.justDegraded()) {
            com.dwinovo.numen.Constants.LOG.error(
                    "[numen] companion '{}' ({}) is DEGRADED after {} consecutive tick errors — "
                            + "pausing it and stopping its tasks",
                    getName().getString(), getUUID(), tickWatchdog.consecutiveErrors());
            com.dwinovo.numen.task.CompanionTickDispatcher.stopTasksForDegraded(this);
        }
    }

    /** True if this body was paused after repeated tick failures (see {@link TickWatchdog}). */
    public boolean isDegraded() {
        return tickWatchdog.isDegraded();
    }

    /** Last tick-failure description (exception type + message), or {@code ""} if none. */
    public String lastTickError() {
        return tickWatchdog.lastError();
    }

    /** Current run of consecutive failed ticks. */
    public int consecutiveTickErrors() {
        return tickWatchdog.consecutiveErrors();
    }

    /** Clear a degraded state and let the body resume (admin recovery / fresh respawn). */
    public void clearDegraded() {
        tickWatchdog.reset();
    }

    @Override
    public void addAdditionalSaveData(CompoundTag output) {
        super.addAdditionalSaveData(output);
        if (ownerUuid != null) {
            output.putUUID(NBT_KEY_OWNER, ownerUuid);   // 1.21.4: no CompoundTag.store(Codec)
        }
    }

    @Override
    public void readAdditionalSaveData(CompoundTag input) {
        super.readAdditionalSaveData(input);
        if (input.hasUUID(NBT_KEY_OWNER)) this.ownerUuid = input.getUUID(NBT_KEY_OWNER);
    }
}
