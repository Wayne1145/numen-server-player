package com.dwinovo.numen.core.tools;

import com.dwinovo.numen.agent.tool.Schema;
import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.entity.NumenPlayer;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.levelgen.structure.Structure;

import java.util.Map;
import java.util.function.Consumer;

/**
 * SAMPLE (raw-NumenTool style, no @NumenAction). A query tool: runs on the body
 * server-side and replies in place. No arguments → an empty schema. The body of
 * {@link #onServerCall} is just the perception logic; nothing reflective.
 */
public final class GetSelfStatusTool implements NumenTool {

    @Override
    public String name() {
        return "get_self_status";
    }

    @Override
    public String description() {
        // The reflex overview rides THIS description (constitution §6): numen-api
        // exposes no system-prompt injection channel to core, but every request
        // re-reads tool descriptions, so the model sees the current roster each
        // turn. Dynamic on purpose — switched-off reflexes drop out of the text.
        String base = "Read your complete status in one call: name, game mode, HP / max HP, "
                + "hunger / saturation, position, dimension, biome, the structures you "
                + "are standing in, equipment, your full backpack inventory, and movement state. "
                + "ALWAYS call this before combat or planning decisions. No arguments.";
        String overview = com.dwinovo.numen.task.reflex.ReflexRegistry.overview();
        return overview.isEmpty() ? base : base + "\n\n" + overview;
    }

    @Override
    public Map<String, Object> parameterSchema() {
        return Schema.none();
    }

    @Override
    public void onServerCall(String toolCallId, JsonObject args, NumenPlayer self, Consumer<String> reply) {
        JsonObject root = new JsonObject();
        root.addProperty("entity_id", self.getId());
        root.addProperty("name", self.getName().getString());
        root.addProperty("game_mode", self.gameMode.getGameModeForPlayer().getName());
        root.addProperty("hp", self.getHealth());
        root.addProperty("max_hp", self.getMaxHealth());
        root.addProperty("hunger", self.getFoodData().getFoodLevel());
        root.addProperty("saturation", self.getFoodData().getSaturationLevel());

        JsonObject pos = new JsonObject();
        pos.addProperty("x", self.getX());
        pos.addProperty("y", self.getY());
        pos.addProperty("z", self.getZ());
        root.add("position", pos);

        root.addProperty("dimension", self.level().dimension().location().toString());
        root.addProperty("biome", self.level().getBiome(self.blockPosition())
                .unwrapKey().map(k -> k.location().toString()).orElse("unknown"));

        JsonArray structures = new JsonArray();
        if (self.level() instanceof ServerLevel sl) {
            Registry<Structure> reg = sl.registryAccess().registryOrThrow(Registries.STRUCTURE);
            for (Structure s : sl.structureManager().getAllStructuresAt(self.blockPosition()).keySet()) {
                ResourceLocation key = reg.getKey(s);
                if (key != null) structures.add(key.toString());
            }
        }
        root.add("structures", structures);

        JsonObject equipment = new JsonObject();
        for (EquipmentSlot slot : EquipmentSlot.values()) {
            ItemStack s = self.getItemBySlot(slot);
            if (s.isEmpty()) continue;
            JsonObject o = new JsonObject();
            o.addProperty("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
            if (s.getCount() > 1) o.addProperty("count", s.getCount());
            equipment.add(slot.getName(), o);
        }
        root.add("equipment", equipment);

        var inv = self.getInventory();
        JsonArray items = new JsonArray();
        int used = 0;
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack s = inv.getItem(i);
            if (s.isEmpty()) continue;
            used++;
            JsonObject o = new JsonObject();
            o.addProperty("slot", i);
            o.addProperty("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
            o.addProperty("count", s.getCount());
            items.add(o);
        }
        JsonObject inventory = new JsonObject();
        inventory.add("items", items);
        inventory.addProperty("slots_used", used);
        inventory.addProperty("slots_total", inv.getContainerSize());
        root.add("inventory", inventory);

        root.add("target", JsonNull.INSTANCE);
        root.addProperty("on_ground", self.onGround());
        root.addProperty("in_water", self.isInWater());
        // 头顶方块：玩家实体占 2 格，feet.y+2 即头顶那格。
        // 用于让模型知道"头上有无天花板/树冠"，避免撞头式寻路。
        var headState = self.level().getBlockState(
                new net.minecraft.core.BlockPos(self.blockPosition().getX(),
                        self.blockPosition().getY() + 2, self.blockPosition().getZ()));
        root.addProperty("block_above",
                net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(headState.getBlock()).toString());
        // Remaining breath — the one stat whose absence let a body drown while its
        // mind calmly planned an 870-block trip (frozen-ocean death, 2026-07-15).
        root.addProperty("air", self.getAirSupply() + "/" + self.getMaxAirSupply() + " ticks");
        root.addProperty("in_lava", self.isInLava());

        // Server-fork addition: body health / degraded state. A body that failed its movement tick
        // repeatedly is paused (degraded) with its tasks stopped — surface it so the driver can see it.
        JsonObject bodyHealth = new JsonObject();
        bodyHealth.addProperty("degraded", self.isDegraded());
        bodyHealth.addProperty("consecutive_tick_errors", self.consecutiveTickErrors());
        if (!self.lastTickError().isEmpty()) {
            bodyHealth.addProperty("last_tick_error", self.lastTickError());
        }
        root.add("body_health", bodyHealth);

        reply.accept(root.toString());
    }
}
