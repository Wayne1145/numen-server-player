"""文件工具不得越出技能/角色目录；取消后新聊天保留来源。"""
import tempfile
import unittest
from pathlib import Path
from persistent_action_cli import make_load_skill, load_persona
class LocalPathAuditTest(unittest.TestCase):
    def test_skill_and_persona_cannot_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'skills').mkdir();(root/'personas').mkdir()
            (root/'private.md').write_text('private')
            self.assertFalse(make_load_skill(root/'skills')({'name':'../private'})['success'])
            with self.assertRaises(ValueError):load_persona(root,'../private')
if __name__=='__main__':unittest.main()
