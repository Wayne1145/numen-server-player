// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.entity;

/**
 * Pure, unit-testable accounting for a companion body's per-tick failures.
 *
 * <p>Replaces the old {@code try { doTick(); } catch (Exception ignored) {}} in
 * {@link NumenPlayer#tick()} which silently swallowed every exception. This class
 * decides two things without any Minecraft dependency (so it can be unit-tested):
 * <ul>
 *   <li><b>when to log</b> — rate-limited: the first error in a run always logs, then
 *       at most once per {@code logIntervalTicks} — so a body NPEing every tick can't
 *       flood the log;</li>
 *   <li><b>when to degrade</b> — after {@code degradeThreshold} <em>consecutive</em>
 *       failed ticks it latches a DEGRADED state exactly once, so the caller can pause
 *       the companion and stop its tasks instead of crashing the server.</li>
 * </ul>
 * A successful tick clears the consecutive run (but does not un-latch a degraded state —
 * that requires an explicit {@link #reset()}, i.e. a deliberate recovery/respawn).
 */
public final class TickWatchdog {

    /** Outcome of recording one failed tick. */
    public record Decision(boolean shouldLog, boolean justDegraded) {}

    private final int degradeThreshold;
    private final long logIntervalTicks;

    private int consecutive;
    private long lastLoggedTick = Long.MIN_VALUE;
    private boolean degraded;
    private String lastError = "";

    /**
     * @param degradeThreshold consecutive failed ticks before the body is flipped to degraded (min 1)
     * @param logIntervalTicks minimum game-ticks between logged errors while failing (min 1)
     */
    public TickWatchdog(int degradeThreshold, long logIntervalTicks) {
        this.degradeThreshold = Math.max(1, degradeThreshold);
        this.logIntervalTicks = Math.max(1, logIntervalTicks);
    }

    /** A tick pass succeeded — clears the consecutive-error run. Does NOT un-latch a degraded state. */
    public void recordSuccess() {
        consecutive = 0;
    }

    /**
     * A tick pass threw. Records it and returns whether the caller should log this one
     * (rate-limited) and whether this error just crossed the body into degraded.
     *
     * @param errorLabel short description of the failure (exception type + message)
     * @param currentTick a monotonic game-tick used only for rate-limiting the log
     */
    public Decision recordError(String errorLabel, long currentTick) {
        lastError = errorLabel == null ? "" : errorLabel;
        consecutive++;
        boolean shouldLog = lastLoggedTick == Long.MIN_VALUE
                || currentTick - lastLoggedTick >= logIntervalTicks;
        boolean justDegraded = !degraded && consecutive >= degradeThreshold;
        if (justDegraded) {
            degraded = true;
            shouldLog = true;   // always surface the transition into degraded
        }
        if (shouldLog) {
            lastLoggedTick = currentTick;
        }
        return new Decision(shouldLog, justDegraded);
    }

    public boolean isDegraded() {
        return degraded;
    }

    public int consecutiveErrors() {
        return consecutive;
    }

    public String lastError() {
        return lastError;
    }

    /** Clear everything — a deliberate recovery (e.g. respawn / admin recover). */
    public void reset() {
        consecutive = 0;
        degraded = false;
        lastError = "";
        lastLoggedTick = Long.MIN_VALUE;
    }
}
