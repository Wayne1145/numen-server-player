package com.dwinovo.numen.core.brain;

import com.google.gson.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.zip.*;

/** 随服托管大脑；配置和数据不随资源升级覆盖，不执行系统级安装。 */
public final class ManagedBrain implements AutoCloseable {
    private final Path root;
    private volatile Process child;
    private volatile ServerSocket gate;
    private volatile Socket connection;
    private volatile boolean closed;
    private static final Gson JSON = new GsonBuilder().setPrettyPrinting().create();
    public ManagedBrain(Path root) { this.root = root.toAbsolutePath().normalize(); }

    public static void extract(InputStream input, Path destination) throws IOException {
        Files.createDirectories(destination);
        Path base = destination.toAbsolutePath().normalize();
        long total = 0;
        try (var zip = new ZipInputStream(input)) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                Path target = base.resolve(entry.getName()).normalize();
                if (!target.startsWith(base) || entry.getName().contains("\\") || entry.getName().startsWith("/"))
                    throw new IOException("unsafe archive path");
                for (Path p = target; p != null && p.startsWith(base); p = p.getParent())
                    if (Files.isSymbolicLink(p)) throw new IOException("symlink in managed archive destination");
                if (entry.isDirectory()) { Files.createDirectories(target); continue; }
                Files.createDirectories(target.getParent());
                Path temp = Files.createTempFile(target.getParent(), ".numen-", ".partial");
                try {
                    try (var out = Files.newOutputStream(temp)) {
                        byte[] buf = new byte[8192]; int n;
                        while ((n = zip.read(buf)) != -1) {
                            total += n;
                            if (total > 64L * 1024 * 1024) throw new IOException("archive too large");
                            out.write(buf, 0, n);
                        }
                    }
                    Files.move(temp, target, StandardCopyOption.REPLACE_EXISTING);
                } finally { Files.deleteIfExists(temp); }
            }
        }
    }

    private static void createOnly(Path path, String content) throws IOException {
        try {
            Files.writeString(path, content, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
            if (Files.getFileStore(path).supportsFileAttributeView("posix"))
                Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"));
        } catch (FileAlreadyExistsException ignored) { /* 服主配置保留 */ }
    }

    public static void prepare(Path root, InputStream payload) throws IOException {
        Files.createDirectories(root);
        boolean fresh = !Files.exists(root.resolve("server_control.json"));
        extract(payload, root.resolve("brain"));
        if (fresh) {
            // 每个服务器独立令牌和本机端口；不扩大破坏权限。
            String token = nonce(); int port;
            try (var s = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) { port = s.getLocalPort(); }
            JsonObject control = new JsonObject();
            var mcp = new JsonObject(); mcp.addProperty("enabled", true); mcp.addProperty("host", "127.0.0.1"); mcp.addProperty("port", port);
            control.add("mcp", mcp);
            var principals = new JsonArray(); var principal = new JsonObject();
            principal.addProperty("id", "managed-brain"); principal.addProperty("role", "ADMIN"); principal.addProperty("token", token);
            principals.add(principal); control.add("principals", principals);
            createOnly(root.resolve("server_control.json"), JSON.toJson(control));
        }
        var config = new JsonObject();
        config.addProperty("mode", fresh ? "managed" : "external");
        config.addProperty("python_executable", "");
        config.addProperty("llm_url", ""); config.addProperty("llm_model", "");
        config.addProperty("api_key_env", "NUMEN_LLM_KEY"); config.addProperty("api_key_file", "llm_key.txt");
        config.addProperty("mcp_principal_id", "managed-brain");
        config.addProperty("companions", ""); config.addProperty("poll_seconds", 15); config.addProperty("cooldown_seconds", 120);
        createOnly(root.resolve("managed_brain.json"), JSON.toJson(config));
        createOnly(root.resolve("llm_key.txt"), "");
    }

    private static String nonce() {
        byte[] b = new byte[32]; new SecureRandom().nextBytes(b);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(b);
    }
    private static String text(JsonObject o, String key) {
        return o.has(key) ? o.get(key).getAsString().trim() : "";
    }
    private String interpreter(JsonObject config) throws IOException {
        String explicit = text(config, "python_executable");
        if (!explicit.isBlank()) return explicit;
        if (!System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("win")) return "python3";
        if (!System.getProperty("os.arch").matches("(?i)(amd64|x86_64)"))
            throw new IOException("Windows runtime requires x64; configure python_executable for this architecture");
        Path runtime = root.resolve("python-3.13.16");
        Path exe = runtime.resolve("python.exe");
        if (!Files.exists(exe)) {
            try (var in = ManagedBrain.class.getResourceAsStream("/numen-brain/python-windows.zip")) {
                if (in == null) throw new IOException("bundled Windows Python runtime missing");
                extract(in, runtime);
                Files.writeString(runtime.resolve("python313._pth"), "python313.zip\n.\n../brain\n", StandardCharsets.UTF_8);
            }
        }
        return exe.toString();
    }

    /** 在后台线程启动：等待端口认证不阻塞服务器 tick；没有模型配置不产生子进程。 */
    public synchronized String start() throws Exception {
        if (closed) return "closed";
        if (child != null && child.isAlive()) return "already running";
        JsonObject config = JsonParser.parseString(Files.readString(root.resolve("managed_brain.json"))).getAsJsonObject();
        if (!text(config, "mode").equals("managed")) return "external mode; no managed process";
        String url = text(config, "llm_url"), model = text(config, "llm_model");
        String key = System.getenv(text(config, "api_key_env"));
        Path keyFile = root.resolve(text(config, "api_key_file")).normalize();
        if (!keyFile.startsWith(root)) throw new IOException("API key path outside config/numen");
        if (key == null || key.isBlank()) key = Files.isRegularFile(keyFile) ? Files.readString(keyFile).trim() : "";
        if (url.isBlank() || model.isBlank() || key.isBlank()) return "setup required: configure llm_url, llm_model and llm_key.txt (or key env)";
        URI endpoint = URI.create(url);
        if (!List.of("http", "https").contains(endpoint.getScheme()) || endpoint.getHost() == null || endpoint.getUserInfo() != null)
            throw new IOException("invalid model endpoint");
        JsonObject control = JsonParser.parseString(Files.readString(root.resolve("server_control.json"))).getAsJsonObject();
        JsonObject mcp = control.getAsJsonObject("mcp");
        if (mcp == null || !mcp.get("enabled").getAsBoolean() || !text(mcp, "host").equals("127.0.0.1"))
            throw new IOException("managed mode requires enabled MCP bound to 127.0.0.1");
        if (control.has("api_agent") && control.getAsJsonObject("api_agent").has("enabled") && control.getAsJsonObject("api_agent").get("enabled").getAsBoolean())
            throw new IOException("disable api_agent before managed mode (single brain owner)");
        String token = null;
        for (JsonElement e : control.getAsJsonArray("principals")) {
            var p = e.getAsJsonObject();
            if (text(p, "id").equals(text(config, "mcp_principal_id"))) token = text(p, "token");
        }
        if (token == null || token.isBlank()) throw new IOException("configured MCP principal missing");
        String python = interpreter(config);
        // 先验证真实解释器，拒绝 Windows Store 占位入口和过旧版本。
        var probe = new ProcessBuilder(python, "-c", "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)")
                .redirectError(ProcessBuilder.Redirect.DISCARD).redirectOutput(ProcessBuilder.Redirect.DISCARD).start();
        if (!probe.waitFor(10, TimeUnit.SECONDS)) { probe.destroyForcibly(); throw new IOException("Python probe timed out"); }
        if (probe.exitValue() != 0) throw new IOException("Python 3.10+ required");
        gate = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")); gate.setSoTimeout(10000);
        String challenge = nonce();
        var args = new ArrayList<>(List.of(python, "-X", "utf8", "-u", "managed_entry.py", "--parent-port", String.valueOf(gate.getLocalPort()),
                "--server-id", "managed", "--poll", String.valueOf(config.get("poll_seconds").getAsDouble()),
                "--cooldown", String.valueOf(config.get("cooldown_seconds").getAsDouble())));
        if (config.get("poll_seconds").getAsDouble() < 1 || config.get("cooldown_seconds").getAsDouble() < 0)
            throw new IOException("invalid polling settings");
        if (!text(config, "companions").isBlank()) { args.add("--companions"); args.add(text(config, "companions")); }
        var builder = new ProcessBuilder(args).directory(root.resolve("brain").toFile());
        var env = builder.environment(); env.keySet().removeIf(k -> k.startsWith("NUMEN_"));
        env.put("NUMEN_LLM_URL", url); env.put("NUMEN_LLM_MODEL", model); env.put("NUMEN_LLM_KEY", key);
        env.put("NUMEN_MCP_URL", "http://127.0.0.1:" + mcp.get("port").getAsInt() + "/mcp");
        env.put("NUMEN_MCP_TOKEN", token); env.put("NUMEN_PARENT_NONCE", challenge); env.put("PYTHONIOENCODING", "utf-8");
        Path logs = root.resolve("brain-logs"); Files.createDirectories(logs);
        builder.redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.appendTo(logs.resolve("managed.log").toFile()));
        try {
            child = builder.start();
            connection = gate.accept(); connection.setSoTimeout(10000);
            var reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
            if (!challenge.equals(reader.readLine())) throw new IOException("managed child authentication failed");
            connection.getOutputStream().write("ok\n".getBytes(StandardCharsets.UTF_8)); connection.getOutputStream().flush();
            gate.close(); gate = null;
            return "managed process started (pid=" + child.pid() + "); see config/numen/brain-logs/managed.log";
        } catch (Exception ex) { close(); throw ex; }
    }

    @Override public synchronized void close() {
        closed = true;
        try { if (connection != null) connection.close(); } catch (IOException ignored) {}
        try { if (gate != null) gate.close(); } catch (IOException ignored) {}
        if (child != null && child.isAlive()) {
            try { if (!child.waitFor(3, TimeUnit.SECONDS)) { child.destroy(); if (!child.waitFor(2, TimeUnit.SECONDS)) child.destroyForcibly(); } }
            catch (InterruptedException ex) { Thread.currentThread().interrupt(); child.destroyForcibly(); }
        }
    }
}
