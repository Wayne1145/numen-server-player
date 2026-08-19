package com.dwinovo.numen.core.act;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 正式服回归：原版破坏提交被保护插件取消时，不能伪报为已经挖掉。 */
class BlockDiggerCommitPolicyTest {

    @Test
    void unchangedWorldStateMeansTheBreakWasRejected() {
        Object iron = new Object();
        assertFalse(BlockDigger.blockTypeChanged(iron, iron));
    }

    @Test
    void changedWorldStateConfirmsTheBreakBeforeClassifyingIt() {
        Object iron = new Object();
        Object air = new Object();
        assertTrue(BlockDigger.blockTypeChanged(iron, air));
        assertEquals(BlockDigger.DigResult.BROKE_TARGET,
                BlockDigger.committedResult(true));
        assertEquals(BlockDigger.DigResult.BROKE_OCCLUDER,
                BlockDigger.committedResult(false));
    }
}