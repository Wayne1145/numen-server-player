// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
/** 精确停止旧任务不能影响新任务；不指定 ID 才表示停当前任务。 */
public final class TaskStopGuard {
    public static boolean matches(String requested,String current) {
        return requested==null || requested.isBlank() || requested.equals(current);
    }
}
