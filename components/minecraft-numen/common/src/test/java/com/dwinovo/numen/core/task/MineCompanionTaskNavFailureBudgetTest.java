package com.dwinovo.numen.core.task;

import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 回归钉桩：封闭矿层不能对大量等价候选逐个重复 NO_PATH。 */
class MineCompanionTaskNavFailureBudgetTest {

    @Test
    void failuresInSameLocalAreaAccumulate() {
        BlockPos anchor = new BlockPos(-138, -11, -141);
        assertEquals(4, MineCompanionTask.nextLocalNavFailureCount(
                3, anchor, new BlockPos(-137, -11, -140)));
    }

    @Test
    void movingToNewAreaResetsBudget() {
        BlockPos anchor = new BlockPos(-138, -11, -141);
        assertEquals(1, MineCompanionTask.nextLocalNavFailureCount(
                5, anchor, new BlockPos(-132, -11, -141)));
    }

    @Test
    void boundedFailuresAbortBeforeAllSixtyFourCandidatesAreTried() {
        assertFalse(MineCompanionTask.shouldAbortLocalNavFailures(5));
        assertTrue(MineCompanionTask.shouldAbortLocalNavFailures(6));
        assertTrue(MineCompanionTask.shouldAbortLocalNavFailures(64));
    }
}
