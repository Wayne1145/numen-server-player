package com.dwinovo.numen.entity;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 任务停止时沿用原版潜行语义：在梯子/藤蔓上刹车，平地则松开。 */
class InputDriverStopPolicyTest {

    @Test
    void stoppedBodyOnlyHoldsSneakWhileClimbable() {
        assertTrue(InputDriver.shouldBrakeOnClimbable(true));
        assertFalse(InputDriver.shouldBrakeOnClimbable(false));
    }
}