package com.dwinovo.numen.core.task.base;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 回归钉桩：坑底掉落的近距判断必须与 collect_items 共用且边界稳定。 */
class NativeItemPickupTest {

    @Test
    void observedPitDropAtDistanceSquaredAboutOnePointThreeSixIsInReach() {
        // 隔离实测距离 1.166 格，平方约 1.36；旧逻辑却没有触发原生碰撞拾取。
        assertTrue(NativeItemPickup.withinReach(1.36));
    }

    @Test
    void exactBoundaryIsIncludedButOutsideIsRejected() {
        assertTrue(NativeItemPickup.withinReach(1.5));
        assertFalse(NativeItemPickup.withinReach(1.500001));
    }
}