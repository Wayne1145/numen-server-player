// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import java.nio.file.Files;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class DurableDispatchAuditTest {
    @Test void concurrentSameKeyRunsOnceAndSyncReceiptSurvivesRestart() throws Exception {
        var root = Files.createTempDirectory("numen-dispatch");
        var id = UUID.randomUUID();
        var count = new AtomicInteger();
        var registry = new IdempotentDispatchRegistry(root);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Callable<ToolResult> call = () -> registry.execute(id,"action","principal/tool/args",() -> {
                count.incrementAndGet();
                try { Thread.sleep(80); } catch (InterruptedException e) { throw new RuntimeException(e); }
                return ToolResult.of("{\"success\":true,\"message\":\"moved once\"}");
            });
            var a = pool.submit(call); var b = pool.submit(call);
            assertEquals(a.get().json(), b.get().json());
            assertEquals(1,count.get());
            var reopened = new IdempotentDispatchRegistry(root);
            reopened.execute(id,"action","principal/tool/args",() -> { fail("不能重新执行"); return null; });
            assertThrows(IllegalStateException.class, () -> reopened.execute(id,"action","other",() -> null));
        } finally { pool.shutdownNow(); }
    }
    @Test void crashReservationIsUnknownNotRetryable() throws Exception {
        var root = Files.createTempDirectory("numen-dispatch");
        var id = UUID.randomUUID();
        var registry = new IdempotentDispatchRegistry(root);
        assertThrows(RuntimeException.class, () -> registry.execute(id,"action","fp",() -> { throw new RuntimeException("crash"); }));
        assertThrows(IllegalStateException.class, () -> new IdempotentDispatchRegistry(root).execute(id,"action","fp",() -> { fail("未知副作用不能重放"); return null; }));
    }
}
