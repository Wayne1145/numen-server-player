// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.core.tools;

import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.core.events.EventRingBuffer;
import com.dwinovo.numen.entity.NumenPlayer;
import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.util.Map;
import java.util.function.Consumer;

/**
 * get_recent_events：返回服务器最近事件（玩家聊天、死亡/重生等），供外部大脑
 * 感知玩家互动与自身状态。事件由 Forge 入口监听并写入 {@link #BUFFER}。
 */
public final class RecentEventsTool implements NumenTool {

    /** 全局事件缓冲（容量 200，只保留最近事件）。 */
    public static final EventRingBuffer BUFFER = new EventRingBuffer(200);

    private static final Gson GSON = new Gson();

    @Override
    public String name() {
        return "get_recent_events";
    }

    @Override
    public String description() {
        return "返回服务器最近的事件列表（玩家聊天、死亡等），最新在前。"
                + "当你想知道其他玩家说了什么、或自己/别人刚刚是否死亡时使用。";
    }

    @Override
    public Map<String, Object> parameterSchema() {
        Map<String, Object> schema = new java.util.LinkedHashMap<>();
        schema.put("type", "object");
        Map<String, Object> props = new java.util.LinkedHashMap<>();
        Map<String, Object> count = new java.util.LinkedHashMap<>();
        count.put("type", "integer");
        count.put("description", "最多返回几条（默认 10，最大 50）");
        props.put("count", count);
        schema.put("properties", props);
        return schema;
    }

    @Override
    public void onServerCall(String toolCallId, JsonObject args,
                             NumenPlayer companion, Consumer<String> reply) {
        int count = 10;
        if (args.has("count")) {
            try {
                count = args.get("count").getAsInt();
            } catch (Exception ignored) { /* default */ }
        }
        count = Math.max(1, Math.min(50, count));
        reply.accept(GSON.toJson(Map.of(
                "success", true,
                "events", BUFFER.recent(count))));
    }
}
