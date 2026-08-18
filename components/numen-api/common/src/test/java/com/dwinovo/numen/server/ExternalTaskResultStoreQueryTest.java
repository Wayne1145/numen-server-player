package com.dwinovo.numen.server;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

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
}
