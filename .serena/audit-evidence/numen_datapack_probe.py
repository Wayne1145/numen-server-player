"""新 datapack 改动的纯逻辑等价性核对，不改世界。"""
# 原版先筛 OnGround 再 limit=1；覆盖版先 limit=1 再检查 OnGround。
entities=[{'tag':True,'on_ground':False},{'tag':True,'on_ground':True}]
original=next((x for x in entities if x['tag'] and x['on_ground']),None)
first=next((x for x in entities if x['tag']),None)
override=first if first and first['on_ground'] else None
print({'original_fires_grounded_entity':original is not None,'override_fires_grounded_entity':override is not None,'equivalent':original==override})
