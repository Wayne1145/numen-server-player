"""托管入口必须检测父进程退出，且不能从旧进程环境继承凭据。"""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

class ManagedEntryTest(unittest.TestCase):
    def test_parent_connection_loss_exits_even_while_worker_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / 'worker.py'
            worker.write_text('import time; time.sleep(60)', encoding='utf-8')
            with socket.socket() as parent:
                parent.bind(('127.0.0.1', 0)); parent.listen(); parent.settimeout(5)
                p = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'managed_entry.py'),
                                      '--parent-port', str(parent.getsockname()[1]), '--entry', str(worker)],
                                     env={**__import__('os').environ, 'NUMEN_PARENT_NONCE': 'test-nonce'})
                try:
                    conn, _ = parent.accept()
                    with conn:
                        self.assertEqual(conn.recv(128), b'test-nonce\n')
                        conn.sendall(b'o')
                        __import__('time').sleep(0.05)
                        conn.sendall(b'k\n')
                    self.assertEqual(p.wait(timeout=5), 0)
                finally:
                    if p.poll() is None:
                        p.kill(); p.wait()

if __name__ == '__main__': unittest.main()
