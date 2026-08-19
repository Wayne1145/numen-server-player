package com.dwinovo.numen.core.task.base;

import com.google.common.collect.Multimap;
import com.dwinovo.numen.entity.NumenPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.ai.attributes.Attribute;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.PickaxeItem;
import net.minecraft.world.item.ShovelItem;
import net.minecraft.world.item.HoeItem;
import net.minecraft.world.level.block.state.BlockState;

/**
 * The two "swap the best implement into the main hand" helpers, unified from
 * the duplicated block-breaking and combat inventory scans.
 *
 * <p>Both scan the WHOLE inventory (not just the hotbar) and swap via
 * {@link NumenPlayer#holdInHand(int)} so the selected item matches the action
 * the task is about to perform.
 */
public final class ToolSelect {

    private ToolSelect() {}

    /**
     * Hold the best implement for breaking {@code state}: among tools that beat
     * the bare hand, one that actually harvests the block (correct tier when the
     * block gates its drops) outranks a merely faster one.
     */
    public static void holdBestTool(NumenPlayer p, BlockState state) {
        Inventory inv = p.getInventory();
        boolean tierGated = state.requiresCorrectToolForDrops();
        int harvest = -1, any = -1;
        float harvestSpeed = 1.0f, anySpeed = 1.0f;
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack s = inv.getItem(i);
            float spd = s.getDestroySpeed(state);
            if (spd <= 1.0f) continue;
            if (!tierGated || s.isCorrectToolForDrops(state)) {
                if (spd > harvestSpeed) { harvestSpeed = spd; harvest = i; }
            } else if (spd > anySpeed) {
                anySpeed = spd;
                any = i;
            }
        }
        int best = harvest >= 0 ? harvest : any;
        if (best >= 0) {
            p.holdInHand(best);
        }
    }

    /**
     * Hold the highest melee-attack-damage item from the whole inventory.
     */
    public static void holdBestWeapon(NumenPlayer p) {
        Inventory inv = p.getInventory();
        int best = -1;
        double bestDmg = 0.0;
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack s = inv.getItem(i);
            double d = combatWeaponDamage(s);
            if (d > bestDmg) {
                bestDmg = d;
                best = i;
            }
        }
        if (best >= 0) {
            p.holdInHand(best);
        }
    }

    /** 是否至少携带一件可由战斗策略使用的合法近战武器。 */
    public static boolean hasCombatWeapon(Iterable<ItemStack> stacks) {
        for (ItemStack stack : stacks) {
            if (combatWeaponDamage(stack) > 0.0) return true;
        }
        return false;
    }

    /**
     * The flat main-hand attack damage an item grants. A block or food scores 0,
     * so it is never chosen over a real weapon.
     */
    /** 战斗伤害分。镐、锹、锄属于生产工具，即使带攻击加成也不能被
     * 自动防卫拿去消耗耐久；剑、斧及带原生攻击属性的模组武器仍兼容。 */
    public static double combatWeaponDamage(ItemStack stack) {
        if (stack.isEmpty()) return 0.0;
        if (stack.getItem() instanceof PickaxeItem
                || stack.getItem() instanceof ShovelItem
                || stack.getItem() instanceof HoeItem) {
            return 0.0;
        }
        // 1.20.1: attribute modifiers come from the item's per-slot Multimap, not a component.
        Multimap<Attribute, AttributeModifier> mods = stack.getAttributeModifiers(EquipmentSlot.MAINHAND);
        double sum = 0.0;
        for (AttributeModifier m : mods.get(Attributes.ATTACK_DAMAGE)) {
            if (m.getOperation() == AttributeModifier.Operation.ADDITION) {
                sum += m.getAmount();
            }
        }
        return sum;
    }
}
