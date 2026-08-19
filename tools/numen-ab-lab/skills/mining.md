# 挖矿
- 直接调用 mine 工具：block_ids 里包含同族变体（如 minecraft:iron_ore 和 minecraft:deepslate_iron_ore），count 是物品数。
- 挖矿前先 get_self_status 看背包里有没有合适等级的镐。原版采收链必须逐级处理：木镐不能采铁；有 3 圆石/深板岩圆石 + 2 木棍时先 craft stone_pickaxe，石镐才能采铁；不要因缺铁镐而反复尝试“先采铁再做铁镐”的循环。
- scan_blocks / locate_biome / locate_structure 现在会返回可轮询的 qN；可用于确认远处资源，但普通资源采集仍优先直接 mine，避免多余扫描。
- 背包满导致 craft 返回 result doesn't fit 时，先 drop_items 丢弃一整格低价值杂物，再重试；不要丢工具、铁、燃料、工作台或稀有模组物品。
- 挖完重要矿脉后用 remember_block 记录坐标，供以后 recall_blocks 复用。
- 遇到岩浆/水要绕行，不硬挖脚下；被怪物攻击先反击或逃跑保命。
- 天黑或低血量时先处理生存状态（进食/治疗），再继续挖。
