// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

/**
 * Server-side capability class for a tool. This is a <b>whitelist</b>: a tool is exposed/allowed
 * only if it is classified into one of the server-safe capabilities AND that capability is enabled
 * by the {@link SecurityPolicy}. Unknown/unclassified tools default to {@link #INTERNAL_ONLY}
 * (denied), so nothing leaks by omission.
 */
public enum ToolCapability {

    /** Read-only perception/query. Safe: needs no control lease, allowed to READONLY principals, concurrent. */
    SERVER_READ_ONLY(false),

    /** Non-destructive body action (move, equip, eat, transfer, open a container). Needs a control lease. */
    SERVER_BODY_ACTION(true),

    /** Destructive action — break/place/attack/drop. Needs a lease AND must be explicitly enabled in policy. */
    DESTRUCTIVE(true),

    /** Companion management (create/delete/force-stop/policy). Admin only. */
    ADMIN_ONLY(true),

    /** Client-side only — never exposed by the server actuator. */
    CLIENT_ONLY(true),

    /** Engine-internal — never exposed by the server actuator. */
    INTERNAL_ONLY(true);

    private final boolean requiresControl;

    ToolCapability(boolean requiresControl) {
        this.requiresControl = requiresControl;
    }

    /** True if invoking a tool of this capability requires holding the write-control lease. */
    public boolean requiresControl() {
        return requiresControl;
    }

    /** True if the server actuator may ever expose/run this capability (READ_ONLY/BODY_ACTION/DESTRUCTIVE/ADMIN). */
    public boolean isServerExposable() {
        return this != CLIENT_ONLY && this != INTERNAL_ONLY;
    }
}
