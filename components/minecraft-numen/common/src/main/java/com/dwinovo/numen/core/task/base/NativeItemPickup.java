package com.dwinovo.numen.core.task.base;

import com.dwinovo.numen.entity.NumenPlayer;
import net.minecraft.world.entity.item.ItemEntity;

/**
 * 掉落实体的原生近距拾取辅助。
 *
 * <p>服务端假玩家不会总能依赖普通客户端移动路径触发实体碰撞回调，尤其是物品
 * 落在脚下一格的坑底时。这里仅在与既有 collect_items 相同的近距阈值内调用
 * {@link ItemEntity#playerTouch}；拾取延迟、背包容量、事件与物品减少仍全部由
 * Minecraft 原生逻辑决定，不直接修改背包或移除实体。
 */
public final class NativeItemPickup {

    /** 与 CollectItemsTaskGoal 旧判据一致：distanceToSqr <= 1.5。 */
    static final double PICKUP_REACH_SQR = 1.5;

    private NativeItemPickup() {}

    public static boolean withinReach(double distanceSqr) {
        return distanceSqr <= PICKUP_REACH_SQR;
    }

    /**
     * 在原生近距范围内尝试一次 playerTouch。
     *
     * @return 本次调用后物品已移除或堆栈数量减少。
     */
    public static boolean tryPickup(NumenPlayer player, ItemEntity item) {
        if (player == null || item == null || item.isRemoved()
                || !withinReach(player.distanceToSqr(item))) {
            return false;
        }
        int before = item.getItem().getCount();
        item.playerTouch(player);
        return item.isRemoved() || item.getItem().getCount() < before;
    }
}
