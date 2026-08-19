package com.dwinovo.numen.core.task.survival;

/** 环境伤害脱险的纯策略常量与判据。 */
public final class DamageEscapePolicy {
    /** 离受伤起点至少四格且重新站稳，才算真正脱险。 */
    public static final double ESCAPE_DISTANCE = 4.0;
    /** 三次独立寻路均失败，不再原地送死，进入可恢复休眠。 */
    public static final int MAX_ROUTE_FAILURES = 3;

    private DamageEscapePolicy() {}

    public static boolean tookDamage(float previousHealth, float currentHealth) {
        return currentHealth < previousHealth;
    }

    public static boolean escaped(double originX, double originZ,
                                  double currentX, double currentZ,
                                  boolean stableFooting) {
        double dx = currentX - originX;
        double dz = currentZ - originZ;
        return stableFooting && dx * dx + dz * dz >= ESCAPE_DISTANCE * ESCAPE_DISTANCE;
    }

    public static boolean shouldDormant(int routeFailures) {
        return routeFailures >= MAX_ROUTE_FAILURES;
    }
}
