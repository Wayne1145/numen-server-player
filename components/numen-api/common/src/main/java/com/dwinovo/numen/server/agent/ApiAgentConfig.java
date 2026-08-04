// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.agent;

import com.dwinovo.numen.Constants;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Settings for the OPTIONAL server-side API agent (S5). Disabled by default. The model API key is
 * <b>never</b> stored in the main config, the repo, logs, or anything sent to clients — it is
 * resolved at use-time from, in order:
 * <ol>
 *   <li>an environment variable ({@code api_key_env}, default {@code NUMEN_API_KEY});</li>
 *   <li>a server-local file ({@code api_key_file}, e.g. {@code config/numen/api_key.txt} — add it
 *       to .gitignore; first line is the key).</li>
 * </ol>
 * No key resolved → the agent stays gracefully disabled while MCP keeps working (the acceptance
 * split). {@link #resolveApiKey()} is the only reader; callers must never log its return value.
 */
public record ApiAgentConfig(boolean enabled, String provider, String model, String baseUrl,
                             String reasoningEffort, String apiKeyEnv, String apiKeyFile,
                             int maxTurns, int requestTimeoutSeconds) {

    public static ApiAgentConfig disabledDefault() {
        return new ApiAgentConfig(false, "openai", "", "", "auto",
                "NUMEN_API_KEY", "", 8, 120);
    }

    /** The API key from env var first, then the server-local key file; null if neither yields one. */
    public String resolveApiKey() {
        String env = apiKeyEnv == null || apiKeyEnv.isBlank() ? "NUMEN_API_KEY" : apiKeyEnv;
        String v = System.getenv(env);
        if (v != null && !v.isBlank()) return v.trim();
        if (apiKeyFile != null && !apiKeyFile.isBlank()) {
            try {
                Path p = Path.of(apiKeyFile);
                if (Files.isRegularFile(p)) {
                    String line = Files.readString(p, StandardCharsets.UTF_8).lines()
                            .filter(s -> !s.isBlank()).findFirst().orElse("");
                    if (!line.isBlank()) return line.trim();
                }
            } catch (Exception ex) {
                Constants.LOG.warn("[numen-agent] could not read api_key_file {}: {}", apiKeyFile, ex.toString());
            }
        }
        return null;
    }

    /** Enabled AND a key is actually resolvable right now. */
    public boolean isUsable() {
        return enabled && resolveApiKey() != null;
    }
}
