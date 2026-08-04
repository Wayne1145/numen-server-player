// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.dwinovo.numen.entity.ServerOwner;

import java.util.UUID;

/**
 * The server-authoritative identity behind a control call — the thing that replaces
 * "the Minecraft network player" for callers that have no in-game player (an MCP client, an
 * API agent). Every {@link ServerNumenActuator} method takes one, and the actuator decides
 * on the main thread what it may do.
 *
 * <p><b>S2 scope:</b> a plain value object with a role and (for OWNER) an owner UUID, plus the
 * authorization predicates. Token→principal mapping, per-principal allow-lists, rate limits,
 * and audit are S3 — this type is the stable seam they build on.
 *
 * <p>There are two independent notions of "owner": the Minecraft-level owner UUID that drives a
 * companion's respawn lifecycle (server-owned companions use {@link ServerOwner#ID}), and this
 * principal's <em>control authority</em>. For S2 the ADMIN role controls everything (including
 * server-owned companions); OWNER is scoped to companions whose MC owner matches its
 * {@link #ownerUuid}; READONLY may read but not act.
 */
public record McpPrincipal(String id, Role role, UUID ownerUuid) {

    public enum Role { ADMIN, OWNER, READONLY }

    public McpPrincipal {
        if (id == null || id.isBlank()) throw new IllegalArgumentException("principal id required");
        if (role == null) throw new IllegalArgumentException("principal role required");
    }

    public static McpPrincipal admin(String id) {
        return new McpPrincipal(id, Role.ADMIN, ServerOwner.ID);
    }

    public static McpPrincipal owner(String id, UUID ownerUuid) {
        return new McpPrincipal(id, Role.OWNER, ownerUuid);
    }

    public static McpPrincipal readonly(String id) {
        return new McpPrincipal(id, Role.READONLY, ServerOwner.ID);
    }

    /** May this principal issue a write/control action on a companion whose MC owner is {@code companionOwner}? */
    public boolean canControl(UUID companionOwner) {
        return switch (role) {
            case ADMIN -> true;
            case OWNER -> ownerUuid != null && ownerUuid.equals(companionOwner);
            case READONLY -> false;
        };
    }

    /** May this principal read a companion whose MC owner is {@code companionOwner}? */
    public boolean canRead(UUID companionOwner) {
        return switch (role) {
            case ADMIN, READONLY -> true;
            case OWNER -> ownerUuid != null && ownerUuid.equals(companionOwner);
        };
    }

    /** May this principal create or delete companions? (READONLY may not.) */
    public boolean canManage() {
        return role != Role.READONLY;
    }
}
