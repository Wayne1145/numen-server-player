// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class IdempotentDispatchRegistryTest {

    @Test
    void recordThenLookupReturnsSameTaskId() {
        UUID companion = UUID.randomUUID();
        IdempotentDispatchRegistry.record(companion, "act-1", "t7");
        assertEquals("t7", IdempotentDispatchRegistry.lookup(companion, "act-1"));
    }

    @Test
    void unknownActionReturnsNull() {
        assertNull(IdempotentDispatchRegistry.lookup(UUID.randomUUID(), "missing"));
        assertNull(IdempotentDispatchRegistry.lookup(UUID.randomUUID(), null));
    }

    @Test
    void companionsAreIsolated() {
        UUID one = UUID.randomUUID();
        UUID two = UUID.randomUUID();
        IdempotentDispatchRegistry.record(one, "act-1", "t1");
        assertNull(IdempotentDispatchRegistry.lookup(two, "act-1"));
    }

    @Test
    void evictsOldestBeyondPerCompanionCap() {
        UUID companion = UUID.randomUUID();
        for (int i = 0; i < 130; i++) {
            IdempotentDispatchRegistry.record(companion, "act-" + i, "t" + i);
        }
        assertNull(IdempotentDispatchRegistry.lookup(companion, "act-0"), "最旧应被淘汰");
        assertEquals("t129", IdempotentDispatchRegistry.lookup(companion, "act-129"));
    }

    @Test
    void clearRemovesCompanionEntries() {
        UUID companion = UUID.randomUUID();
        IdempotentDispatchRegistry.record(companion, "act-1", "t1");
        IdempotentDispatchRegistry.clear(companion);
        assertNull(IdempotentDispatchRegistry.lookup(companion, "act-1"));
    }
}
