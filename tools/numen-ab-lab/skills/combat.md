# 打怪与自保
- 先 scan_nearby_entities(type_filter=hostile) 拿到怪物列表（entity_id、距离、种类、HP）。
- 用 melee_attack(entity_ids=[...]) 逐个/批量击杀；工具自带追踪寻路、武器切换、拾取掉落。
- 打怪前 get_self_status 确认血量和装备；血量低（<10）先逃跑或进食，不要硬拼。
- 同时面对多只怪物时先杀最近的/远程的（骷髅）优先。
- 被怪物攻击时先 get_self_status：只有背包里有合格近战武器（剑、斧或带攻击属性的模组武器）才调用 melee_attack。镐、锹、锄是受保护生产工具，不算武器；没有武器时直接 goto 拉开距离，不要追怪。
- 骷髅怕近战贴脸（近战比射箭快），苦力怕要保持距离（它会爆炸），僵尸直接砍。
- 打完用 collect_items 收掉落，重要战利品用 remember_block 记录。
