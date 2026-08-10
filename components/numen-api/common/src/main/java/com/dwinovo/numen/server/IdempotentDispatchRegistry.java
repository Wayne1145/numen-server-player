// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 外部动作的幂等派发注册表：client_action_id → task_id。
 *
 * <p>外部大脑在动作派发前生成一个稳定的 client_action_id；如果动作已被服务器
 * 受理但回执在网络上丢失（dispatch_unknown 窗口），重发同一 id 不会产生第二次
 * 副作用——命中注册表时直接返回原 task_id。所有调用都发生在服务器主线程
 * （MCP 请求处理经 onMainThread 路由）；每同伴只保留最近 128 条，删除同伴时清理。
 */
public final class IdempotentDispatchRegistry {

    private static final int MAX_PER_COMPANION = 128;
    private static final Map<UUID, LinkedHashMap<String, String>> DISPATCHES = new LinkedHashMap<>();

    private IdempotentDispatchRegistry() {}

    /** 记录一次派发；同一 id 重复记录覆盖为同一条目。 */
    public static void record(UUID companionId, String clientActionId, String taskId) {
        if (companionId == null || clientActionId == null || clientActionId.isBlank()
                || taskId == null || taskId.isBlank()) {
            return;
        }
        LinkedHashMap<String, String> byAction =
                DISPATCHES.computeIfAbsent(companionId, ignored -> new LinkedHashMap<>());
        byAction.remove(clientActionId);
        byAction.put(clientActionId, taskId);
        while (byAction.size() > MAX_PER_COMPANION) {
            String oldest = byAction.keySet().iterator().next();
            byAction.remove(oldest);
        }
    }

    /** 查询某 client_action_id 是否已派发；未命中返回 null。 */
    public static String lookup(UUID companionId, String clientActionId) {
        if (companionId == null || clientActionId == null || clientActionId.isBlank()) {
            return null;
        }
        LinkedHashMap<String, String> byAction = DISPATCHES.get(companionId);
        return byAction == null ? null : byAction.get(clientActionId);
    }

    /** 删除同伴时清理其全部幂等记录。 */
    public static void clear(UUID companionId) {
        DISPATCHES.remove(companionId);
    }
}
