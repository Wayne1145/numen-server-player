// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
/** 派发原点固定，tick 前限制自动选目标/施工寻路的实际活动范围。 */
public final class ActionBounds {
    private record Bound(String dim,double x,double y,double z,double radius) {}
    private static final Map<UUID,Bound> BOUNDS=new ConcurrentHashMap<>();
    public static void register(UUID id,String dim,double x,double y,double z,double radius) {
        BOUNDS.put(id,new Bound(dim,x,y,z,radius));
    }
    public static boolean violates(UUID id,String dim,double x,double y,double z) {
        Bound b=BOUNDS.get(id);
        if(b==null)return false;
        return !b.dim.equals(dim) || !Double.isFinite(x+y+z)
            || Math.sqrt((x-b.x)*(x-b.x)+(y-b.y)*(y-b.y)+(z-b.z)*(z-b.z))>b.radius;
    }
    public static void clear(UUID id) { BOUNDS.remove(id); }
}
