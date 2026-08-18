import tempfile
import unittest
import uuid
from pathlib import Path

from action_brain import (
    ActionBrainRuntime,
    CompanionRunLock,
    RecoveryRequired,
    TurnCheckpoint,
    execute_tool_exact,
    resolve_companion_id,
)
from persistent_brain import BrainSessionStore


class FakeMcp:
    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = list(statuses or [])

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "acquire_control":
            return {"lease_id": "lease-1"}
        if name == "release_control":
            return "released"
        if name == "goto":
            return {"success": True, "data": {"task_id": "t9", "async": True}}
        if name == "task_status":
            return self.statuses.pop(0)
        if name == "get_self_status":
            return {"name": arguments["companion"], "position": {"x": 6.2, "y": 100, "z": 0.5}}
        return {"success": True}


class NeverCalledMcp:
    """模拟服务器：可用工具在 requires_control 之外不存在（模型误调旧工具名）。"""

    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "acquire_control":
            return {"lease_id": "lease-1"}
        return {"success": True}


class CompanionBindingTest(unittest.TestCase):
    def test_only_one_process_lock_can_own_a_companion_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain.lock"
            first = CompanionRunLock(path)
            second = CompanionRunLock(path)
            with first:
                with self.assertRaisesRegex(BlockingIOError, "already running"):
                    with second:
                        self.fail("第二个同伴大脑不应取得锁")
            with second:
                self.assertTrue(path.exists())

    def test_resolves_exact_name_to_uuid_from_live_listing(self):
        companion_id = str(uuid.uuid4())
        listing = f"Other id={uuid.uuid4()} [live]\nABBrain id={companion_id} [live]"
        self.assertEqual(companion_id, resolve_companion_id(listing, "ABBrain"))

    def test_rejects_missing_or_ambiguous_live_identity(self):
        same = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_companion_id("Other id=" + same + " [live]", "ABBrain")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_companion_id(
                f"ABBrain id={same} [live]\nABBrain id={uuid.uuid4()} [live]", "ABBrain"
            )


class ExactTaskExecutionTest(unittest.TestCase):
    def test_release_failure_does_not_lose_dispatch_task_id(self):
        mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
        release_calls = 0
        original_call = mcp.call

        def flaky_release(name, arguments):
            nonlocal release_calls
            if name == "release_control":
                release_calls += 1
                raise RuntimeError("network hiccup on release")
            return original_call(name, arguments)

        mcp.call = flaky_release
        observed = []
        outcome = execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
            on_dispatched=lambda dispatch, task_id: observed.append(task_id),
        )

        self.assertEqual("t9", observed[0])
        self.assertEqual("t9", outcome["task_id"])
        self.assertEqual("done", outcome["terminal"]["state"])
        self.assertEqual(1, release_calls)

    def test_write_action_uses_ephemeral_lease_and_exact_terminal_task_id(self):
        mcp = FakeMcp([
            {"state": "running", "task_id": "t9", "result": None},
            {"state": "done", "task_id": "t9", "result": {"success": True}},
        ])
        outcome = execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
        )
        self.assertEqual("done", outcome["terminal"]["state"])
        self.assertEqual(
            ["acquire_control", "goto", "release_control", "task_status", "task_status"],
            [name for name, _ in mcp.calls],
        )
        status_calls = [args for name, args in mcp.calls if name == "task_status"]
        self.assertEqual(["t9", "t9"], [args["task_id"] for args in status_calls])

    def test_dispatch_hook_receives_task_id_before_terminal_polling(self):
        mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
        observed = []
        execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
            on_dispatched=lambda dispatch, task_id: observed.append((dispatch, task_id)),
        )
        self.assertEqual("t9", observed[0][1])
        self.assertEqual("t9", observed[0][0]["data"]["task_id"])

    def test_idle_is_not_accepted_as_terminal_success(self):
        mcp = FakeMcp([{"state": "idle", "task_id": None, "result": None}])
        with self.assertRaisesRegex(RuntimeError, "idle"):
            execute_tool_exact(
                mcp,
                companion="ABBrain",
                name="goto",
                arguments={"x": 6.5, "y": None, "z": 0.5},
                requires_control=True,
                sleep=lambda _: None,
                timeout_seconds=0.01,
            )

    def test_error_is_terminal_state(self):
        # 回归：任务完成后服务端清理记录，task_status 返回 error("unknown task_id")。
        # 此前轮询到 120s 超时，回合挂死；error 现在视为终态直接收束。
        mcp = FakeMcp([{"state": "error", "task_id": "t9",
                        "detail": "unknown task_id: t9", "result": None}])
        outcome = execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
            timeout_seconds=5,
        )
        self.assertEqual("t9", outcome["task_id"])
        self.assertEqual("error", outcome["terminal"]["state"])


