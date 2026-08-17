package com.dwinovo.numen.core;

import com.dwinovo.numen.agent.skill.SkillRegistry;
import com.dwinovo.numen.core.pathing.cache.PathCaches;
import com.dwinovo.numen.core.task.ScanBlocksJob;
import net.minecraft.server.MinecraftServer;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.server.ServerStoppedEvent;
import net.minecraftforge.fml.ModList;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLEnvironment;

import java.nio.file.Path;

/**
 * Forge entry point for the numen-core tool pack. Registers the tools and task
 * runners into the numen-api engine, then wires the server-tick work its tools
 * need (budget-sliced block scans, the off-thread pathfinder's chunk snapshots).
 * The engine itself is brought up by the separate numen-api mod, which core
 * depends on.
 *
 * <p>Forge keeps separate mod and game event buses, just like the NeoForge
 * reference this was ported from — per-tick / world lifecycle events go on
 * {@link MinecraftForge#EVENT_BUS}.
 */
@Mod(Constants.MOD_ID)
public class NumenCoreForge {

    public NumenCoreForge() {
        NumenCore.init();

        MinecraftForge.EVENT_BUS.addListener(NumenCoreForge::onServerTickPost);
        // 服务器事件(玩家聊天/死亡)进入环形缓冲，供 MCP get_recent_events 读取。
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.ServerChatEvent e) ->
                com.dwinovo.numen.core.tools.RecentEventsTool.BUFFER.add(
                        "chat", e.getPlayer() == null ? "" : e.getPlayer().getName().getString(),
                        e.getRawText()));
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.entity.living.LivingDeathEvent e) -> {
            if (e.getEntity() instanceof net.minecraft.world.entity.player.Player player) {
                com.dwinovo.numen.core.tools.RecentEventsTool.BUFFER.add(
                        "death", player.getName().getString(),
                        player.getName().getString() + " died");
            }
        });
        // Mohist/Arclight 等混合端：聊天走 Bukkit 管道（AsyncPlayerChatEvent），
        // Forge 的 ServerChatEvent 不触发。用反射安全注册 Bukkit 监听器——
        // 纯 Forge 环境没有 org.bukkit 类，这里必须容忍 ClassNotFound。
        registerBukkitChatListener();
        // Release pathfinding chunk-ref snapshots when the server stops (don't pin an old world).
        MinecraftForge.EVENT_BUS.addListener((ServerStoppedEvent e) -> PathCaches.dropAll());
        // Debug verbs merged into the /numen root registered by the engine mod.
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.RegisterCommandsEvent e) ->
                com.dwinovo.numen.core.debug.DebugCommands.register(e.getDispatcher()));

        // Client-only: declare core's built-in skills, read in place from the
        // skills/ dir bundled in this jar. Skills feed the client-side LLM, so
        // this never runs on a dedicated server.
        if (FMLEnvironment.dist == Dist.CLIENT) {
            declareBundledSkills();
        }

        Constants.LOG.info("numen-core initialised on Forge.");
    }

    /**
     * 在 Mohist/Arclight（Forge+Bukkit 混合端）上补一条 Bukkit 聊天监听，
     * 因为这类服务端把玩家聊天交给 Bukkit 管道处理，Forge 的
     * {@code ServerChatEvent} 不再触发。用反射安全注册：
     * <ul>
     *   <li>有 Bukkit API（混合端）→ 注册 {@code AsyncPlayerChatEvent} 监听，
     *       把玩家名+原文写入同一事件缓冲（与 Forge 死亡事件共用）；</li>
     *   <li>纯 Forge（无 org.bukkit）→ 静默跳过，不影响其它功能。</li>
     * </ul>
     * <p>注意：Bukkit 的 {@code registerEvents(Listener, Plugin)} 靠扫描
     * {@code @EventHandler} 注解，动态代理拿不到注解；因此这里用
     * {@code registerEvent(Class, Listener, EventPriority, EventExecutor, Plugin)}
     * 重载 + {@code EventExecutor} 代理，绕开注解要求。监听器在 Bukkit 异步
     * 聊天线程执行；EventRingBuffer 内部已线程安全。
     */
    private static void registerBukkitChatListener() {
        try {
            Class<?> asyncChat = Class.forName("org.bukkit.event.player.AsyncPlayerChatEvent");
            Class<?> listenerIface = Class.forName("org.bukkit.event.Listener");
            Class<?> executorIface = Class.forName("org.bukkit.event.EventExecutor");
            Class<?> priorityEnum = Class.forName("org.bukkit.event.EventPriority");
            Class<?> bukkit = Class.forName("org.bukkit.Bukkit");
            Class<?> pluginClass = Class.forName("org.bukkit.plugin.Plugin");

            // Listener 占位实例（executor 才是真正处理逻辑）
            Object placeholder = java.lang.reflect.Proxy.newProxyInstance(
                    NumenCoreForge.class.getClassLoader(),
                    new Class<?>[]{listenerIface},
                    (p, m, a) -> null);
            // EventExecutor：execute(Listener, Event)
            Object executor = java.lang.reflect.Proxy.newProxyInstance(
                    NumenCoreForge.class.getClassLoader(),
                    new Class<?>[]{executorIface},
                    (p, m, a) -> {
                        if (m.getName().equals("execute") && a != null && a.length == 2
                                && asyncChat.isInstance(a[1])) {
                            try {
                                Object evt = a[1];
                                Object player = asyncChat.getMethod("getPlayer").invoke(evt);
                                String name = String.valueOf(
                                        player.getClass().getMethod("getName").invoke(player));
                                String message = String.valueOf(
                                        asyncChat.getMethod("getMessage").invoke(evt));
                                com.dwinovo.numen.core.tools.RecentEventsTool.BUFFER.add("chat", name, message);
                            } catch (Throwable ignored) {
                                // 取字段失败就不记这条，别让聊天监听影响游戏
                            }
                        }
                        return null;
                    });

            // 需要一个已注册的 Plugin 作为注册载体
            Object plugin = null;
            Object pm = bukkit.getMethod("getPluginManager").invoke(null);
            Object plugins = pm.getClass().getMethod("getPlugins").invoke(pm);
            if (plugins instanceof Object[] arr && arr.length > 0) {
                plugin = arr[0];
            }
            if (plugin == null) {
                Constants.LOG.info("[numen-core] Bukkit API present but no plugin to register chat listener — "
                        + "chat events may be missed on this hybrid server");
                return;
            }
            Object normal = Enum.valueOf((Class) priorityEnum, "NORMAL");
            pm.getClass().getMethod("registerEvent", Class.class, listenerIface,
                            priorityEnum, executorIface, pluginClass)
                    .invoke(pm, asyncChat, placeholder, normal, executor, plugin);
            Constants.LOG.info("[numen-core] registered Bukkit AsyncPlayerChatEvent listener (hybrid server)");
        } catch (ClassNotFoundException e) {
            // 纯 Forge 无 Bukkit —— 正常跳过
            Constants.LOG.info("[numen-core] no Bukkit API (pure Forge) — chat via ServerChatEvent only");
        } catch (Throwable t) {
            Constants.LOG.warn("[numen-core] Bukkit chat listener registration failed: {}",
                    t.toString());
        }
    }

    private static void declareBundledSkills() {
        Path root = ModList.get().getModFileById(Constants.MOD_ID).getFile().findResource("skills");
        if (root != null) {
            SkillRegistry.instance().declareBundled(root);
        } else {
            Constants.LOG.warn("[numen-core] no bundled skills/ dir found in jar");
        }
    }

    private static void onServerTickPost(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        MinecraftServer server = event.getServer();
        // 排程机器的心跳随机器归了 numen-api;core 只 tick 自己的工具配套。
        ScanBlocksJob.tick(server);
        PathCaches.serverTick(server);
        // Debug particles for pathing state, sent only to players with debug on.
        com.dwinovo.numen.core.debug.PathDebugRenderer.serverTick(server);
    }
}
