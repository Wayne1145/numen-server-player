package com.dwinovo.numen.core.task;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 回归钉桩：侧前方已在交互距离内的目标必须直接挖，不能绕去远处同类目标。 */
class MineCompanionTaskDirectTargetTest {

    @Test
    void lateralVisibleTargetIsEligible() {
        // 正式服复现：工作台在身体侧前方 1.33 格，且无遮挡。
        assertTrue(MineCompanionTask.eligibleDirectTarget(true, true));
    }

    @Test
    void outOfReachOrOccludedTargetStillNeedsNavigation() {
        assertFalse(MineCompanionTask.eligibleDirectTarget(false, true));
        assertFalse(MineCompanionTask.eligibleDirectTarget(true, false));
    }
}
