// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

/** The outcome of a {@link SecurityPolicy} check: allowed, plus a machine code + human reason for audit. */
public record PolicyDecision(boolean allowed, ToolCapability capability, String code, String reason) {

    public static PolicyDecision allow(ToolCapability cap) {
        return new PolicyDecision(true, cap, "ok", "ok");
    }

    public static PolicyDecision deny(String code, String reason) {
        return new PolicyDecision(false, null, code, reason);
    }
}
