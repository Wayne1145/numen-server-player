package com.dwinovo.numen.server;

import com.dwinovo.numen.task.TaskResult;
import com.dwinovo.numen.task.TaskState;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ExternalTaskResultStoreTest {

    @AfterEach
    void clear() {
        ExternalTaskResultStore.clearAll();
    }

    @Test
    void recordsStructuredTerminalResultByCompanionAndTaskId() {
        UUID companion = UUID.randomUUID();
        TaskResult result = TaskResult.ok("arrived", Map.of("final_x", 6.4));

        ExternalTaskResultStore.record(companion, "t7", TaskState.SUCCESS, "goto", result);

        TaskStatus status = ExternalTaskResultStore.get(companion, "t7");
        assertEquals("done", status.state());
        assertEquals("t7", status.taskId());
        assertEquals("goto: arrived", status.detail());
        assertTrue(status.resultJson().contains("\"success\":true"));
        assertTrue(status.resultJson().contains("\"final_x\":6.4"));
    }

    @Test
    void mapsFailureTimeoutAndCancellationWithoutCrossCompanionLeakage() {
        UUID one = UUID.randomUUID();
        UUID two = UUID.randomUUID();
        ExternalTaskResultStore.record(one, "t1", TaskState.FAILED, "goto", TaskResult.fail("boxed in"));
        ExternalTaskResultStore.record(one, "t2", TaskState.TIMEOUT, "mine", TaskResult.timeout("partial"));
        ExternalTaskResultStore.record(one, "t3", TaskState.CANCELLED, "build", TaskResult.cancelled("stopped"));

        assertEquals("failed", ExternalTaskResultStore.get(one, "t1").state());
        assertEquals("timeout", ExternalTaskResultStore.get(one, "t2").state());
        assertEquals("stopped", ExternalTaskResultStore.get(one, "t3").state());
        assertNull(ExternalTaskResultStore.get(two, "t1"));
    }

    @Test
    void keepsOnlyMostRecentThirtyTwoResultsPerCompanion() {
        UUID companion = UUID.randomUUID();
        for (int i = 1; i <= 33; i++) {
            ExternalTaskResultStore.record(companion, "t" + i, TaskState.SUCCESS,
                    "goto", TaskResult.ok("done " + i));
        }

        assertNull(ExternalTaskResultStore.get(companion, "t1"));
        assertEquals("done", ExternalTaskResultStore.get(companion, "t2").state());
        assertEquals("done", ExternalTaskResultStore.get(companion, "t33").state());
    }
}