class PersistentActionTurnTest(unittest.TestCase):
    def test_local_tool_runs_without_mcp_or_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp()
            answers = iter([
                '{"type":"tool","name":"load_skill","arguments":{"name":"mining"}}',
                '{"type":"final","content":"技能已加载。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "load_skill", "parameters": {}}],
                requires_control={},
                local_tools={"load_skill": lambda args: {"success": True, "skill": args["name"]}},
            )
            result = runtime.run_turn("学习挖矿技能")

            self.assertEqual("技能已加载。", result["final"])
            self.assertEqual([], mcp.calls, "本地工具不应触碰 MCP/租约")
            persisted = store.messages()
            self.assertIn("load_skill", persisted[1]["content"])
            self.assertIn('"skill": "mining"', persisted[2]["content"])
            self.assertFalse(checkpoint.path.exists())

    def test_mcp_tool_and_local_tool_mix_in_one_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                '{"type":"tool","name":"load_skill","arguments":{"name":"mining"}}',
                '{"type":"final","content":"完成。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "goto", "parameters": {}}, {"name": "load_skill", "parameters": {}}],
                requires_control={"goto": True},
                local_tools={"load_skill": lambda args: {"success": True, "skill": args["name"]}},
                sleep=lambda _: None,
            )
            result = runtime.run_turn("先走再学")

            self.assertEqual("完成。", result["final"])
            self.assertEqual(2, len(result["actions"]))
            self.assertEqual("goto", result["actions"][0]["tool"])
            self.assertEqual("load_skill", result["actions"][1]["tool"])

    def test_repeated_same_tool_decision_is_blocked_to_break_loop(self):
        # 回归：模型反复派发同一异步任务（同工具+同参数）时会陷入循环
        # （execute_tool_exact 同步等待超时/轮次超限）。第二次相同决策应被
        # 循环检测拦截，注入提示引导收敛，而不是再次 dispatch。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                # 第二次完全相同 → 应被拦截，注入提示
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                '{"type":"final","content":"已到达。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
            )
            result = runtime.run_turn("向东走六格")

            self.assertEqual("已到达。", result["final"])
            goto_calls = [args for name, args in mcp.calls if name == "goto"]
            self.assertEqual(1, len(goto_calls),
                             "重复的相同工具调用应被循环检测拦截，只 dispatch 一次")
            self.assertFalse(checkpoint.path.exists())

    def test_duplicate_tool_correction_is_visible_to_next_model_call(self):
        # 循环保护不能只拦截调用；纠正提示必须进入下一轮模型上下文。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"z":0.5}}',
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"z":0.5}}',
                '{"type":"final","content":"已停止重复"}',
            ])
            observed = []

            def model(messages, _tools):
                observed.append(messages)
                return next(answers)

            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-correction"),
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=mcp, companion="ABBrain", model=model,
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True}, local_tools={},
                sleep=lambda _: None, max_rounds=6,
            )
            result = runtime.run_turn("走过去")
            self.assertEqual("已停止重复", result["final"])
            third_context = "\n".join(m.get("content", "") for m in observed[2])
            self.assertIn("不要再派发重复的工具调用", third_context)

    def test_different_args_same_tool_not_blocked(self):
        # 同一工具但不同参数（不同目标）是合法调用，不应被循环检测误伤。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp([
                {"state": "done", "task_id": "t9", "result": {"success": True}},
                {"state": "done", "task_id": "t9", "result": {"success": True}},
            ])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                '{"type":"tool","name":"goto","arguments":{"x":7.5,"y":null,"z":0.5}}',
                '{"type":"final","content":"完成。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
            )
            result = runtime.run_turn("走走")

            self.assertEqual("完成。", result["final"])
            goto_calls = [args for name, args in mcp.calls if name == "goto"]
            self.assertEqual(2, len(goto_calls), "不同参数的两个 goto 都合法")

    def test_same_tool_and_args_allowed_after_intervening_world_change(self):
        # 回归真实铁镐链：第一次 craft 因无工作台失败，随后 build 成功改变
        # 世界状态，再次 craft 相同参数必须放行，不能被历史级重复保护永久拉黑。
        class StatefulMcp(FakeMcp):
            def call(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "acquire_control":
                    return {"lease_id": "lease-1"}
                if name == "release_control":
                    return "released"
                if name == "craft":
                    craft_count = len([x for x in self.calls if x[0] == "craft"])
                    if craft_count == 1:
                        raise RuntimeError(
                            'MCP tool call failed: {"success":false,"message":"needs crafting table"}')
                    return {"success": True, "message": "crafted iron_pickaxe"}
                if name == "build":
                    return {"success": True, "message": "placed crafting_table"}
                return {"success": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mcp = StatefulMcp()
            answers = iter([
                '{"type":"tool","name":"craft","arguments":{"item_id":"minecraft:iron_pickaxe"}}',
                '{"type":"tool","name":"build","arguments":{"blocks":[{"block_id":"minecraft:crafting_table","x":1,"y":2,"z":3}]}}',
                '{"type":"tool","name":"craft","arguments":{"item_id":"minecraft:iron_pickaxe"}}',
                '{"type":"final","content":"铁镐完成"}',
            ])
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-state-change"),
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "craft", "parameters": {}},
                       {"name": "build", "parameters": {}}],
                requires_control={"craft": True, "build": True},
                local_tools={}, sleep=lambda _: None, max_rounds=8,
            )
            result = runtime.run_turn("做铁镐")
            self.assertEqual("铁镐完成", result["final"])
            self.assertEqual(2, len([x for x in mcp.calls if x[0] == "craft"]))

    def test_consecutive_empty_tool_results_trigger_early_final(
            self):
        # 回归：感知工具返回无 task_id 的"queued 空包"(terminal=None)，
        # 模型永远拿不到结果会无限重试。连续达到阈值应主动 early_final
        # 收束，而不是耗到 max_rounds 超限。
        class QueuedMcp:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "acquire_control":
                    return {"lease_id": "lease-1"}
                if name == "release_control":
                    return "released"
                # 模仿 locate_biome/scan_blocks：排队但不给结果、无 task_id
                return {"success": True,
                        "message": "action queued (sync task) — perceive to confirm"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = QueuedMcp()
            # 模型不停地调用"排队黑洞"工具
            answers = iter([
                '{"type":"tool","name":"scan_blocks","arguments":{"block_ids":["minecraft:oak_log"],"radius":64}}',
                '{"type":"tool","name":"scan_blocks","arguments":{"block_ids":["minecraft:oak_log"],"radius":64}}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "scan_blocks", "parameters": {}}],
                requires_control={"scan_blocks": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
                empty_result_cap=2,
            )
            result = runtime.run_turn("找棵树")

            # 关键断言：应通过 early_final 收束，而非耗到轮次超限
            self.assertIn("early_final", result)
            self.assertEqual("empty_result_cap", result["early_final"])
            self.assertIsInstance(result["final"], str)
            self.assertIn("工具反馈链路", result["final"])
            self.assertFalse(checkpoint.path.exists())
            # 至多 dispatch 了 2 次（达到阈值即停），没有无限重试
            scan_calls = [c for c in mcp.calls if c[0] == "scan_blocks"]
            self.assertLessEqual(len(scan_calls), 2)

    def test_unavailable_tool_gets_correction_feedback_not_crash(self):
        # 回归：模型从历史会话学到已停用工具名（scan_blocks 等）并继续调用，
        # 运行时不应崩溃（此前 raise ValueError 毁掉整个回合），而应给模型
        # 一句可读的纠正反馈并让它换工具；连续 3 次仍不可用则 early_final。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = NeverCalledMcp()
            # 模型第一轮调已停用工具 scan_blocks，第二轮改调可用工具 mine
            answers = iter([
                '{"type":"tool","name":"scan_blocks","arguments":{"block_ids":["minecraft:oak_log"],"radius":64}}',
                '{"type":"tool","name":"mine","arguments":{"block_ids":["minecraft:oak_log"],"count":5}}',
                '{"type":"final","content":"砍树完成"}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "mine", "parameters": {}}],
                requires_control={"mine": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
                empty_result_cap=2,
            )
            result = runtime.run_turn("帮我砍树")

            # 关键断言：回合没有崩溃，模型收到纠正后改用 mine 完成任务
            self.assertNotIn("early_final", result)
            self.assertEqual("砍树完成", result["final"])
            self.assertFalse(checkpoint.path.exists())
            mine_calls = [c for c in mcp.calls if c[0] == "mine"]
            self.assertEqual(len(mine_calls), 1)

    def test_unavailable_tool_cap_after_3_attempts(self):
        # 模型连续 3 次调用不可用工具 → 不应无限空转，直接 early_final 收束。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-2")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = NeverCalledMcp()
            answers = iter([
                '{"type":"tool","name":"scan_blocks","arguments":{}}',
                '{"type":"tool","name":"scan_blocks","arguments":{}}',
                '{"type":"tool","name":"scan_blocks","arguments":{}}',
            ])

            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "mine", "parameters": {}}],
                requires_control={"mine": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
                empty_result_cap=2,
            )
            result = runtime.run_turn("帮我砍树")

            self.assertEqual("unavailable_tool_cap", result.get("early_final"))
            self.assertFalse(checkpoint.path.exists())
            self.assertIn("不可用", result["final"])

    def test_sync_tool_with_data_is_not_counted_as_empty(self):
        # 回归：get_self_status / scan_nearby_entities 等同步工具虽然
        # task_id=None、terminal=None，但 dispatch 里有真实数据（hp/entities）。
        # 旧逻辑把它们计入"空结果"，模型连续调用两次就被 early_final 掐断，
        # 表现为"明明有结果却说感知不可用"。修复后只对排队占位包计数。
        class SyncDataMcp:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "acquire_control":
                    return {"lease_id": "lease-1"}
                if name == "release_control":
                    return "released"
                # 同步感知：dispatch 有真实数据，无 task_id
                if name == "scan_nearby_entities":
                    return {"dispatch": {"entities": [
                        {"id": 1, "type": "zombie", "distance": 5.0}]}}
                if name == "get_self_status":
                    return {"dispatch": {"hp": 20.0, "name": "ABBrain",
                                         "position": {"x": 0, "y": 64, "z": 0}}}
                return {"success": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = SyncDataMcp()
            # 模型连续调用两个同步感知工具（都有数据），然后 final
            answers = iter([
                '{"type":"tool","name":"scan_nearby_entities","arguments":{"radius":16}}',
                '{"type":"tool","name":"get_self_status","arguments":{}}',
                '{"type":"final","content":"状态正常"}',
            ])
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "scan_nearby_entities", "parameters": {}},
                       {"name": "get_self_status", "parameters": {}}],
                requires_control={"scan_nearby_entities": False,
                                  "get_self_status": False},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
                empty_result_cap=2,
            )
            result = runtime.run_turn("看看周围")

            # 关键断言：没有触发 early_final，模型正常完成 final
            self.assertNotIn("early_final", result)
            self.assertEqual("状态正常", result["final"])
            self.assertFalse(checkpoint.path.exists())

    def test_sync_tools_with_message_results_are_not_counted_as_empty(self):
        # lookup_recipe 的完整配方只放在 message 中，没有 data；连续两次
        # 成功查询不能被判成“已排队/无结果”。
        class RecipeMcp:
            def call(self, name, arguments):
                return {"success": True,
                        "message": f"recipe(s) for {arguments['item_id']}: planks -> item"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            answers = iter([
                '{"type":"tool","name":"lookup_recipe","arguments":{"item_id":"minecraft:stick"}}',
                '{"type":"tool","name":"lookup_recipe","arguments":{"item_id":"minecraft:iron_pickaxe"}}',
                '{"type":"final","content":"配方查询完成"}',
            ])
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-recipe"),
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=RecipeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "lookup_recipe", "parameters": {}}],
                requires_control={"lookup_recipe": False}, local_tools={},
                sleep=lambda _: None, max_rounds=6, empty_result_cap=2,
            )
            result = runtime.run_turn("查两次配方")
            self.assertEqual("配方查询完成", result["final"])
            self.assertNotIn("early_final", result)

    def test_live_business_failure_is_immediately_fed_back_and_releases_lease(self):
        # 正常执行（非 recover_turn）遇到 craft 缺材料，应在同一回合立即
        # 让模型改方案；即使 dispatch 抛错也必须释放控制租约。
        class BusinessFailMcp(FakeMcp):
            def call(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "acquire_control":
                    return {"lease_id": "lease-1"}
                if name == "release_control":
                    return "released"
                if name == "craft":
                    raise RuntimeError(
                        'MCP tool call failed: {"success":false,"message":"missing 2x stick"}')
                return {"success": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mcp = BusinessFailMcp()
            answers = iter([
                '{"type":"tool","name":"craft","arguments":{"item_id":"minecraft:iron_pickaxe"}}',
                '{"type":"final","content":"缺木棍，已调整计划"}',
            ])
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-fail"),
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "craft", "parameters": {}}],
                requires_control={"craft": True}, local_tools={},
                sleep=lambda _: None, max_rounds=4,
            )
            result = runtime.run_turn("做铁镐")
            self.assertEqual("缺木棍，已调整计划", result["final"])
            self.assertEqual(1, len([x for x in mcp.calls if x[0] == "release_control"]))
            self.assertFalse(runtime.checkpoint.path.exists())

    def test_write_action_injects_client_action_id(self):
        mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
        execute_tool_exact(
            mcp, companion="ABBrain", name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True, sleep=lambda _: None,
        )
        goto_call = [args for name, args in mcp.calls if name == "goto"][0]
        client_id = goto_call.get("client_action_id")
        self.assertTrue(isinstance(client_id, str) and len(client_id) > 8)

    def test_recover_planned_action_redispatch_same_client_action_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_planned",
                "turn_id": "turn-replay",
                "user": "向东走六格",
                "tool": "goto",
                "arguments": {"x": 6.5, "y": None, "z": 0.5},
                "client_action_id": "act-replay-1",
                "transcript": [
                    {"role": "user", "content": "向东走六格"},
                    {"role": "assistant", "content": '{"type":"tool","name":"goto","arguments":{}}'},
                ],
            })
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: '{"type":"final","content":"恢复重发完成。"}',
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                local_tools={},
                sleep=lambda _: None,
            )
            result = runtime.recover_turn()

            self.assertEqual("恢复重发完成。", result["final"])
            goto_call = [args for name, args in mcp.calls if name == "goto"]
            self.assertEqual(1, len(goto_call))
            self.assertEqual("act-replay-1", goto_call[0]["client_action_id"])
            self.assertFalse(checkpoint.path.exists())

    def test_recover_planned_action_business_failure_feeds_back_to_model(self):
        # 回归：恢复重发工具时遇到业务失败（如 craft 缺材料、mine 无目标），
        # 不应抛异常让恢复计数堆积（3 次后清 checkpoint 丢回合），而应把
        # 失败结果交给模型重新决策——模型看到"缺 2x stick"后转而合成木棍。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_planned",
                "turn_id": "turn-craft",
                "user": "合成铁镐",
                "tool": "craft",
                "arguments": {"item_id": "minecraft:iron_pickaxe"},
                "client_action_id": "act-craft-1",
                "transcript": [
                    {"role": "user", "content": "合成铁镐"},
                    {"role": "assistant", "content": '{"type":"tool","name":"craft","arguments":{}}'},
                ],
            })

            class CraftFailMcp(FakeMcp):
                def call(self, name, arguments):
                    self.calls.append((name, dict(arguments)))
                    if name == "acquire_control":
                        return {"lease_id": "lease-1"}
                    if name == "release_control":
                        return "released"
                    if name == "craft" and "iron_pickaxe" in str(arguments):
                        raise RuntimeError(
                            "MCP tool call failed: {\"success\":false,\"message\":"
                            "\"not enough materials for iron_pickaxe — missing: 2x stick\"}")
                    return {"success": True, "message": "done"}

            mcp = CraftFailMcp()
            # 模型先看到 craft 失败反馈，再决策合成木棍（这次成功），然后 final
            answers = iter([
                '{"type":"tool","name":"craft","arguments":{"item_id":"minecraft:stick"}}',
                '{"type":"final","content":"先做木棍再合成铁镐"}',
            ])
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
                model=lambda _messages, _tools: next(answers),
                tools=[{"name": "craft", "parameters": {}},
                       {"name": "goto", "parameters": {}}],
                requires_control={"craft": True, "goto": True},
                local_tools={},
                sleep=lambda _: None,
                max_rounds=8,
            )
            result = runtime.recover_turn()

            # 关键断言：没有因 craft 失败崩溃，模型收到失败结果后重新决策
            self.assertEqual("先做木棍再合成铁镐", result["final"])
            self.assertFalse(checkpoint.path.exists())
            # craft 被调用 2 次：重发的 iron_pickaxe（失败）+ 模型迭代的 stick
            craft_calls = [args for name, args in mcp.calls if name == "craft"]
            self.assertEqual(2, len(craft_calls))

    def test_recover_planned_action_without_client_id_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({"state": "tool_planned", "turn_id": "turn-x", "tool": "goto"})
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-1"),
                checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "", tools=[], requires_control={},
            )
            with self.assertRaisesRegex(RecoveryRequired, "ambiguous"):
                runtime.recover_turn()

    def test_checkpoint_write_fsyncs_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            import action_brain
            original = action_brain.fsync_dir
            synced = []
            action_brain.fsync_dir = lambda path: synced.append(path)
            try:
                checkpoint.write({"state": "tool_planned", "turn_id": "t"})
            finally:
                action_brain.fsync_dir = original
            self.assertEqual([root], synced)
            self.assertEqual({"state": "tool_planned", "turn_id": "t"}, checkpoint.read())

    def test_invalid_model_format_is_retried_without_persisting_correction_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            observed = []
            answers = iter([
                "我应该用普通文字回答",
                '{"type":"final","content":"已改为合法格式。"}',
            ])

            def model(messages, _tools):
                observed.append([dict(message) for message in messages])
                return next(answers)

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=FakeMcp(), companion="ABBrain", model=model,
                tools=[], requires_control={}, max_format_retries=2,
            )
            result = runtime.run_turn("请回答")

            self.assertEqual("已改为合法格式。", result["final"])
            self.assertEqual(2, len(observed))
            self.assertIn("格式错误", observed[1][-1]["content"])
            persisted_text = "\n".join(message["content"] for message in store.messages())
            self.assertNotIn("普通文字", persisted_text)
            self.assertNotIn("格式错误", persisted_text)

    def test_format_retry_exhaustion_before_tools_leaves_no_checkpoint_or_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "仍然不是 JSON",
                tools=[], requires_control={}, max_format_retries=1,
            )
            with self.assertRaisesRegex(ValueError, "valid JSON decision"):
                runtime.run_turn("请回答")
            self.assertFalse(checkpoint.path.exists())
            self.assertEqual([], store.messages())

    def test_complete_action_turn_commits_full_transcript_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                '{"type":"final","content":"已经走到目标附近。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=mcp,
                companion="ABBrain",
                model=lambda messages, tools: next(answers),
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                sleep=lambda _: None,
            )
            result = runtime.run_turn("向东走六格")

            self.assertEqual("已经走到目标附近。", result["final"])
            self.assertFalse(checkpoint.path.exists())
            persisted = store.messages()
            self.assertEqual("user", persisted[0]["role"])
            self.assertEqual("向东走六格", persisted[0]["content"])
            self.assertIn('"name": "goto"', persisted[1]["content"])
            self.assertEqual("user", persisted[2]["role"])
            self.assertTrue(persisted[2]["content"].startswith("<tool_result "))
            self.assertIn('"state": "done"', persisted[2]["content"])
            self.assertNotIn('"samples"', persisted[2]["content"])
            self.assertTrue(result["actions"][0]["samples"])
            self.assertEqual({"role": "assistant", "content": "已经走到目标附近。"}, persisted[3])

    def test_model_failure_before_any_action_leaves_no_history_or_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")

            def fail(_messages, _tools):
                raise RuntimeError("model unavailable")

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=FakeMcp(),
                companion="ABBrain",
                model=fail,
                tools=[],
                requires_control={},
            )
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                runtime.run_turn("不要留下悬空消息")
            self.assertEqual([], store.messages())
            self.assertFalse(checkpoint.path.exists())

    def test_existing_incomplete_side_effect_checkpoint_blocks_silent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_dispatched",
                "user": "向东走六格",
                "tool": "goto",
                "task_id": "t9",
            })
            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=FakeMcp(),
                companion="ABBrain",
                model=lambda _messages, _tools: '{"type":"final","content":"不该执行"}',
                tools=[],
                requires_control={},
            )
            with self.assertRaises(RecoveryRequired):
                runtime.run_turn("向东走六格")
            self.assertEqual([], store.messages())

    def test_recover_dispatched_task_queries_same_id_without_replaying_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_dispatched",
                "turn_id": "turn-recover",
                "user": "向东走六格",
                "tool": "goto",
                "task_id": "t9",
                "dispatch": {"data": {"task_id": "t9"}},
                "transcript": [
                    {"role": "user", "content": "向东走六格"},
                    {"role": "assistant", "content": '{"type":"tool","name":"goto","arguments":{}}'},
                ],
            })
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=mcp,
                companion="ABBrain",
                model=lambda _messages, _tools: '{"type":"final","content":"恢复后确认完成。"}',
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                sleep=lambda _: None,
            )
            result = runtime.recover_turn()

            self.assertEqual("恢复后确认完成。", result["final"])
            self.assertNotIn("goto", [name for name, _ in mcp.calls])
            self.assertEqual("t9", [args for name, args in mcp.calls if name == "task_status"][0]["task_id"])
            self.assertFalse(checkpoint.path.exists())

    def test_recover_clears_checkpoint_when_turn_was_already_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            transcript = [
                {"role": "user", "content": "请求"},
                {"role": "assistant", "content": "完成"},
            ]
            store.append_batch(transcript, transaction_id="turn-committed")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "committing",
                "turn_id": "turn-committed",
                "transcript": transcript,
            })
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "", tools=[], requires_control={},
            )
            result = runtime.recover_turn()
            self.assertEqual("already_committed", result["state"])
            self.assertFalse(checkpoint.path.exists())
            self.assertEqual(transcript, store.messages())

    def test_recover_refuses_ambiguous_planned_action_without_dispatch_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({"state": "tool_planned", "turn_id": "turn-x", "tool": "goto"})
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-1"),
                checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "", tools=[], requires_control={},
            )
            with self.assertRaisesRegex(RecoveryRequired, "ambiguous"):
                runtime.recover_turn()


if __name__ == "__main__":
    unittest.main()
