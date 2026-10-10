// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AuthTokensTest {

    @Test
    void resolvesKnownTokensOnly() {
        UUID owner = UUID.randomUUID();
        AuthTokens t = new AuthTokens(Map.of(
                "tok-admin", McpPrincipal.admin("root"),
                "tok-owner", McpPrincipal.owner("alice", owner)));
        assertEquals("root", t.resolve("tok-admin").id());
        assertEquals(McpPrincipal.Role.OWNER, t.resolve("tok-owner").role());
        assertEquals(owner, t.resolve("tok-owner").ownerUuid());
        assertNull(t.resolve("tok-nope"), "unknown token → null (no principal)");
        assertNull(t.resolve(null));
        assertNull(t.resolve(""));
    }

    @Test
    void emptyStoreResolvesNothing() {
        AuthTokens t = AuthTokens.empty();
        assertTrue(t.isEmpty());
        assertNull(t.resolve("anything"));
    }
}
