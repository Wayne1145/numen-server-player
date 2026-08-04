// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.dwinovo.numen.entity.ServerOwner;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Unit tests for the principal authorization predicates. */
class McpPrincipalTest {

    @Test
    void adminControlsAndReadsEverything() {
        McpPrincipal p = McpPrincipal.admin("root");
        assertTrue(p.canControl(UUID.randomUUID()));
        assertTrue(p.canControl(ServerOwner.ID));
        assertTrue(p.canRead(UUID.randomUUID()));
        assertTrue(p.canManage());
    }

    @Test
    void ownerScopedToItsOwnUuid() {
        UUID mine = UUID.randomUUID();
        UUID other = UUID.randomUUID();
        McpPrincipal p = McpPrincipal.owner("u", mine);
        assertTrue(p.canControl(mine));
        assertFalse(p.canControl(other));
        assertFalse(p.canControl(ServerOwner.ID));
        assertTrue(p.canRead(mine));
        assertFalse(p.canRead(other));
        assertTrue(p.canManage());
    }

    @Test
    void readonlyReadsButCannotActOrManage() {
        McpPrincipal p = McpPrincipal.readonly("ro");
        assertFalse(p.canControl(UUID.randomUUID()));
        assertTrue(p.canRead(UUID.randomUUID()));
        assertFalse(p.canManage());
    }

    @Test
    void blankIdRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> new McpPrincipal("  ", McpPrincipal.Role.ADMIN, ServerOwner.ID));
    }
}
