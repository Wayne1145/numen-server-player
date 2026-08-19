package com.dwinovo.numen.core.task;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** goto 的搜索抵达不能早于身体物理稳定。 */
class MoveToArrivalSettlementTest {

    @Test
    void searchArrivalWaitsWhileTaskLevelArrivalIsStillFalse() {
        assertEquals(com.dwinovo.numen.task.TaskState.RUNNING,
                MoveToCompanionTask.settledArrivalState(false));
    }

    @Test
    void searchArrivalCompletesAfterTaskLevelArrivalBecomesTrue() {
        assertEquals(com.dwinovo.numen.task.TaskState.SUCCESS,
                MoveToCompanionTask.settledArrivalState(true));
    }

    @Test
    void goalCellIsNotStableWhileStillFalling() {
        assertEquals(false, MoveToCompanionTask.stablePhysicalArrival(false, false, false));
        assertEquals(true, MoveToCompanionTask.stablePhysicalArrival(true, false, false));
        assertEquals(true, MoveToCompanionTask.stablePhysicalArrival(false, true, false));
        assertEquals(true, MoveToCompanionTask.stablePhysicalArrival(false, false, true));
    }
}
