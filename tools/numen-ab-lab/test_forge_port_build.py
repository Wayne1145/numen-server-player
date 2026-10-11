"""跨版本构建必须严格限版本、使用匹配引擎，且干净导出包含鉴权源码。"""
import unittest,tempfile,subprocess
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_forge_port import prepare_port
ROOT=Path(__file__).resolve().parents[2]
class ForgePortBuildTest(unittest.TestCase):
    def test_unsupported_version_refused_before_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):prepare_port(ROOT,Path(tmp)/'export','1.20.2','HEAD')
            self.assertFalse((Path(tmp)/'export').exists())
    def test_export_has_exact_range_and_matching_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'export'
            prepare_port(ROOT,out,'1.20','7a908a4f2a6adb7a9512517ad0ec4f7b7b39efa9')
            self.assertTrue((out/'components/numen-api/common/src/main/java/com/dwinovo/numen/server/AuthTokens.java').exists())
            for mod in ['numen-api','minecraft-numen']:
                text=(out/'components'/mod/'gradle.properties').read_text()
                self.assertIn('minecraft_version_range=[1.20]\n',text)
                self.assertIn('forge_version_range=[46,47)\n',text)
                self.assertNotIn("include('fabric')",(out/'components'/mod/'settings.gradle').read_text())
            text=(out/'components/minecraft-numen/forge/build.gradle').read_text()
            self.assertNotIn('numen-api-forge-1.20.1',text)
            self.assertIn('0.0.7-fix4-mc1.20-exp.1',text)
            self.assertIn('numen-api-forge-1.20',text)
            self.assertIn('public void renderWidget', (out/'components/numen-api/common/src/main/java/com/dwinovo/numen/client/screen/SimpleButton.java').read_text())
if __name__=='__main__':unittest.main()
