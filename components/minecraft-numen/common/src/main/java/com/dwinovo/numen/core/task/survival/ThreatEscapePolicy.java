package com.dwinovo.numen.core.task.survival;

/** 怪物逃生连续失败时的安全收束判据。 */
public final class ThreatEscapePolicy {
    public static final int MAX_FAILED_ROUTES = 3;

    private ThreatEscapePolicy() {}

    public static boolean shouldDormant(int failedRoutes) {
        return failedRoutes >= MAX_FAILED_ROUTES;
    }
}
