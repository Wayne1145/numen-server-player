// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

/**
 * A snapshot of a companion's background task for {@link ServerNumenActuator#getTaskStatus}.
 * {@code state} is one of {@code running}, {@code queued}, {@code done}, {@code failed},
 * {@code timeout}, {@code stopped}, {@code idle}, or {@code error}. Terminal snapshots include
 * the original structured {@code resultJson}; callers should query by the action's task id.
 */
public record TaskStatus(String taskId, String state, String detail, String resultJson) {

    public TaskStatus(String taskId, String state, String detail) {
        this(taskId, state, detail, null);
    }

    public static TaskStatus idle() {
        return new TaskStatus(null, "idle", "body idle — no background task", null);
    }

    public static TaskStatus error(String detail) {
        return new TaskStatus(null, "error", detail, null);
    }
}
