// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.EnumSet;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The server control policy — what the actuator is allowed to do, independent of any single call.
 * Immutable; built from config with restrictive defaults (the S3 posture):
 * <ul>
 *   <li>only {@link ToolCapability#SERVER_READ_ONLY} and {@link ToolCapability#SERVER_BODY_ACTION}
 *       enabled — <b>destructive off</b>, admin-management gated separately;</li>
 *   <li>overworld only; {@code minecraft} namespace only (no mod namespaces);</li>
 *   <li>bounded lease TTL, action distance, global concurrent tasks, and per-principal /
 *       per-companion call rates.</li>
 * </ul>
 * {@link #permitInvoke} combines a tool's {@link ToolCapability} with the principal's role and the
 * target dimension. The lease check, namespace-of-args check, rate limit, and budget are applied by
 * the actuator using this policy's parameters.
 */
public final class SecurityPolicy {

    private static final Pattern NAMESPACED = Pattern.compile("^([a-z0-9_.-]+):[a-z0-9_./-]+$");

    private final Set<ToolCapability> enabledCapabilities;
    private final Set<String> allowedDimensions;
    private final Set<String> allowedNamespaces;
    private final double maxActionDistance;
    private final long leaseTtlMs;
    private final int globalMaxConcurrentTasks;
    private final int maxCallsPerMinutePerPrincipal;
    private final int maxCallsPerMinutePerCompanion;

    public SecurityPolicy(Set<ToolCapability> enabledCapabilities, Set<String> allowedDimensions,
                          Set<String> allowedNamespaces, double maxActionDistance, long leaseTtlMs,
                          int globalMaxConcurrentTasks, int maxCallsPerMinutePerPrincipal,
                          int maxCallsPerMinutePerCompanion) {
        this.enabledCapabilities = EnumSet.copyOf(enabledCapabilities);
        this.allowedDimensions = Set.copyOf(allowedDimensions);
        this.allowedNamespaces = Set.copyOf(allowedNamespaces);
        this.maxActionDistance = maxActionDistance;
        this.leaseTtlMs = leaseTtlMs;
        this.globalMaxConcurrentTasks = globalMaxConcurrentTasks;
        this.maxCallsPerMinutePerPrincipal = maxCallsPerMinutePerPrincipal;
        this.maxCallsPerMinutePerCompanion = maxCallsPerMinutePerCompanion;
    }

    /** The restrictive default posture (destructive off, overworld+minecraft only). */
    public static SecurityPolicy defaults() {
        return new SecurityPolicy(
                EnumSet.of(ToolCapability.SERVER_READ_ONLY, ToolCapability.SERVER_BODY_ACTION),
                Set.of("minecraft:overworld"),
                Set.of("minecraft"),
                64.0,
                10 * 60 * 1000L,
                8,
                120,
                60);
    }

    public long leaseTtlMs() { return leaseTtlMs; }
    public double maxActionDistance() { return maxActionDistance; }
    public int globalMaxConcurrentTasks() { return globalMaxConcurrentTasks; }
    public int maxCallsPerMinutePerPrincipal() { return maxCallsPerMinutePerPrincipal; }
    public int maxCallsPerMinutePerCompanion() { return maxCallsPerMinutePerCompanion; }
    public boolean capabilityEnabled(ToolCapability c) { return enabledCapabilities.contains(c); }
    public boolean dimensionAllowed(String dim) { return allowedDimensions.contains(dim); }
    public boolean namespaceAllowed(String ns) { return allowedNamespaces.contains(ns); }

    /**
     * Decide whether {@code principal} may invoke {@code toolName} against a companion in
     * {@code dimension}. Combines the tool's capability, whether policy enables it, the principal's
     * role, and the dimension. The caller still enforces the lease (for control tools), the
     * namespace-of-args check ({@link #namespaceViolation}), and the rate limit.
     */
    public PolicyDecision permitInvoke(McpPrincipal principal, String toolName, String dimension) {
        ToolCapability cap = ToolCatalog.of(toolName);
        if (!cap.isServerExposable()) {
            return PolicyDecision.deny("tool_not_exposed", "tool '" + toolName + "' is not server-exposable");
        }
        if (!capabilityEnabled(cap)) {
            return PolicyDecision.deny("capability_disabled", cap + " is disabled by the server policy");
        }
        switch (principal.role()) {
            case READONLY -> {
                if (cap != ToolCapability.SERVER_READ_ONLY) {
                    return PolicyDecision.deny("readonly", "readonly principal may only call read-only tools");
                }
            }
            case OWNER -> {
                if (cap == ToolCapability.ADMIN_ONLY) {
                    return PolicyDecision.deny("admin_only", "tool '" + toolName + "' is admin-only");
                }
            }
            case ADMIN -> { /* admin may use any enabled, exposable capability */ }
        }
        if (dimension != null && !dimensionAllowed(dimension)) {
            return PolicyDecision.deny("dimension", "dimension '" + dimension + "' is not allowed by policy");
        }
        return PolicyDecision.allow(cap);
    }

    /**
     * Return the first namespaced id in the args JSON whose namespace is NOT allowed (e.g. a modded
     * block/item id when only {@code minecraft} is permitted), or {@code null} if all are allowed.
     * A best-effort scan of every string value (recursing objects/arrays).
     */
    public String namespaceViolation(JsonElement args) {
        if (args == null) return null;
        if (args.isJsonPrimitive() && args.getAsJsonPrimitive().isString()) {
            String s = args.getAsString();
            var m = NAMESPACED.matcher(s);
            if (m.matches()) {
                String ns = m.group(1);
                if (!namespaceAllowed(ns)) return ns;
            }
            return null;
        }
        if (args.isJsonObject()) {
            for (var e : ((JsonObject) args).entrySet()) {
                String v = namespaceViolation(e.getValue());
                if (v != null) return v;
            }
        } else if (args.isJsonArray()) {
            for (JsonElement el : (JsonArray) args) {
                String v = namespaceViolation(el);
                if (v != null) return v;
            }
        }
        return null;
    }
}
