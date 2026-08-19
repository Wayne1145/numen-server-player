package com.dwinovo.numen.core.task;

import com.dwinovo.numen.task.TaskState;

import com.dwinovo.numen.entity.NumenPlayer;
import com.dwinovo.numen.core.task.base.AbstractCompanionTask;
import com.dwinovo.numen.core.task.base.Precondition;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** {@code drop_items} on the player body — toss items forward, natively. One-tick. */
public final class DropCompanionTask extends AbstractCompanionTask<DropItemsTaskRecord> {

    /**
     * 满背包腾格时，给主动丢弃物一分钟的原生拾取冷却。
     *
     * <p>普通 {@code Player.drop} 的冷却只有约两秒。外部大脑下一轮决策往往比它更慢，
     * 尤其在狭窄矿道里，物品撞墙落回脚边后会被同一个假玩家立即捡回，导致“丢弃成功”
     * 与“背包仍满”无限互相抵消。这里只延长满背包场景；非满背包递给主人时仍保留原版
     * 短冷却。物品仍是原生 ItemEntity，会掉落、受事件影响并在五分钟后正常消失。
     */
    static final int FULL_INVENTORY_PICKUP_DELAY_TICKS = 20 * 60;

    private int dropped;
    private String doneMessage = "done";

    public DropCompanionTask(NumenPlayer player, DropItemsTaskRecord record) {
        super(player, record);
    }

    @Override
    protected List<Precondition> preconditions() {
        return List.of(
                () -> player.level() instanceof ServerLevel ? null
                        : new Precondition.Failure("not on a server level", FailureType.UNKNOWN),
                () -> PlayerInv.count(player.getInventory(), r.item) > 0 ? null
                        : new Precondition.Failure("no " + r.label + " in inventory to drop",
                                FailureType.NO_MATERIAL));
    }

    @Override
    protected void onStart() {
        Inventory inv = player.getInventory();
        // 必须在移除物品前判断；移除后本次操作自己就会制造空槽。
        boolean inventoryWasFull = inv.getFreeSlot() < 0;
        int have = PlayerInv.count(inv, r.item);
        dropped = Math.min(r.count, have);
        PlayerInv.remove(inv, r.item, dropped);

        // Toss like a real player: native Player.drop(stack, false) throws each stack in the facing
        // direction with vanilla motion + pickup delay and fires the drop event (mods watching item
        // tosses see it) — instead of hand-building an ItemEntity with a made-up velocity.
        int max = new ItemStack(r.item).getMaxStackSize();
        int remaining = dropped;
        while (remaining > 0) {
            int lump = Math.min(remaining, max);
            remaining -= lump;
            ItemEntity droppedEntity = player.drop(new ItemStack(r.item, lump), false);
            int extendedDelay = extendedPickupDelay(inventoryWasFull);
            if (extendedDelay > 0 && droppedEntity != null) {
                droppedEntity.setPickUpDelay(extendedDelay);
            }
        }
        doneMessage = "dropped " + dropped + "x " + r.label
                + (dropped < r.count ? " (only had " + dropped + ")" : "");
        succeed();   // work is done — finalize this same tick, before a Stop can mislabel it
    }

    @Override
    protected TaskState onTick() {
        return TaskState.SUCCESS;
    }

    /** No nav / overlay to release. */
    @Override
    protected void cleanup() {}

    @Override
    protected Map<String, Object> resultData() {
        Map<String, Object> data = new HashMap<>();
        data.put("item", r.label);
        data.put("dropped", dropped);
        data.put("remaining_in_inventory", PlayerInv.count(player.getInventory(), r.item));
        return data;
    }

    @Override
    protected String successMessage() {
        return doneMessage;
    }

    @Override
    protected String timeoutMessage() {
        return "drop timed out unexpectedly";
    }

    @Override
    protected String cancelledMessage() {
        return "drop interrupted";
    }

    /** 纯策略钉桩：只有满背包腾格才覆盖原版拾取冷却。 */
    static int extendedPickupDelay(boolean inventoryWasFull) {
        return inventoryWasFull ? FULL_INVENTORY_PICKUP_DELAY_TICKS : 0;
    }
}
