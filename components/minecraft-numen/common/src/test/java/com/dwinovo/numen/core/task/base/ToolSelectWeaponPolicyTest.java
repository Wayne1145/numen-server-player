package com.dwinovo.numen.core.task.base;

import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 自动防卫不能消耗唯一生产工具的回归测试。 */
class ToolSelectWeaponPolicyTest {
    @Test
    void productionToolsAreNotAutomaticCombatWeapons() {
        assertEquals(0.0, ToolSelect.combatWeaponDamage(new ItemStack(Items.IRON_PICKAXE)));
        assertEquals(0.0, ToolSelect.combatWeaponDamage(new ItemStack(Items.IRON_SHOVEL)));
        assertEquals(0.0, ToolSelect.combatWeaponDamage(new ItemStack(Items.IRON_HOE)));
    }

    @Test
    void swordAndAxeRemainEligibleWeapons() {
        assertTrue(ToolSelect.combatWeaponDamage(new ItemStack(Items.IRON_SWORD)) > 0.0);
        assertTrue(ToolSelect.combatWeaponDamage(new ItemStack(Items.IRON_AXE)) > 0.0);
    }
}
