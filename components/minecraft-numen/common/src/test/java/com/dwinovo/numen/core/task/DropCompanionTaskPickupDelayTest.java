package com.dwinovo.numen.core.task;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** 回归钉桩：满背包丢垃圾后不能在下一轮模型决策前被自己立即捡回。 */
class DropCompanionTaskPickupDelayTest {

    @Test
    void fullInventoryGetsOneMinuteNativePickupDelay() {
        assertEquals(20 * 60, DropCompanionTask.extendedPickupDelay(true));
    }

    @Test
    void nonFullInventoryKeepsVanillaDropDelay() {
        assertEquals(0, DropCompanionTask.extendedPickupDelay(false));
    }
}