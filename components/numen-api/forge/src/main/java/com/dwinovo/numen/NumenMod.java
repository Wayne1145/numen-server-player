package com.dwinovo.numen;

import com.dwinovo.numen.network.NumenNetwork;
import com.dwinovo.numen.platform.ForgeNumenConfig;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegisterCommandsEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.ModLoadingContext;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.config.ModConfig;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;
import net.minecraftforge.fml.loading.FMLEnvironment;

/**
 * Forge mod entry for 1.20.4. Forge keeps separate mod and game event buses,
 * just like the NeoForge reference this was ported from — registration-type
 * events go on the mod bus (from the constructor here), while the per-tick /
 * world lifecycle events go on {@link MinecraftForge#EVENT_BUS}.
 *
 * <p>Networking is registered eagerly via {@code NumenNetwork.register()} — the
 * Forge {@code SimpleChannel} accepts message
 * registration during construction, so there is no deferred
 * "flush on RegisterPayloadHandlersEvent" dance like NeoForge required.
 */
@Mod(Constants.MOD_ID)
public class NumenMod {

    /** Server-side MCP endpoint, started on server-start when enabled in config; stopped on shutdown. */
    private static com.dwinovo.numen.server.mcp.ServerMcpServer mcpServer;

    public NumenMod() {
        IEventBus modBus = FMLJavaModLoadingContext.get().getModEventBus();

        // Register the TOML config spec — Forge handles file creation +
        // hot-reload from here on. SPEC is built in the ForgeNumenConfig static
        // initialiser (just data, no I/O), so referencing it now is safe.
        ModLoadingContext.get().registerConfig(ModConfig.Type.COMMON, ForgeNumenConfig.SPEC);

        // Build the SimpleChannel and register every payload eagerly.
        NumenNetwork.register();

        // Dev: /numen_summon — create a companion fake player at the caller.
        MinecraftForge.EVENT_BUS.addListener((RegisterCommandsEvent e) -> {
            com.dwinovo.numen.entity.NumenCommands.register(e.getDispatcher());
            // /numenctl — server-side actuator harness (simulated principal); the S2 test surface.
            com.dwinovo.numen.server.ServerActuatorCommands.register(e.getDispatcher());
        });
        // When an owner logs in, bring their dormant companions back.
        MinecraftForge.EVENT_BUS.addListener(NumenMod::onPlayerLoggedIn);
        MinecraftForge.EVENT_BUS.addListener(NumenMod::onPlayerChangedDimension);
        // On server start, bring back SERVER-OWNED (ownerless) companions from the registry — there is
        // no owner login to trigger them, so a pure-server AI player survives a restart with its .dat.
        // Also attach the server to the single ServerNumenActuator (the API/MCP/console control entry).
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.server.ServerStartedEvent e) -> {
            com.dwinovo.numen.entity.Companions.respawnAllServerOwned(e.getServer());
            // Load the control-layer config (policy + token→principal + mcp) and attach the actuator.
            java.nio.file.Path cfgPath = net.minecraftforge.fml.loading.FMLPaths.CONFIGDIR.get()
                    .resolve("numen").resolve("server_control.json");
            com.dwinovo.numen.server.ServerControlConfig cfg =
                    com.dwinovo.numen.server.ServerControlConfig.load(cfgPath).withGeneratedAdminTokenIfNeeded();
            com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.configure(cfg);
            com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.attach(e.getServer());
            // Optional server-side API agent (S5): same actuator + same lease as MCP; no key -> stays off.
            com.dwinovo.numen.server.agent.ServerApiAgentManager.INSTANCE.configure(
                    cfg.apiAgent, cfg.policy, com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE);
            // Start the server-side MCP endpoint only if explicitly enabled in config.
            if (cfg.mcp.enabled()) {
                try {
                    mcpServer = new com.dwinovo.numen.server.mcp.ServerMcpServer(cfg.mcp,
                            com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE, cfg.tokens, cfg.policy);
                    mcpServer.start();
                } catch (Exception ex) {
                    Constants.LOG.error("[numen-mcp] failed to start: {}", ex.toString());
                    mcpServer = null;
                }
            }
        });
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.server.ServerStoppingEvent e) -> {
            if (mcpServer != null) { mcpServer.stop(); mcpServer = null; }
            com.dwinovo.numen.server.agent.ServerApiAgentManager.INSTANCE.shutdown();
            com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.detach();
        });
        // 排程机器的心跳:每 tick 驱动全部同伴的竞价/任务/收尾 + actuator 的租约 TTL 清扫。
        MinecraftForge.EVENT_BUS.addListener((net.minecraftforge.event.TickEvent.ServerTickEvent e) -> {
            if (e.phase == net.minecraftforge.event.TickEvent.Phase.END) {
                com.dwinovo.numen.task.CompanionTickDispatcher.tick(e.getServer());
                com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.tick();   // TTL lease sweep
            }
        });
        // Control-lease cleanup: drop a companion's write lease the moment its body dies or leaves.
        com.dwinovo.numen.entity.CompanionLifecycle.onDeath(b ->
                com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.onCompanionGone(b.getUUID()));
        com.dwinovo.numen.entity.CompanionLifecycle.onRemove(b ->
                com.dwinovo.numen.server.ServerNumenActuatorImpl.INSTANCE.onCompanionGone(b.getUUID()));

        // Client init (key mappings / HUD / world-render path overlay) is wired
        // from the client class, only on the physical client.
        if (FMLEnvironment.dist == Dist.CLIENT) {
            NumenForgeClient.init(modBus);
        }

        CommonClass.init();
        Constants.LOG.info("Numen mod initialised on Forge.");
    }

    private static void onPlayerLoggedIn(PlayerEvent.PlayerLoggedInEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer player)) return;
        if (player instanceof com.dwinovo.numen.entity.NumenPlayer) return;  // not the companion itself
        MinecraftServer server = player.level().getServer();
        if (server != null) {
            com.dwinovo.numen.entity.Companions.respawnAllOwnedBy(server, player.getUUID());
            com.dwinovo.numen.entity.Companions.syncRosterToOwner(server, player);
        }
    }

    /** The companion crossed a portal on its own — tell its brain (ambient world event). */
    private static void onPlayerChangedDimension(PlayerEvent.PlayerChangedDimensionEvent event) {
        if (event.getEntity() instanceof com.dwinovo.numen.entity.NumenPlayer ap) {
            com.dwinovo.numen.entity.Companions.onDimensionChanged(ap);
        }
    }
}
