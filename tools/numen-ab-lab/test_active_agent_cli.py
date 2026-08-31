"""ActiveAgent CLI 常驻轮询的连接复用回归测试。"""
import unittest
from unittest.mock import patch

import active_agent_cli


class FakeMcp:
    instances = 0
    initializes = 0

    def __init__(self):
        type(self).instances += 1

    def initialize(self):
        type(self).initializes += 1


class FakeContext:
    instances = 0

    def __init__(self, **kwargs):
        type(self).instances += 1
        self.companion = kwargs["companion"]


class ContextFactoryCacheTest(unittest.TestCase):
    def setUp(self):
        FakeMcp.instances = 0
        FakeMcp.initializes = 0
        FakeContext.instances = 0

    def test_same_companion_reuses_context_and_mcp(self):
        """每轮读取同一同伴时不得重新初始化 MCP 或枚举身份。"""
        with patch.object(active_agent_cli, "McpClient", FakeMcp), \
             patch.object(active_agent_cli, "CompanionContext", FakeContext):
            factory = active_agent_cli.build_context_factory("numen-ab")
            first = factory("yachiyo")
            second = factory("yachiyo")

        self.assertIs(first, second)
        self.assertEqual(1, FakeMcp.instances)
        self.assertEqual(1, FakeMcp.initializes)
        self.assertEqual(1, FakeContext.instances)

    def test_different_companions_get_isolated_contexts(self):
        with patch.object(active_agent_cli, "McpClient", FakeMcp), \
             patch.object(active_agent_cli, "CompanionContext", FakeContext):
            factory = active_agent_cli.build_context_factory("numen-ab")
            first = factory("yachiyo")
            second = factory("Bob")

        self.assertIsNot(first, second)
        self.assertEqual(2, FakeMcp.instances)
        self.assertEqual(2, FakeContext.instances)


if __name__ == "__main__":
    unittest.main()
