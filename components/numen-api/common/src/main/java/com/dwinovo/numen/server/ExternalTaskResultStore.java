// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;

import com.dwinovo.numen.task.TaskResult;
import com.dwinovo.numen.task.TaskState;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 外部 MCP/API 异步任务的有界终态缓存。
 *
 * <p>所有调用都发生在服务器主线程；每个同伴仅保留最近 32 条，防止长期开服时无界增长。
 */
public final class ExternalTaskResultStore {

    private static final int MAX_PER_COMPANION = 32;
    private static final Map<UUID, LinkedHashMap<String, TaskStatus>> RESULTS = new LinkedHashMap<>();

    private ExternalTaskResultStore() {}

    public static void record(UUID companionId, String taskId, TaskState taskState,
                              String toolName, TaskResult result) {
        if (companionId == null || taskId == null || taskId.isBlank()) return;
        String state = switch (taskState) {
            case SUCCESS -> "done";
            case TIMEOUT -> "timeout";
            case CANCELLED -> "stopped";
            default -> "failed";
        };
        String message = result == null ? "no result produced" : result.message();
        String detail = (toolName == null || toolName.isBlank() ? "task" : toolName)
                + ": " + (message == null ? "" : message);
        String resultJson = result == null
                ? "{\"success\":false,\"message\":\"no result produced\"}"
                : result.toJson();
        LinkedHashMap<String, TaskStatus> byTask =
                RESULTS.computeIfAbsent(companionId, ignored -> new LinkedHashMap<>());
        byTask.remove(taskId);
        byTask.put(taskId, new TaskStatus(taskId, state, detail, resultJson));
        while (byTask.size() > MAX_PER_COMPANION) {
            String oldest = byTask.keySet().iterator().next();
            byTask.remove(oldest);
        }
    }

    public static TaskStatus get(UUID companionId, String taskId) {
        LinkedHashMap<String, TaskStatus> byTask = RESULTS.get(companionId);
        return byTask == null ? null : byTask.get(taskId);
    }

    public static void clear(UUID companionId) {
        RESULTS.remove(companionId);
    }

    static void clearAll() {
        RESULTS.clear();
    }
}
