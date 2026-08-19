package com.dwinovo.numen.core.task.survival;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 怪物逃生失败后的安全收束。 */
class ThreatEscapePolicyTest {
    @Test
    void threeFailedFleeRoutesRequireDormancy() {
        assertFalse(ThreatEscapePolicy.shouldDormant(2));
        assertTrue(ThreatEscapePolicy.shouldDormant(3));
    }
}
