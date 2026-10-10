"""服务端安全顺序的补充结构门禁；Java 行为测试另跑。"""
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]/'components'
class SecurityWiringTest(unittest.TestCase):
    def test_no_protocol_cache_bypass(self):
        text=(ROOT/'numen-api/common/src/main/java/com/dwinovo/numen/server/mcp/ServerMcpServer.java').read_text()
        self.assertNotIn('IdempotentDispatchRegistry.lookup',text)
    def test_authorization_before_respawn_and_exact_stop(self):
        text=(ROOT/'numen-api/common/src/main/java/com/dwinovo/numen/server/ServerNumenActuatorImpl.java').read_text()
        body=text[text.index('public CompletableFuture<ToolResult> invoke'):text.index('private ToolResult fail')]
        self.assertLess(body.index('principal.canControl(owner)'),body.index('resolve(s, companionId)'))
        self.assertIn('TaskStopGuard.matches(taskId, current == null ? null : current.publicId())',text)
        self.assertIn('distanceViolation',body)
        self.assertIn('journal.execute',body)
if __name__=='__main__': unittest.main()
