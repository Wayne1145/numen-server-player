// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server.mcp;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * A tiny <b>stdio ↔ HTTP</b> MCP bridge, using only the JDK (no Minecraft, no third-party deps), so a
 * stdio-only MCP client (Claude Desktop, Hermes, any MCP host that spawns a stdio server) can reach
 * the {@link ServerMcpServer}. Each JSON-RPC frame arrives as a line on stdin, is POSTed to the HTTP
 * endpoint with the Bearer token, and the response is written back as a line on stdout (a 202
 * no-content notification response produces no line).
 *
 * <pre>
 *   java -cp numen-forge-1.20.1-*-all.jar \
 *     com.dwinovo.numen.server.mcp.StdioMcpAdapter http://127.0.0.1:25567/mcp &lt;bearer-token&gt;
 *   # or via env: NUMEN_MCP_URL / NUMEN_MCP_TOKEN
 * </pre>
 */
public final class StdioMcpAdapter {

    private StdioMcpAdapter() {}

    public static void main(String[] args) throws Exception {
        String url = args.length > 0 ? args[0] : env("NUMEN_MCP_URL", "http://127.0.0.1:25567/mcp");
        String token = args.length > 1 ? args[1] : env("NUMEN_MCP_TOKEN", "");

        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);

        String line;
        while ((line = in.readLine()) != null) {
            if (line.isBlank()) continue;
            HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofSeconds(30))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(line, StandardCharsets.UTF_8));
            if (!token.isEmpty()) b.header("Authorization", "Bearer " + token);
            HttpResponse<String> resp = client.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            String body = resp.body();
            if (body != null && !body.isEmpty()) out.println(body);
        }
    }

    private static String env(String key, String def) {
        String v = System.getenv(key);
        return v == null || v.isEmpty() ? def : v;
    }
}
