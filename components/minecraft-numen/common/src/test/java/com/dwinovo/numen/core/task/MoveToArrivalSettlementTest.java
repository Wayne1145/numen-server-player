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
}
