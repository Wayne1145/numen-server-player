"""HTTP 防护回归：真实回环请求，不连接 Minecraft。"""
import http.client
import tempfile
import unittest
from pathlib import Path
from brain_server import BrainServer
class HttpAuditTest(unittest.TestCase):
    def test_nonloopback_and_empty_token_refused(self):
        with self.assertRaises(ValueError):
            BrainServer(host='0.0.0.0',port=0,token='',factory=None,static_dir=Path('.'))
    def test_query_token_rejected_and_body_bounded(self):
        server=BrainServer(host='127.0.0.1',port=0,token='test',factory=lambda *_:None,static_dir=Path('.'))
        server.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.bound_port(),timeout=5)
            conn.request('GET','/health?token=test')
            self.assertEqual(401,conn.getresponse().status)
            conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',server.bound_port(),timeout=5)
            conn.request('POST','/v1/turns',body=b'{}',headers={'Authorization':'Bearer test','Content-Length':'1000000'})
            self.assertEqual(413,conn.getresponse().status)
            conn.close()
        finally: server.stop()
if __name__=='__main__':unittest.main()
