// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.agent;

import com.dwinovo.numen.agent.provider.AssistantTurn;
import com.dwinovo.numen.agent.provider.LlmToolCall;
import com.dwinovo.numen.entity.ServerOwner;
import com.dwinovo.numen.server.CompanionRef;
import com.dwinovo.numen.server.Lease;
import com.dwinovo.numen.server.McpPrincipal;
import com.dwinovo.numen.server.ServerNumenActuator;
import com.dwinovo.numen.server.TaskStatus;
import com.dwinovo.numen.server.ToolResult;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Unit tests for the API-agent loop core with a scripted model and a mock actuator — no network,
 * no Minecraft, no key. Verifies: lease acquired before acting and released after; tool calls
 * flow through the actuator; async dispatches are polled to idle; the loop ends on a plain turn.
 */
class ServerApiAgentTest {

    /** Records every actuator interaction; async goto goes running→idle across polls. */
    static class MockActuator implements ServerNumenActuator {
        final List<String> log = new ArrayList<>();
        int statusPolls;
        boolean leaseHeld;

        @Override public CompletableFuture<List<CompanionRef>> listCompanions(McpPrincipal p) {
            return CompletableFuture.completedFuture(List.of());
        }
        @Override public CompletableFuture<CompanionRef> createCompanion(McpPrincipal p, String name) {
            return CompletableFuture.completedFuture(new CompanionRef(UUID.randomUUID(), name, ServerOwner.ID, true));
        }
        @Override public CompletableFuture<Boolean> deleteCompanion(McpPrincipal p, UUID id) {
            return CompletableFuture.completedFuture(true);
        }
        @Override public CompletableFuture<Lease> acquireControl(McpPrincipal p, UUID id) {
            log.add("acquire");
            leaseHeld = true;
            return CompletableFuture.completedFuture(new Lease("L1", id, p.id(), 1, 0, Long.MAX_VALUE));
        }
        @Override public CompletableFuture<Void> releaseControl(McpPrincipal p, UUID id, String leaseId) {
            log.add("release:" + leaseId);
            leaseHeld = false;
            return CompletableFuture.completedFuture(null);
        }
        @Override public CompletableFuture<ToolResult> invoke(McpPrincipal p, UUID id, String leaseId,
                                                              String tool, JsonObject args) {
            assertTrue(leaseHeld, "tool ran without the lease");
            assertEquals("L1", leaseId);
            log.add("invoke:" + tool);
            if ("goto".equals(tool)) {
                return CompletableFuture.completedFuture(
                        ToolResult.of("{\"success\":true,\"data\":{\"task_id\":\"t9\"}}"));
            }
            return CompletableFuture.completedFuture(ToolResult.of("{\"success\":true,\"message\":\"hp 20\"}"));
        }
        @Override public CompletableFuture<TaskStatus> getTaskStatus(McpPrincipal p, UUID id, String taskId) {
            statusPolls++;
            log.add("status#" + statusPolls);
            return CompletableFuture.completedFuture(statusPolls < 2
                    ? new TaskStatus("t9", "running", "goto")
                    : TaskStatus.idle());
        }
        @Override public CompletableFuture<Void> stopTask(McpPrincipal p, UUID id, String taskId) {
            log.add("stopTask");
            return CompletableFuture.completedFuture(null);
        }
    }

    private static AssistantTurn toolTurn(LlmToolCall... calls) {
        return new AssistantTurn("", List.of(calls), null);
    }

    @Test
    void loopRunsToolsThenFinishes() throws Exception {
        MockActuator act = new MockActuator();
        // Scripted model: turn1 = perceive + goto; turn2 = plain "done".
        Deque<AssistantTurn> script = new ArrayDeque<>(List.of(
                toolTurn(new LlmToolCall("c1", "get_self_status", "{}"),
                        new LlmToolCall("c2", "goto", "{\"x\":1,\"z\":2}")),
                new AssistantTurn("done — arrived", List.of(), null)));
        ServerApiAgent agent = new ServerApiAgent(act, McpPrincipal.admin("api-agent"),
                UUID.randomUUID(), (msgs, sys) -> CompletableFuture.completedFuture(script.poll()),
                8, 50, 10);

        String out = agent.run("walk to 1,2");

        assertEquals("done — arrived", out);
        assertEquals(ServerApiAgent.State.IDLE, agent.state());
        // Order: acquire → both tools through the actuator (goto polled to idle) → release.
        assertEquals("acquire", act.log.get(0));
        assertTrue(act.log.contains("invoke:get_self_status"));
        assertTrue(act.log.contains("invoke:goto"));
        assertTrue(act.log.contains("status#2"), "async goto polled until idle: " + act.log);
        assertEquals("release:L1", act.log.get(act.log.size() - 1));
        assertTrue(act.log.indexOf("invoke:goto") < act.log.indexOf("status#1"));
    }

    @Test
    void turnBudgetBoundsTheLoop() throws Exception {
        MockActuator act = new MockActuator();
        // Model that calls a tool forever — the budget must stop it.
        ServerApiAgent agent = new ServerApiAgent(act, McpPrincipal.admin("api-agent"),
                UUID.randomUUID(),
                (msgs, sys) -> CompletableFuture.completedFuture(
                        toolTurn(new LlmToolCall("c", "get_self_status", "{}"))),
                3, 50, 10);

        String out = agent.run("loop forever");

        assertEquals("(turn budget reached)", out);
        assertEquals(3, act.log.stream().filter(s -> s.equals("invoke:get_self_status")).count());
        assertEquals("release:L1", act.log.get(act.log.size() - 1), "lease released even at budget");
    }

    @Test
    void leaseContentionFailsFastAndClean() {
        MockActuator act = new MockActuator() {
            @Override public CompletableFuture<Lease> acquireControl(McpPrincipal p, UUID id) {
                return CompletableFuture.failedFuture(
                        new IllegalStateException("companion is already controlled by another principal"));
            }
        };
        ServerApiAgent agent = new ServerApiAgent(act, McpPrincipal.admin("api-agent"),
                UUID.randomUUID(), (msgs, sys) -> CompletableFuture.completedFuture(
                        new AssistantTurn("never reached", List.of(), null)),
                8, 50, 10);
        try {
            agent.run("do something");
            org.junit.jupiter.api.Assertions.fail("should fail on contention");
        } catch (Exception ex) {
            assertTrue(ex.getMessage().contains("already controlled"), ex.getMessage());
        }
        assertNull(act.log.stream().filter(s -> s.startsWith("invoke")).findFirst().orElse(null),
                "no tool may run without the lease");
        assertEquals(ServerApiAgent.State.IDLE, agent.state());
    }
}
