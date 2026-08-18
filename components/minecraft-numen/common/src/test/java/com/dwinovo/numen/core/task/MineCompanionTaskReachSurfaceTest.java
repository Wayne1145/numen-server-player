package com.dwinovo.numen.core.task;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 回归钉桩:withinReach 的"到方块表面距离"语义。
 *
 * <p>历史 bug(2026-08-18 正式服实测):withinReach 用玩家到方块中心的距离
 * (4.5²),而 MC 原版交互距离 4.5 是到方块表面。中心距离比表面距离多
 * 0.5~0.87 格——玩家站在方块边缘(挖树的高处原木、挖矿的侧面矿脉)时,
 * 表面可达但中心判定超距 → 挖掘被 cancel、进度清零 → 换下一个目标重挖
 * → 表现为"快挖完却换目标、永远挖不下来"。本测试锁定:表面距离必须
 * 比中心距离小,且边界方块(表面距离 ≤ reach)必须判可达。
 */
class MineCompanionTaskReachSurfaceTest {

    /** 方块 (10,70,10),玩家站在旁边一格处(脚 x=10.5, z=11.0,眼 y≈70.62)。 */
    private static final int BX = 10, BY = 70, BZ = 10;
    private static final double REACH = 4.5;
    private static final double REACH_SQR = REACH * REACH;

    private double centerDistSqr(double px, double py, double pz) {
        double cx = BX + 0.5, cy = BY + 0.5, cz = BZ + 0.5;
        double dx = px - cx, dy = py - cy, dz = pz - cz;
        return dx * dx + dy * dy + dz * dz;
    }

    @Test
    void surfaceDistanceIsNeverLargerThanCenterDistance() {
        // 站在方块正侧面、方块正上方、斜上方三种典型挖掘姿态。
        double[][] stances = {
                {10.5, 70.62, 11.0},   // 侧面,水平差 1 格
                {10.5, 72.62, 10.5},   // 上方约 2 格
                {11.5, 71.62, 11.5},   // 斜上方
        };
        for (double[] s : stances) {
            double surf = MineCompanionTask.surfaceDistSqr(s[0], s[1], s[2], BX, BY, BZ);
            double center = centerDistSqr(s[0], s[1], s[2]);
            assertTrue(surf <= center,
                    "surface dist must be <= center dist for stance "
                            + s[0] + "," + s[1] + "," + s[2]);
        }
    }

    @Test
    void edgeStanceReachableBySurfaceButNotByCenter() {
        // 站在方块侧面:水平差 4.6 格、垂直正对中心 → 到表面 4.1 格(可达),
        // 到中心 4.6 格(旧逻辑判不可达)。这正是"快挖完却换目标"的触发姿态。
        double px = BX + 0.5 + 4.6;      // 水平 4.6 格(表面外 4.1)
        double py = BY + 0.5;            // 垂直正对中心
        double pz = BZ + 0.5;            // 正对表面
        double surf = MineCompanionTask.surfaceDistSqr(px, py, pz, BX, BY, BZ);
        double center = centerDistSqr(px, py, pz);
        assertTrue(surf <= REACH_SQR, "surface reachable: " + surf);
        assertTrue(center > REACH_SQR, "center unreachable under old logic: " + center);
    }

    @Test
    void standingBesideBlockIsReachable() {
        // 站在方块旁一格(脚 x=10.5, z=11.0):表面距离 ≈ 1 格,必然可达。
        double surf = MineCompanionTask.surfaceDistSqr(10.5, 70.62, 11.0, BX, BY, BZ);
        assertTrue(surf <= REACH_SQR, "beside block must be reachable: " + surf);
    }

    @Test
    void treeTopCellReachableBySurfaceButNotByCenter() {
        // 挖树冠:目标在玩家头顶上方 4 格(74-70)、水平正对树干中心。
        // 到表面 ≈ 4.38 格 → 可达;到中心 ≈ 4.88 格 → 旧逻辑判不可达。
        // 这就是"树顶挖不下来"的几何根因。
        double surf = MineCompanionTask.surfaceDistSqr(
                10.5, 69.62, 10.5, 10, 74, 10);
        // 中心距离相对目标方块 (10,74,10) 计算
        double cx = 10.5, cy = 74.5, cz = 10.5;
        double dx = 10.5 - cx, dy = 69.62 - cy, dz = 10.5 - cz;
        double center = dx * dx + dy * dy + dz * dz;
        assertTrue(surf <= REACH_SQR, "tree top cell should be reachable: " + surf);
        assertTrue(center > REACH_SQR, "tree top unreachable under old center logic: " + center);
    }
}
