// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AuditLogLevelPolicyTest {

    private static AuditLog.Record record(String tool, boolean success) {
        return new AuditLog.Record(1L, "mcp-admin", "actuator", "companion",
                tool, null, null, 0L, success, success ? "ok" : "failed");
    }

    @Test
    void successfulHealthPollsDoNotNeedInfoConsoleNoise() {
        assertTrue(AuditLog.isRoutineSuccessfulRead(record("list_companions", true)));
        assertTrue(AuditLog.isRoutineSuccessfulRead(record("get_recent_events", true)));
        assertTrue(AuditLog.isRoutineSuccessfulRead(record("get_self_status", true)));
        assertTrue(AuditLog.isRoutineSuccessfulRead(record("task_status", true)));
    }

    @Test
    void failuresAndActionsRemainOperatorVisible() {
        assertFalse(AuditLog.isRoutineSuccessfulRead(record("get_recent_events", false)));
        assertFalse(AuditLog.isRoutineSuccessfulRead(record("mine", true)));
        assertFalse(AuditLog.isRoutineSuccessfulRead(record("send_chat", true)));
    }
}