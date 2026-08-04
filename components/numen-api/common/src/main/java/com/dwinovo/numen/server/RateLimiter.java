// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Fixed-window rate limiter keyed by an arbitrary string (per-principal, per-companion). Pure: the
 * clock is passed in, so it is fully unit-testable. {@code maxPerWindow <= 0} means unlimited.
 */
public final class RateLimiter {

    private final int maxPerWindow;
    private final long windowMs;
    private final Map<String, long[]> state = new ConcurrentHashMap<>();   // key -> [windowStartMs, count]

    public RateLimiter(int maxPerWindow, long windowMs) {
        this.maxPerWindow = maxPerWindow;
        this.windowMs = Math.max(1, windowMs);
    }

    /** Try to consume one unit for {@code key} at {@code nowMs}. True = allowed, false = over the limit. */
    public synchronized boolean tryAcquire(String key, long nowMs) {
        if (maxPerWindow <= 0) return true;
        long[] w = state.computeIfAbsent(key, k -> new long[]{nowMs, 0});
        if (nowMs - w[0] >= windowMs) {
            w[0] = nowMs;
            w[1] = 0;
        }
        if (w[1] >= maxPerWindow) return false;
        w[1]++;
        return true;
    }

    /** Current count in the active window for a key (for diagnostics). */
    public synchronized long count(String key, long nowMs) {
        long[] w = state.get(key);
        if (w == null || nowMs - w[0] >= windowMs) return 0;
        return w[1];
    }
}
