// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RateLimiterTest {

    @Test
    void allowsUpToLimitThenBlocksWithinWindow() {
        RateLimiter r = new RateLimiter(3, 1000);
        assertTrue(r.tryAcquire("k", 0));
        assertTrue(r.tryAcquire("k", 100));
        assertTrue(r.tryAcquire("k", 200));
        assertFalse(r.tryAcquire("k", 300), "4th within the window is blocked");
    }

    @Test
    void windowResets() {
        RateLimiter r = new RateLimiter(2, 1000);
        assertTrue(r.tryAcquire("k", 0));
        assertTrue(r.tryAcquire("k", 500));
        assertFalse(r.tryAcquire("k", 900));
        assertTrue(r.tryAcquire("k", 1000), "new window at +1000ms");
    }

    @Test
    void keysAreIndependent() {
        RateLimiter r = new RateLimiter(1, 1000);
        assertTrue(r.tryAcquire("a", 0));
        assertFalse(r.tryAcquire("a", 10));
        assertTrue(r.tryAcquire("b", 10), "different key has its own budget");
    }

    @Test
    void zeroOrNegativeMeansUnlimited() {
        RateLimiter r = new RateLimiter(0, 1000);
        for (int i = 0; i < 100; i++) assertTrue(r.tryAcquire("k", i));
    }
}
