// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Unit tests for the single-writer control-lease core (S2 mechanism; S3 adds the adversarial suite). */
class LeaseRegistryTest {

    private final McpPrincipal a = McpPrincipal.admin("A");
    private final McpPrincipal b = McpPrincipal.admin("B");   // distinct id → distinct holder
    private final UUID comp = UUID.randomUUID();

    @Test
    void acquireThenContention() {
        LeaseRegistry r = new LeaseRegistry();
        Lease la = r.acquire(a, comp, 1000, 10000);
        assertNotNull(la);
        assertEquals("A", la.principalId());
        assertNull(r.acquire(b, comp, 2000, 10000), "different principal can't take a held lease");
        Lease la2 = r.acquire(a, comp, 3000, 10000);
        assertNotNull(la2, "same principal may renew");
        assertTrue(la2.fencingToken() > la.fencingToken(), "fencing token is monotonic");
    }

    @Test
    void validForWrite() {
        LeaseRegistry r = new LeaseRegistry();
        Lease la = r.acquire(a, comp, 1000, 10000);
        assertTrue(r.isValidForWrite(comp, a, la.leaseId(), 2000));
        assertFalse(r.isValidForWrite(comp, a, "wrong", 2000));
        assertFalse(r.isValidForWrite(comp, b, la.leaseId(), 2000), "lease belongs to A, not B");
        assertFalse(r.isValidForWrite(comp, a, la.leaseId(), 999999), "expired lease is invalid");
        assertFalse(r.isValidForWrite(comp, a, null, 2000));
    }

    @Test
    void releaseFreesForOthers() {
        LeaseRegistry r = new LeaseRegistry();
        Lease la = r.acquire(a, comp, 1000, 10000);
        assertNull(r.acquire(b, comp, 1100, 10000));
        assertTrue(r.release(comp, "A", la.leaseId()));
        Lease lb = r.acquire(b, comp, 1200, 10000);
        assertNotNull(lb);
        assertEquals("B", lb.principalId());
    }

    @Test
    void expiredLeaseYieldsToNewAcquirer() {
        LeaseRegistry r = new LeaseRegistry();
        r.acquire(a, comp, 1000, 5000);                 // expires at 6000
        Lease lb = r.acquire(b, comp, 7000, 5000);      // A expired → B may acquire
        assertNotNull(lb);
        assertEquals("B", lb.principalId());
    }

    @Test
    void releaseIsGuarded() {
        LeaseRegistry r = new LeaseRegistry();
        Lease la = r.acquire(a, comp, 1000, 10000);
        assertFalse(r.release(comp, "B", la.leaseId()), "wrong principal can't release");
        assertFalse(r.release(comp, "A", "wrong-lease"), "wrong leaseId can't release");
        assertTrue(r.release(comp, "A", la.leaseId()));
    }

    @Test
    void sweepExpiredReleasesTimedOutLeases() {
        LeaseRegistry r = new LeaseRegistry();
        Lease la = r.acquire(a, comp, 1000, 5000);          // expires at 6000
        assertTrue(r.sweepExpired(5000).isEmpty(), "not yet expired");
        assertNotNull(r.current(comp));
        var released = r.sweepExpired(6000);                // now expired
        assertEquals(1, released.size());
        assertEquals(comp, released.get(0));
        assertNull(r.current(comp), "swept away");
        assertTrue(r.isValidForWrite(comp, a, la.leaseId(), 6000) == false);
    }
}
