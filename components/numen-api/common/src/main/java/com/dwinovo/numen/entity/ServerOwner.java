// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.entity;

import java.util.UUID;

/**
 * Sentinel owner for <b>server-owned</b> (ownerless) companions — the core of the
 * pure-server-side "AI real player": a companion that belongs to the server itself,
 * not to any connected human player.
 *
 * <p>Upstream Numen ties every companion to a human owner (spawn beside the owner,
 * respawn-at-owner, roster-sync to the owner's client). A dedicated-server AI player
 * has no human owner, so we mint companions with this sentinel owner UUID and give
 * them a parallel, human-independent lifecycle (see {@code Companions}):
 * console summon, respawn-at-last-known-spot on death, and re-spawn from the registry
 * on server start. All the human-owner paths simply skip these bodies, because
 * {@code getPlayerList().getPlayer(ID)} is always {@code null}.
 */
public final class ServerOwner {

    /** The nil UUID (all-zero) — no real player ever has it, so it is a safe "server" sentinel. */
    public static final UUID ID = new UUID(0L, 0L);

    private ServerOwner() {}

    /** True if {@code owner} is the server-owned sentinel (ownerless companion). */
    public static boolean isServerOwned(UUID owner) {
        return ID.equals(owner);
    }
}
