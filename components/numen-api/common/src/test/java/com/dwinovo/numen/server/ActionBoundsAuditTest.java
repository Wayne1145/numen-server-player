// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
import org.junit.jupiter.api.Test;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
class ActionBoundsAuditTest {
    @Test void limitsActualMovementAndDimension() {
        UUID id=UUID.randomUUID();
        ActionBounds.register(id,"minecraft:overworld",0,0,0,64);
        assertFalse(ActionBounds.violates(id,"minecraft:overworld",64,0,0));
        assertTrue(ActionBounds.violates(id,"minecraft:overworld",65,0,0));
        assertTrue(ActionBounds.violates(id,"minecraft:the_nether",0,0,0));
        ActionBounds.clear(id);
        assertFalse(ActionBounds.violates(id,"minecraft:overworld",1000,0,0));
    }
    @Test void lateStopCannotCancelNewTask() {
        assertFalse(TaskStopGuard.matches("t1","t2"));
        assertFalse(TaskStopGuard.matches("t1",null));
        assertTrue(TaskStopGuard.matches(null,"t2"));
        assertTrue(TaskStopGuard.matches("t2","t2"));
    }
}
