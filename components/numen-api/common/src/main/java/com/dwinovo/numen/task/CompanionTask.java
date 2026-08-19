package com.dwinovo.numen.task;
import com.dwinovo.numen.task.TaskResult;

/**
 * One running task on a companion {@link com.dwinovo.numen.entity.NumenPlayer}
 * body, driven by {@code CompanionTickDispatcher}. The player-body replacement
 * for the Mob's {@code LlmTaskGoal} (which was a vanilla {@code Goal} run by a
 * GoalSelector) — here the dispatcher owns the lifecycle directly:
 * {@link #start()} once, {@link #tick()} each server tick until it returns a
 * terminal {@link TaskState}, then {@link #buildResult} for the reply.
 */
public interface CompanionTask {

    /** First-tick setup. May return a terminal state immediately via the record. */
    void start();

    /** Advance one tick. Returns {@link TaskState#RUNNING} or a terminal state. */
    TaskState tick();

    /**
     * 身体状态采样：即使任务被更高优先级本能抢占，调度器仍每 tick 调用。
     * 默认无状态；需要跨抢占监控生命/位置的任务可覆盖。
     */
    default void observeBody(com.dwinovo.numen.entity.NumenPlayer player) {}

    /** The result envelope handed back to the LLM. */
    TaskResult buildResult(TaskState finalState);
}
