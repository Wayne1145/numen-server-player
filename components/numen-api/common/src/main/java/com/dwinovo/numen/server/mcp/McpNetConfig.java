// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.mcp;

/**
 * Network/transport settings for the server-side MCP endpoint. Security-first defaults: <b>disabled</b>,
 * bound to loopback {@code 127.0.0.1}. Cross-machine access is expected to go through an SSH tunnel /
 * WireGuard / Tailscale rather than binding a public interface.
 */
public record McpNetConfig(boolean enabled, String host, int port,
                           int maxBodyBytes, int maxBatch, int maxDepth,
                           int workerThreads, int requestTimeoutSeconds, int maxRequestsPerMinutePerPrincipal) {

    public static McpNetConfig disabledDefault() {
        return new McpNetConfig(false, "127.0.0.1", 25567,
                64 * 1024, 16, 32, 8, 10, 240);
    }

    /** A loopback address (IPv4 127.0.0.0/8 or IPv6 ::1) — the only auth-optional bind. */
    public boolean isLoopback() {
        String h = host == null ? "" : host.trim();
        return h.equals("127.0.0.1") || h.startsWith("127.") || h.equals("::1")
                || h.equalsIgnoreCase("localhost") || h.equals("[::1]");
    }

    /** The wildcard binds are refused outright (never bind 0.0.0.0 / ::). */
    public boolean isWildcard() {
        String h = host == null ? "" : host.trim();
        return h.equals("0.0.0.0") || h.equals("::") || h.isEmpty() || h.equals("[::]");
    }
}
