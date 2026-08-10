// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.core.events;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 服务器事件环形缓冲：聊天、死亡等 Forge 事件由入口类写入，MCP 侧
 * {@code get_recent_events} 工具按需读取。只保留最近 {@code capacity} 条，
 * 防止长期开服无界增长。事件字段为字符串 map，便于 JSON 序列化。
 */
public final class EventRingBuffer {

    private final int capacity;
    private final Deque<Map<String, String>> events = new ArrayDeque<>();

    public EventRingBuffer(int capacity) {
        this.capacity = Math.max(1, capacity);
    }

    /** 追加一条事件；超过容量时丢弃最旧一条。 */
    public synchronized void add(String kind, String source, String detail) {
        Map<String, String> event = new LinkedHashMap<>();
        event.put("kind", kind);
        event.put("source", source == null ? "" : source);
        event.put("detail", detail == null ? "" : detail);
        event.put("time", String.valueOf(System.currentTimeMillis()));
        events.addLast(event);
        while (events.size() > capacity) {
            events.removeFirst();
        }
    }

    /** 返回最近最多 {@code count} 条的副本，最新在前。 */
    public synchronized List<Map<String, String>> recent(int count) {
        List<Map<String, String>> out = new ArrayList<>();
        var iterator = events.descendingIterator();
        int remaining = Math.max(0, count);
        while (iterator.hasNext() && remaining-- > 0) {
            out.add(new LinkedHashMap<>(iterator.next()));
        }
        return out;
    }

    public synchronized int size() {
        return events.size();
    }
}
