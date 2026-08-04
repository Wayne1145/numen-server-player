// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.util.UUID;

/**
 * A write-control lease over one companion — at most one live lease per companion, so only one
 * driver moves a body at a time. Carries a monotonic {@code fencingToken} so a late request from a
 * superseded holder can be rejected.
 *
 * <p><b>S2 scope:</b> single-writer acquire/release + validity check are enforced (see
 * {@link LeaseRegistry}). Fencing-token stale-rejection, TTL auto-release timers, contention
 * adversarial tests, and the readonly/rate-limit/audit wrapping are S3.
 */
public record Lease(String leaseId,
                    UUID companionId,
                    String principalId,
                    long fencingToken,
                    long issuedAtMs,
                    long expiresAtMs) {

    public boolean isExpired(long nowMs) {
        return nowMs >= expiresAtMs;
    }
}
