// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

/**
 * Server-authoritative bearer-token → {@link McpPrincipal} map. The token is the only credential a
 * remote caller presents; the principal (role, owner UUID) is decided <b>here</b>, never trusted
 * from the request body. Token comparison is constant-time. Used by the S4 HTTP/MCP layer; kept in
 * S3 so the identity model is complete and testable independent of transport.
 */
public final class AuthTokens {

    private final Map<String, McpPrincipal> byToken;

    public AuthTokens(Map<String, McpPrincipal> byToken) {
        this.byToken = Map.copyOf(byToken);
    }

    public static AuthTokens empty() {
        return new AuthTokens(Map.of());
    }

    /** Resolve a bearer token to its principal, or {@code null} if unknown. Constant-time per token. */
    public McpPrincipal resolve(String token) {
        if (token == null || token.isEmpty()) return null;
        McpPrincipal match = null;
        // Check every entry without short-circuiting, so a match doesn't reveal itself via timing.
        for (Map.Entry<String, McpPrincipal> e : byToken.entrySet()) {
            if (constantTimeEquals(e.getKey(), token)) {
                match = e.getValue();
            }
        }
        return match;
    }

    public boolean isEmpty() {
        return byToken.isEmpty();
    }

    private static boolean constantTimeEquals(String a, String b) {
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }
}
