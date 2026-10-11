"""跨进程文件锁回归：Windows 与 POSIX 使用同一调用契约。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class FileLockTest(unittest.TestCase):
    def test_second_process_cannot_take_held_lock(self):
        import file_lock as lock
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'brain.lock'
            with p.open('a+b') as f:
                lock.flock(f.fileno(), lock.LOCK_EX | lock.LOCK_NB)
                script = ("import sys; sys.path.insert(0,sys.argv[2]); import file_lock as l; "
                          "f=open(sys.argv[1],'a+b'); "
                          "l.flock(f.fileno(),l.LOCK_EX|l.LOCK_NB)")
                r = subprocess.run([sys.executable, '-c', script, str(p), str(Path(__file__).parent)], capture_output=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(b'BlockingIOError', r.stderr)
                lock.flock(f.fileno(), lock.LOCK_UN)
                r = subprocess.run([sys.executable, '-c', script, str(p), str(Path(__file__).parent)], capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr.decode())

if __name__ == '__main__':
    unittest.main()
