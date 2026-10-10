"""距离任务围栏及维度工具接线的回归。"""
import unittest
from pathlib import Path
from world_memory import WorldMemoryStore
from persistent_action_cli import make_world_memory_tools
class MemoryToolAuditTest(unittest.TestCase):
    def test_tools_pass_dimension(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store=WorldMemoryStore(Path(tmp)/'m')
            tools=make_world_memory_tools(store)
            tools['remember_block']({'type':'chest','x':1,'y':2,'z':3,'dimension':'minecraft:the_nether'})
            result=tools['recall_blocks']({'dimension':'minecraft:the_nether'})
            self.assertEqual(1,len(result['blocks']))
            self.assertEqual('minecraft:the_nether',result['blocks'][0]['dimension'])
if __name__=='__main__':unittest.main()
