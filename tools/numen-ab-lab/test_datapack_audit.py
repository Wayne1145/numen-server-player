"""第三方覆盖包回滚门禁：恢复原选择器语义，不声称未经证实的性能优化。"""
import unittest
from pathlib import Path
class DatapackAuditTest(unittest.TestCase):
    def test_ground_filter_precedes_limit(self):
        p=Path(__file__).parent/'datapacks/commandsceptre-equivalence/data/commandsceptre/functions/explosion_on_ground.mcfunction'
        self.assertTrue(p.exists())
        self.assertEqual('data modify entity @e[tag=ignite_on_ground,nbt={OnGround:1b},limit=1] Fuse set value 0s',p.read_text().strip())
if __name__=='__main__':unittest.main()
