package com.dwinovo.numen.core.task.chain;

import com.dwinovo.numen.core.pathing.calc.NavGoal;
import com.dwinovo.numen.core.pathing.exec.PlayerNav;
import com.dwinovo.numen.core.task.SurvivalConfig;
import com.dwinovo.numen.core.task.survival.DamageEscapePolicy;
import com.dwinovo.numen.core.task.survival.SurvivalDecisions;
import com.dwinovo.numen.entity.Companions;
import com.dwinovo.numen.entity.InputDriver;
import com.dwinovo.numen.entity.NumenPlayer;
import com.dwinovo.numen.task.BodyLog;
import com.dwinovo.numen.task.TaskChain;
import com.dwinovo.numen.task.reflex.Reflex;
import net.minecraft.core.BlockPos;

/**
 * 非怪物环境伤害脱险本能。仙人掌、浆果丛、岩浆和模组尖刺不会成为
 * {@code Monster}，旧 mob-defense 因而完全看不见；本链只在身体本 tick
 * 真实掉血后启动，从受伤点向外走至少四格。三次寻路都失败时保存身体并
 * 休眠，宁可等待下一次工具调用唤醒，也不允许 idle 身体原地死亡循环。
 */
public final class DamageEscapeChain implements TaskChain, Reflex {
    private static final double ESCAPE_SPEED = 1.3;

    private final BodyLog bodyLog;
    private float previousHealth = Float.NaN;
    private boolean active;
    private double originX;
    private double originZ;
    private int originY;
    private int routeFailures;
    private PlayerNav nav;

    public DamageEscapeChain() {
        this(null);
    }

    public DamageEscapeChain(BodyLog bodyLog) {
        this.bodyLog = bodyLog;
    }

    @Override
    public float getPriority(NumenPlayer companion) {
        float health = companion.getHealth();
        if (Float.isNaN(previousHealth)) {
            previousHealth = health;
            return SurvivalDecisions.DORMANT;
        }
        boolean damaged = DamageEscapePolicy.tookDamage(previousHealth, health);
        previousHealth = health;
        if (damaged && !active) {
            active = true;
            originX = companion.getX();
            originZ = companion.getZ();
            originY = companion.blockPosition().getY();
            routeFailures = 0;
        }
        if (!SurvivalConfig.enabled() || !com.dwinovo.numen.task.reflex.ReflexRegistry.enabled(id())) {
            return SurvivalDecisions.DORMANT;
        }
        return active ? SurvivalDecisions.DAMAGE_ESCAPE_PRIORITY : SurvivalDecisions.DORMANT;
    }

    @Override
    public void tick(NumenPlayer companion) {
        if (DamageEscapePolicy.escaped(originX, originZ,
                companion.getX(), companion.getZ(), companion.onGround())) {
            if (bodyLog != null) bodyLog.report("escaped from a damaging block or environmental hazard");
            release(companion);
            return;
        }
        if (nav == null) {
            BlockPos danger = BlockPos.containing(originX, originY, originZ);
            nav = PlayerNav.toGoal(companion,
                    () -> NavGoal.runAway(danger, originY),
                    ESCAPE_SPEED,
                    () -> DamageEscapePolicy.escaped(originX, originZ,
                            companion.getX(), companion.getZ(), companion.onGround()));
        }
        switch (nav.tick()) {
            case RUNNING -> { /* 正在脱离 */ }
            case ARRIVED -> {
                if (DamageEscapePolicy.escaped(originX, originZ,
                        companion.getX(), companion.getZ(), companion.onGround())) {
                    release(companion);
                }
            }
            case FAILED -> {
                nav.stop();
                nav = null;
                if (DamageEscapePolicy.shouldDormant(++routeFailures)) {
                    InputDriver.stop(companion);
                    active = false;
                    if (bodyLog != null) {
                        bodyLog.report("could not escape an environmental hazard and went dormant for safety");
                    }
                    var server = companion.level().getServer();
                    if (server != null) {
                        // 下一 server task 再移除，避免在当前玩家列表迭代中改集合。
                        server.execute(() -> Companions.dormant(server, companion));
                    }
                }
            }
        }
    }

    @Override
    public void onInterrupt(NumenPlayer companion) {
        // 坠落/缺氧等更高优先级本能可暂时抢占，但不能忘记危险起点。
        if (nav != null) {
            nav.stop();
            nav = null;
        }
        InputDriver.stop(companion);
    }

    private void release(NumenPlayer companion) {
        if (nav != null) nav.stop();
        nav = null;
        active = false;
        routeFailures = 0;
        InputDriver.stop(companion);
    }

    @Override public String name() { return "damage_escape"; }
    @Override public String id() { return name(); }
    @Override public String describe() { return "被尖刺、火焰等环境持续伤害时会脱离危险，走不掉则安全休眠"; }
}
