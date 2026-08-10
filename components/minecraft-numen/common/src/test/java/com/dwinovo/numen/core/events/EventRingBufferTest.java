// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.core.events;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EventRingBufferTest {

    @Test
    void recentReturnsNewestFirstWithCap() {
        EventRingBuffer buffer = new EventRingBuffer(8);
        for (int i = 1; i <= 10; i++) {
            buffer.add("chat", "source", "message " + i);
        }
        var recent = buffer.recent(5);
        assertEquals(5, recent.size());
        assertEquals("message 10", recent.get(0).get("detail"));
        assertEquals("message 6", recent.get(4).get("detail"));
        assertEquals(8, buffer.size(), "环形缓冲应保持容量上限");
    }

    @Test
    void recentCountCapsAtBufferSize() {
        EventRingBuffer buffer = new EventRingBuffer(3);
        buffer.add("death", "src", "a");
        var recent = buffer.recent(100);
        assertEquals(1, recent.size());
    }

    @Test
    void emptyBufferReturnsEmptyList() {
        EventRingBuffer buffer = new EventRingBuffer(4);
        assertTrue(buffer.recent(10).isEmpty());
    }

    @Test
    void recentIsolatedCopyNotLiveView() {
        EventRingBuffer buffer = new EventRingBuffer(4);
        buffer.add("chat", "src", "one");
        var snapshot = buffer.recent(10);
        buffer.add("chat", "src", "two");
        assertEquals(1, snapshot.size(), "返回副本，后续写入不影响已取快照");
    }
}
