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

    private final java.nio.file.Path root;
    public IdempotentDispatchRegistry(java.nio.file.Path root) {
        this.root = root;
    }

    /** 主线程授权后进入：写前持久预留，回执含同步结果；未知窗口拒绝重放。 */
    public synchronized ToolResult execute(UUID companion, String key, String fingerprint,
                                           java.util.function.Supplier<ToolResult> action) {
        if (key == null) return action.get();
        if (!key.matches("[A-Za-z0-9_.:-]{1,128}")) throw new IllegalStateException("invalid client_action_id");
        try {
            java.nio.file.Path dir = root.resolve(companion.toString());
            java.nio.file.Files.createDirectories(dir);
            java.nio.file.Path file = dir.resolve(key + ".json");
            if (java.nio.file.Files.exists(file)) {
                var record = com.google.gson.JsonParser.parseString(java.nio.file.Files.readString(file)).getAsJsonObject();
                if (!fingerprint.equals(record.get("fingerprint").getAsString()))
                    throw new IllegalStateException("client_action_id conflict: principal/tool/arguments differ");
                if (!record.has("receipt")) throw new IllegalStateException("dispatch_unknown: reserved action requires verification; refusing replay");
                return ToolResult.of(record.get("receipt").getAsString());
            }
            // 不淘汰动作键；达到预算失败关闭，避免旧键淘汰后重复世界副作用。
            try (var files = java.nio.file.Files.list(dir)) {
                if (files.count() >= 10000) throw new IllegalStateException("idempotency journal capacity reached; archive safely before new actions");
            }
            var record = new com.google.gson.JsonObject();
            record.addProperty("fingerprint", fingerprint);
            persist(file, record.toString());
            ToolResult result = action.get();
            record.addProperty("receipt", result.json());
            persist(file, record.toString());
            return result;
        } catch (java.io.IOException ex) {
            throw new IllegalStateException("idempotency journal unavailable; refusing dispatch", ex);
        }
    }

    private static void persist(java.nio.file.Path file, String text) throws java.io.IOException {
        var temporary = file.resolveSibling(file.getFileName() + ".tmp");
        try (var channel = java.nio.channels.FileChannel.open(temporary,
                java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.TRUNCATE_EXISTING,
                java.nio.file.StandardOpenOption.WRITE)) {
            var bytes = java.nio.ByteBuffer.wrap(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            while (bytes.hasRemaining()) channel.write(bytes);
            channel.force(true);
        }
        try {
            java.nio.file.Files.move(temporary, file, java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException ex) {
            java.nio.file.Files.move(temporary, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        }
    }

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
