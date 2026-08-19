package com.dwinovo.numen.core.task;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 实服回归：采集任务不能在濒死或无处接收掉落时继续破坏方块。 */
class MineCompanionTaskSafetyTest {

    @Test
    void significantHealthLossAbortsBeforeASecondDangerousHit() {
        // 正式服 t3 首次可见受伤是 22→14；此时必须停，不能再承受第二次 8 点伤害。
        assertTrue(MineCompanionTask.unsafeHealth(22.0f, 14.0f));
        assertFalse(MineCompanionTask.unsafeHealth(22.0f, 20.0f));
    }

    @Test
    void alreadyLowHealthCannotRestartTheSameMiningTask() {
        assertTrue(MineCompanionTask.unsafeHealth(6.0f, 6.0f));
        assertFalse(MineCompanionTask.unsafeHealth(22.0f, 18.0f));
    }

    @Test
    void miningWithoutAMatchingStackReservesOneRouteByproductSlot() {
        assertFalse(MineCompanionTask.canAcceptDrops(-1, false));
        assertFalse(MineCompanionTask.canAcceptDrops(0, false));
        assertFalse(MineCompanionTask.canAcceptDrops(1, false));
        assertTrue(MineCompanionTask.canAcceptDrops(2, false));
        assertTrue(MineCompanionTask.canAcceptDrops(-1, true));
    }

    @Test
    void navigationWithoutMovementOrDiggingHasABoundedStallWindow() {
        assertFalse(MineCompanionTask.navigationStalled(199));
        assertTrue(MineCompanionTask.navigationStalled(200));
    }

    @Test
    void reachableOreCannotInterruptNavigationWhileATargetDropIsPending() {
        assertFalse(MineCompanionTask.shouldInterruptNavigation(true, true));
        assertTrue(MineCompanionTask.shouldInterruptNavigation(false, true));
        assertFalse(MineCompanionTask.shouldInterruptNavigation(false, false));
    }
}
