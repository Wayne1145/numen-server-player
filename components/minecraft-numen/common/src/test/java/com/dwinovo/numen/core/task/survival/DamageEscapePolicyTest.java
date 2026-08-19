package com.dwinovo.numen.core.task.survival;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 环境伤害脱险状态机的纯策略回归。 */
class DamageEscapePolicyTest {

    @Test
    void realHealthLossStartsAnEscapeEpisode() {
        assertTrue(DamageEscapePolicy.tookDamage(20.0f, 18.0f));
        assertFalse(DamageEscapePolicy.tookDamage(18.0f, 18.0f));
        assertFalse(DamageEscapePolicy.tookDamage(18.0f, 20.0f));
    }

    @Test
    void fourHorizontalBlocksWithSolidFootingCountsAsEscaped() {
        assertTrue(DamageEscapePolicy.escaped(0, 0, 4, 0, true));
        assertFalse(DamageEscapePolicy.escaped(0, 0, 3.9, 0, true));
        assertFalse(DamageEscapePolicy.escaped(0, 0, 4, 0, false));
    }

    @Test
    void threeFailedRoutesRequireDormancyInsteadOfInfiniteDeaths() {
        assertFalse(DamageEscapePolicy.shouldDormant(2));
        assertTrue(DamageEscapePolicy.shouldDormant(3));
    }
}
