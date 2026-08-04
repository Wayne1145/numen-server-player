// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.entity;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Unit tests for the pure tick-failure watchdog that replaced NumenPlayer's silent catch. */
class TickWatchdogTest {

    @Test
    void belowThresholdNeverDegrades() {
        TickWatchdog w = new TickWatchdog(5, 100);
        for (int i = 0; i < 4; i++) {
            assertFalse(w.recordError("boom", i).justDegraded());
        }
        assertFalse(w.isDegraded());
        assertEquals(4, w.consecutiveErrors());
    }

    @Test
    void degradesExactlyOnceAtThreshold() {
        TickWatchdog w = new TickWatchdog(3, 100);
        assertFalse(w.recordError("a", 0).justDegraded());
        assertFalse(w.recordError("b", 1).justDegraded());
        TickWatchdog.Decision third = w.recordError("c", 2);
        assertTrue(third.justDegraded(), "crosses into degraded on the 3rd consecutive error");
        assertTrue(third.shouldLog(), "the transition into degraded always logs");
        assertTrue(w.isDegraded());
        // Still degraded, but justDegraded only fires on the transition edge.
        assertFalse(w.recordError("d", 3).justDegraded());
        assertTrue(w.isDegraded());
    }

    @Test
    void successResetsConsecutiveRun() {
        TickWatchdog w = new TickWatchdog(3, 100);
        w.recordError("a", 0);
        w.recordError("b", 1);
        w.recordSuccess();
        assertEquals(0, w.consecutiveErrors());
        assertFalse(w.recordError("c", 2).justDegraded(), "only 1 error since the reset");
        assertFalse(w.isDegraded());
    }

    @Test
    void successDoesNotUnlatchDegraded() {
        TickWatchdog w = new TickWatchdog(1, 100);
        assertTrue(w.recordError("boom", 0).justDegraded());
        w.recordSuccess();
        assertTrue(w.isDegraded(), "degraded latches until an explicit reset()");
    }

    @Test
    void loggingIsRateLimited() {
        TickWatchdog w = new TickWatchdog(1000, 100);   // high degrade threshold to isolate logging
        assertTrue(w.recordError("e", 0).shouldLog(), "first error always logs");
        assertFalse(w.recordError("e", 50).shouldLog(), "within the interval: suppressed");
        assertFalse(w.recordError("e", 99).shouldLog());
        assertTrue(w.recordError("e", 100).shouldLog(), "interval elapsed: logs again");
        assertFalse(w.recordError("e", 150).shouldLog());
    }

    @Test
    void resetClearsEverything() {
        TickWatchdog w = new TickWatchdog(1, 100);
        w.recordError("boom", 0);
        assertTrue(w.isDegraded());
        w.reset();
        assertFalse(w.isDegraded());
        assertEquals(0, w.consecutiveErrors());
        assertEquals("", w.lastError());
        assertTrue(w.recordError("x", 5).shouldLog(), "after reset, the first error logs again");
    }

    @Test
    void lastErrorTracks() {
        TickWatchdog w = new TickWatchdog(5, 100);
        w.recordError("NullPointerException: foo", 0);
        assertEquals("NullPointerException: foo", w.lastError());
    }
}
