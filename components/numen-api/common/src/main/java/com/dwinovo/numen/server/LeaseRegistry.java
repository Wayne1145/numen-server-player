// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Server-authoritative write-control leases: at most one live lease per companion. All operations
 * are pure (a millisecond clock is passed in, never read here) so the core is unit-testable.
 *
 * <p><b>S2 scope:</b> single-writer acquire (reject if another principal holds an unexpired lease),
 * release, and write-validity check with a monotonic fencing token. <b>S3 adds:</b> a background
 * TTL sweeper that auto-releases expired leases, fencing-token stale-request rejection at the call
 * sites, readonly enforcement, rate limiting, audit, and death/delete/shutdown release wiring.
 */
public final class LeaseRegistry {

    private final Map<UUID, Lease> byCompanion = new ConcurrentHashMap<>();
    private final AtomicLong fencing = new AtomicLong();

    /**
     * Acquire (or renew) the write lease for {@code companionId}. Returns a new lease, or {@code null}
     * if a <em>different</em> principal already holds an unexpired lease (contention). The same
     * principal re-acquiring gets a fresh lease with a higher fencing token (renewal).
     */
    public Lease acquire(McpPrincipal principal, UUID companionId, long nowMs, long ttlMs) {
        Lease current = byCompanion.get(companionId);
        if (current != null && !current.isExpired(nowMs) && !current.principalId().equals(principal.id())) {
            return null;   // held by someone else — contention
        }
        Lease lease = new Lease(UUID.randomUUID().toString(), companionId, principal.id(),
                fencing.incrementAndGet(), nowMs, nowMs + ttlMs);
        byCompanion.put(companionId, lease);
        return lease;
    }

    /** Release a lease if it is the current one held by this principal. Idempotent; returns true if released. */
    public boolean release(UUID companionId, String principalId, String leaseId) {
        Lease current = byCompanion.get(companionId);
        if (current != null && current.leaseId().equals(leaseId) && current.principalId().equals(principalId)) {
            byCompanion.remove(companionId);
            return true;
        }
        return false;
    }

    /** True if {@code leaseId} is the live, unexpired lease held by {@code principal} over {@code companionId}. */
    public boolean isValidForWrite(UUID companionId, McpPrincipal principal, String leaseId, long nowMs) {
        if (leaseId == null) return false;
        Lease current = byCompanion.get(companionId);
        return current != null
                && !current.isExpired(nowMs)
                && current.leaseId().equals(leaseId)
                && current.principalId().equals(principal.id());
    }

    /** The current lease over a companion, or {@code null}. */
    public Lease current(UUID companionId) {
        return byCompanion.get(companionId);
    }

    /** Drop any lease for a companion — called on death / delete / shutdown. */
    public void releaseAll(UUID companionId) {
        byCompanion.remove(companionId);
    }

    /**
     * Remove every lease that has expired as of {@code nowMs} and return the affected companion ids.
     * Called periodically (server tick) so a driver that acquired control then vanished (crashed
     * client, dropped MCP session, plain timeout) does not hold a companion hostage forever.
     */
    public java.util.List<UUID> sweepExpired(long nowMs) {
        java.util.List<UUID> released = new java.util.ArrayList<>();
        for (Map.Entry<UUID, Lease> e : byCompanion.entrySet()) {
            if (e.getValue().isExpired(nowMs)) released.add(e.getKey());
        }
        for (UUID id : released) {
            byCompanion.remove(id);
        }
        return released;
    }
}
