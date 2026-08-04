// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ServerControlConfigTest {

    @Test
    void parsesPolicyAndPrincipals() {
        String json = """
            {
              "policy": {
                "enabled_capabilities": ["SERVER_READ_ONLY", "SERVER_BODY_ACTION", "DESTRUCTIVE"],
                "allowed_dimensions": ["minecraft:overworld", "minecraft:the_nether"],
                "allowed_namespaces": ["minecraft", "create"],
                "lease_ttl_ms": 30000,
                "max_calls_per_minute_per_principal": 42
              },
              "principals": [
                {"token": "T-admin", "id": "root", "role": "ADMIN"},
                {"token": "T-ro", "id": "viewer", "role": "READONLY"}
              ]
            }
            """;
        ServerControlConfig cfg = ServerControlConfig.fromJson(json);
        assertTrue(cfg.policy.capabilityEnabled(ToolCapability.DESTRUCTIVE));
        assertTrue(cfg.policy.dimensionAllowed("minecraft:the_nether"));
        assertTrue(cfg.policy.namespaceAllowed("create"));
        assertEquals(30000, cfg.policy.leaseTtlMs());
        assertEquals(42, cfg.policy.maxCallsPerMinutePerPrincipal());
        assertEquals("root", cfg.tokens.resolve("T-admin").id());
        assertEquals(McpPrincipal.Role.READONLY, cfg.tokens.resolve("T-ro").role());
    }

    @Test
    void defaultsAreRestrictive() {
        ServerControlConfig d = ServerControlConfig.defaults();
        assertNotNull(d.policy);
        assertFalse(d.policy.capabilityEnabled(ToolCapability.DESTRUCTIVE));
        assertTrue(d.tokens.isEmpty(), "no tokens by default → MCP surface stays shut");
    }
}
