// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

/**
 * A snapshot of a companion's background task for {@link ServerNumenActuator#getTaskStatus}.
 * {@code state} is one of {@code running}, {@code queued}, {@code idle} (no background task — the
 * external driver then perceives to confirm the effect), or {@code error}/{@code unauthorized}.
 */
public record TaskStatus(String taskId, String state, String detail) {

    public static TaskStatus idle() {
        return new TaskStatus(null, "idle", "body idle — no background task");
    }

    public static TaskStatus error(String detail) {
        return new TaskStatus(null, "error", detail);
    }
}
