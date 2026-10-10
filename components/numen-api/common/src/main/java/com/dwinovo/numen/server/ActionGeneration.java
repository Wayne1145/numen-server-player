// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
import java.util.UUID;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.nio.file.*;
/** 持久取消围栏：服务器重启不误取消未完成回合，旧请求不能跨显式 cancel。 */
public final class ActionGeneration {
    private static final Map<UUID,Long> VALUES=new ConcurrentHashMap<>();
    private static Path root;
    public static synchronized void configure(Path directory) { root=directory;VALUES.clear(); }
    public static synchronized long current(UUID id) {
        return VALUES.computeIfAbsent(id,key -> {
            if(root==null)return 0L;
            Path file=root.resolve(id+".generation");
            try { return Files.exists(file)?Long.parseLong(Files.readString(file)):0L; }
            catch(Exception ex) { throw new IllegalStateException("cancel fence unreadable",ex); }
        });
    }
    public static synchronized void cancel(UUID id) {
        long next=current(id)+1;
        if(root!=null) {
            try {
                Files.createDirectories(root);
                Path file=root.resolve(id+".generation"),tmp=root.resolve(id+".generation.tmp");
                try(var channel=java.nio.channels.FileChannel.open(tmp,StandardOpenOption.CREATE,
                        StandardOpenOption.TRUNCATE_EXISTING,StandardOpenOption.WRITE)) {
                    channel.write(java.nio.ByteBuffer.wrap(Long.toString(next).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
                    channel.force(true);
                }
                Files.move(tmp,file,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
            } catch(Exception ex) { throw new IllegalStateException("cancel fence cannot persist",ex); }
        }
        VALUES.put(id,next);
    }
    public static boolean matches(UUID id,long generation) { return current(id)==generation; }
}
