package com.dwinovo.numen.core.tools;

import com.dwinovo.numen.agent.tool.NumenTool;
import com.dwinovo.numen.task.TaskResult;
import com.dwinovo.numen.entity.NumenPlayer;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.ChatType;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.PlayerChatMessage;
import net.minecraft.server.MinecraftServer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * send_chat：让假人在游戏聊天框以自己名字说一句话。
 *
 * <p>服务端专用改造后，假人没有客户端 UI 可供"说话"；本工具直接走服务端
 * 原生的 {@code PlayerList.broadcastChatMessage}，以假人的显示名广播一条真实
 * 聊天消息（像玩家按回车说话一样出现在聊天框）。这是"假人自由对话"的第一步：
 * 先让它能发出消息，后续 Agent 模式再把模型回复桥接进来。
 */
public final class ChatTool implements NumenTool {

    private static final int MAX_TEXT = 256;

    @Override
    public String name() {
        return "send_chat";
    }

    @Override
    public String description() {
        return "让假人在游戏聊天框以自己名字说一句话（真实玩家聊天广播）。"
                + "参数 text 是要发送的聊天内容。"
                + "当你需要向服务器上的玩家打招呼、汇报、交流时使用。"
                + "注意：这只是单向发言，不包含任何回复逻辑。";
    }

    @Override
    public Map<String, Object> parameterSchema() {
        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type", "object");
        Map<String, Object> props = new LinkedHashMap<>();
        Map<String, Object> text = new LinkedHashMap<>();
        text.put("type", "string");
        text.put("description", "要发送的聊天消息内容（最长 256 字符）");
        props.put("text", text);
        schema.put("properties", props);
        List<String> required = new ArrayList<>();
        required.add("text");
        schema.put("required", required);
        return schema;
    }

    @Override
    public void onServerCall(String toolCallId, JsonObject args,
                             NumenPlayer companion, Consumer<String> reply) {
        String text = args.has("text") ? args.get("text").getAsString().trim() : "";
        if (text.isEmpty()) {
            reply.accept(TaskResult.fail("text is required").toJson());
            return;
        }
        if (text.length() > MAX_TEXT) {
            text = text.substring(0, MAX_TEXT);
        }
        // 1.20.1 的 ServerPlayer 无 getServer() 方法，用 level().getServer()。
        MinecraftServer server = companion.level().getServer();
        if (server == null) {
            reply.accept(TaskResult.fail("no server attached").toJson());
            return;
        }
        try {
            // 真实玩家聊天广播：以假人显示名发一条聊天消息。
            // 1.20.1 的 ChatType$Bound 没有 createBoundSender，用 public 三参构造器。
            var registry = server.registryAccess().registryOrThrow(Registries.CHAT_TYPE);
            var chatType = registry.getOrThrow(ChatType.CHAT);
            var bound = new ChatType.Bound(chatType, companion.getDisplayName(), Component.empty());
            PlayerChatMessage msg = PlayerChatMessage.system(text);
            server.getPlayerList().broadcastChatMessage(msg, companion, bound);
            // TaskResult.toJson() 用 Gson 转义，text 含引号/反斜杠/换行也不会破坏 JSON。
            reply.accept(TaskResult.ok("said: " + text).toJson());
        } catch (Exception ex) {
            // 兜底：系统消息广播，仍然带假人名字。
            server.getPlayerList().broadcastSystemMessage(
                    Component.literal("[" + companion.getName().getString() + "] " + text), false);
            reply.accept(TaskResult.ok("said (system): " + text).toJson());
        }
    }
}
