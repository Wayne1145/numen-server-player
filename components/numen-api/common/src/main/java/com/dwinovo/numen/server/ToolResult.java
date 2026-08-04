// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * The outcome of a {@link ServerNumenActuator#invoke} call. {@code json} is the raw JSON the tool
 * produced (a query result, or the dispatch envelope of a background task). For a background
 * (async) action the tool replies immediately with a {@code task_id}; {@link #taskId} surfaces it
 * so the caller can poll {@link ServerNumenActuator#getTaskStatus} until the body is idle.
 */
public record ToolResult(boolean ok, String json, String taskId) {

    /** Wrap the raw tool JSON, deriving {@code ok} from its {@code success} flag and any {@code task_id}. */
    public static ToolResult of(String json) {
        boolean ok = true;
        String taskId = null;
        try {
            JsonObject o = JsonParser.parseString(json).getAsJsonObject();
            if (o.has("success") && !o.get("success").isJsonNull()) {
                ok = o.get("success").getAsBoolean();
            }
            if (o.has("task_id") && !o.get("task_id").isJsonNull()) {
                taskId = o.get("task_id").getAsString();
            } else if (o.has("data") && o.get("data").isJsonObject()) {
                JsonObject data = o.getAsJsonObject("data");
                if (data.has("task_id") && !data.get("task_id").isJsonNull()) {
                    taskId = data.get("task_id").getAsString();
                }
            }
        } catch (RuntimeException ignored) {
            // non-JSON tool output — treat as an opaque successful text result
        }
        return new ToolResult(ok, json, taskId);
    }

    public static ToolResult fail(String message) {
        return new ToolResult(false, "{\"success\":false,\"message\":\"" + escape(message) + "\"}", null);
    }

    private static String escape(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
    }
}
