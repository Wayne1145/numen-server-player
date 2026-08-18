package com.dwinovo.numen.server;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/** 延迟感知查询必须保留真实结构化终态，不能只返回 queued 空包。 */
class ExternalTaskResultStoreQueryTest {
    @AfterEach
    void clear() {
        ExternalTaskResultStore.clearAll();
    }

    @Test
    void successfulQueryIsRetainedByTaskId() {
        UUID id = UUID.randomUUID();
        String json = "{\"matches\":[{\"x\":1,\"y\":2,\"z\":3}],\"total_found\":1}";
        ExternalTaskResultStore.recordQuery(id, "q7", "scan_blocks", json);
        TaskStatus status = ExternalTaskResultStore.get(id, "q7");
        assertNotNull(status);
        assertEquals("done", status.state());
        assertEquals(json, status.resultJson());
    }

    @Test
    void failedQueryIsRetainedAsFailure() {
        UUID id = UUID.randomUUID();
        ExternalTaskResultStore.recordQuery(id, "q8", "scan_blocks",
                "{\"success\":false,\"message\":\"scan failed\"}");
        assertEquals("failed", ExternalTaskResultStore.get(id, "q8").state());
    }

    @Test
    void queryIsVisibleAsQueuedBeforeLateCallbackThenOverwrittenByTerminalResult() {
        UUID id = UUID.randomUUID();
        ExternalTaskResultStore.registerQuery(id, "q9", "scan_blocks");

        TaskStatus pending = ExternalTaskResultStore.get(id, "q9");
        assertNotNull(pending);
        assertEquals("queued", pending.state());
        assertNull(pending.resultJson());

        String json = "{\"matches\":[],\"total_found\":0}";
        ExternalTaskResultStore.recordQuery(id, "q9", "scan_blocks", json);
        TaskStatus done = ExternalTaskResultStore.get(id, "q9");
        assertEquals("done", done.state());
        assertEquals(json, done.resultJson());

        // 极窄竞态：回调先落盘，随后注册 pending；终态绝不能被降级。
        ExternalTaskResultStore.registerQuery(id, "q9", "scan_blocks");
        assertEquals("done", ExternalTaskResultStore.get(id, "q9").state());
    }
}
