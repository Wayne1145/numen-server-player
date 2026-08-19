package com.dwinovo.numen.core.task.base;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 所有身体任务共享的失血保险丝。 */
class TaskHealthFuseTest {

    @Test
    void cumulativeSixHpLossTripsTheFuse() {
        assertTrue(AbstractCompanionTask.unsafeTaskHealth(20.0f, 14.0f));
        assertTrue(AbstractCompanionTask.unsafeTaskHealth(10.0f, 7.0f));
    }

    @Test
    void smallLossAtSafeHealthDoesNotTrip() {
        assertFalse(AbstractCompanionTask.unsafeTaskHealth(20.0f, 16.0f));
    }

    @Test
    void currentHealthAtOrBelowEightAlwaysTrips() {
        assertTrue(AbstractCompanionTask.unsafeTaskHealth(20.0f, 8.0f));
    }
}
