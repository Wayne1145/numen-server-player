// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.util.Map;

/**
 * Server-side capability classification for the concrete Numen tools. A <b>whitelist</b>: any tool
 * not listed here is treated as {@link ToolCapability#INTERNAL_ONLY} (denied), so a newly-added or
 * modded tool is never silently exposed.
 *
 * <p>Classification follows the S3 default posture: perception is read-only; movement / inventory /
 * container use is a body action; break/place/attack/drop are destructive (off by default);
 * companion management is admin-only. {@code interact_at} / {@code interact_entity} are treated as
 * body actions (their common use is right-click/open); their left-button break/attack is gated the
 * same as any body action and can be tightened later.
 */
public final class ToolCatalog {

    private static final Map<String, ToolCapability> CAP = Map.ofEntries(
            // -- read-only perception --
            e("get_self_status", ToolCapability.SERVER_READ_ONLY),
            e("get_owner_status", ToolCapability.SERVER_READ_ONLY),
            e("get_world_info", ToolCapability.SERVER_READ_ONLY),
            e("look_around", ToolCapability.SERVER_READ_ONLY),
            e("scan_blocks", ToolCapability.SERVER_READ_ONLY),
            e("scan_nearby_entities", ToolCapability.SERVER_READ_ONLY),
            e("inspect_block", ToolCapability.SERVER_READ_ONLY),
            e("inspect_block_storage", ToolCapability.SERVER_READ_ONLY),
            e("inspect_gui", ToolCapability.SERVER_READ_ONLY),
            e("lookup_recipe", ToolCapability.SERVER_READ_ONLY),
            e("locate_biome", ToolCapability.SERVER_READ_ONLY),
            e("locate_structure", ToolCapability.SERVER_READ_ONLY),
            e("task_status", ToolCapability.SERVER_READ_ONLY),
            e("get_recent_events", ToolCapability.SERVER_READ_ONLY),
            // -- non-destructive body actions --
            e("goto", ToolCapability.SERVER_BODY_ACTION),
            e("collect_items", ToolCapability.SERVER_BODY_ACTION),
            e("equip_item", ToolCapability.SERVER_BODY_ACTION),
            e("eat_item", ToolCapability.SERVER_BODY_ACTION),
            e("craft", ToolCapability.SERVER_BODY_ACTION),
            e("transfer", ToolCapability.SERVER_BODY_ACTION),
            e("close_gui", ToolCapability.SERVER_BODY_ACTION),
            e("interact_at", ToolCapability.SERVER_BODY_ACTION),
            e("interact_entity", ToolCapability.SERVER_BODY_ACTION),
            e("send_chat", ToolCapability.SERVER_BODY_ACTION),
            e("task_stop", ToolCapability.SERVER_BODY_ACTION),
            // -- destructive (off by default; enable in policy) --
            e("mine", ToolCapability.DESTRUCTIVE),
            e("build", ToolCapability.DESTRUCTIVE),
            e("drop_items", ToolCapability.DESTRUCTIVE),
            e("melee_attack", ToolCapability.DESTRUCTIVE),
            e("ranged_attack", ToolCapability.DESTRUCTIVE),
            // -- client/internal (never server-exposed) --
            e("load_skill", ToolCapability.CLIENT_ONLY),
            e("todowrite", ToolCapability.INTERNAL_ONLY));

    private ToolCatalog() {}

    private static Map.Entry<String, ToolCapability> e(String k, ToolCapability v) {
        return Map.entry(k, v);
    }

    /** Capability of a tool by name; unknown tools are INTERNAL_ONLY (denied) — a whitelist, never a blacklist. */
    public static ToolCapability of(String toolName) {
        return CAP.getOrDefault(toolName, ToolCapability.INTERNAL_ONLY);
    }

    /** True if this tool is classified as server-exposable at all (regardless of the current policy). */
    public static boolean isKnownServerTool(String toolName) {
        return of(toolName).isServerExposable();
    }
}
