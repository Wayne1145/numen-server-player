// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.util.EnumSet;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SecurityPolicyTest {

    private final McpPrincipal admin = McpPrincipal.admin("a");
    private final McpPrincipal owner = McpPrincipal.owner("o", UUID.randomUUID());
    private final McpPrincipal ro = McpPrincipal.readonly("r");
    private static final String OW = "minecraft:overworld";

    @Test
    void defaultsEnableReadAndBodyNotDestructive() {
        SecurityPolicy p = SecurityPolicy.defaults();
        assertTrue(p.capabilityEnabled(ToolCapability.SERVER_READ_ONLY));
        assertTrue(p.capabilityEnabled(ToolCapability.SERVER_BODY_ACTION));
        assertFalse(p.capabilityEnabled(ToolCapability.DESTRUCTIVE), "destructive is off by default");
    }

    @Test
    void readonlyMayOnlyRead() {
        SecurityPolicy p = SecurityPolicy.defaults();
        assertTrue(p.permitInvoke(ro, "get_self_status", OW).allowed());
        PolicyDecision d = p.permitInvoke(ro, "goto", OW);
        assertFalse(d.allowed());
        assertEquals("readonly", d.code());
    }

    @Test
    void bodyActionAllowedForOwnerAndAdmin() {
        SecurityPolicy p = SecurityPolicy.defaults();
        assertTrue(p.permitInvoke(owner, "goto", OW).allowed());
        assertTrue(p.permitInvoke(admin, "transfer", OW).allowed());
    }

    @Test
    void destructiveDeniedUntilEnabled() {
        SecurityPolicy off = SecurityPolicy.defaults();
        PolicyDecision d = off.permitInvoke(admin, "mine", OW);
        assertFalse(d.allowed());
        assertEquals("capability_disabled", d.code());

        SecurityPolicy on = new SecurityPolicy(
                EnumSet.of(ToolCapability.SERVER_READ_ONLY, ToolCapability.SERVER_BODY_ACTION,
                        ToolCapability.DESTRUCTIVE),
                Set.of(OW), Set.of("minecraft"), 64, 1000, 8, 120, 60);
        assertTrue(on.permitInvoke(admin, "mine", OW).allowed());
        assertFalse(on.permitInvoke(ro, "mine", OW).allowed(), "readonly still can't mine");
    }

    @Test
    void unknownToolNotExposed() {
        SecurityPolicy p = SecurityPolicy.defaults();
        PolicyDecision d = p.permitInvoke(admin, "definitely_not_a_tool", OW);
        assertFalse(d.allowed());
        assertEquals("tool_not_exposed", d.code());   // unknown -> INTERNAL_ONLY -> not server-exposable
    }

    @Test
    void dimensionGate() {
        SecurityPolicy p = SecurityPolicy.defaults();
        assertTrue(p.permitInvoke(admin, "goto", OW).allowed());
        PolicyDecision d = p.permitInvoke(admin, "goto", "minecraft:the_nether");
        assertFalse(d.allowed());
        assertEquals("dimension", d.code());
    }

    @Test
    void namespaceViolationScan() {
        SecurityPolicy p = SecurityPolicy.defaults();
        assertNull(p.namespaceViolation(JsonParser.parseString("{\"block_ids\":[\"minecraft:stone\"]}")));
        assertEquals("create",
                p.namespaceViolation(JsonParser.parseString("{\"block_ids\":[\"create:andesite_alloy\"]}")));
    }
}
