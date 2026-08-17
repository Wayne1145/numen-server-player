package com.dwinovo.numen.core.task;

import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 回归钉桩:sameColumnReachableWindow 的几何语义。
 *
 * <p>历史 bug(2026-08-17 正式服实测):reachableTarget() 用
 * {@code ore.getY() < feet.getY() → continue} 把脚下的矿直接排除,
 * 导致假人导航到矿脉正上方后认为该矿不可挖 → stance dud → 拉黑 →
 * 走向下一个矿,全程 0 收集。本测试把"同柱可达窗口"抽成纯函数锁定:
 * 脚下 1–2 格必须可挖(站在矿脉顶上低头挖),再深必须交给寻路,不能盲挖。
 */
class MineCompanionTaskReachWindowTest {

    private static final BlockPos FEET = new BlockPos(10, 64, -20);

    @Test
    void oreDirectlyUnderFeetIsReachable() {
        // 回归核心:假人站在矿顶(矿在脚下一格),必须能原地挖。
        assertTrue(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(10, 63, -20)));
    }

    @Test
    void oreTwoBelowFeetIsReachable() {
        assertTrue(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(10, 62, -20)));
    }

    @Test
    void oreAtFeetLevelIsReachable() {
        assertTrue(MineCompanionTask.sameColumnReachableWindow(FEET, FEET));
    }

    @Test
    void oreOneAboveFeetIsReachable() {
        assertTrue(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(10, 65, -20)));
    }

    @Test
    void oreThreeBelowFeetIsNotInPlaceMineable() {
        // 再深不能原地盲挖:必须由 composite ore-field goal 开竖井接近。
        assertFalse(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(10, 61, -20)));
    }

    @Test
    void oreInAdjacentColumnIsNotInPlaceMineable() {
        assertFalse(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(11, 63, -20)));
        assertFalse(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(10, 63, -19)));
    }

    @Test
    void columnMatchIsRequiredEvenInReach() {
        assertFalse(MineCompanionTask.sameColumnReachableWindow(FEET, new BlockPos(12, 64, -20)));
    }
}
