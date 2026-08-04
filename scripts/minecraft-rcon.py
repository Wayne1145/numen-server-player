#!/usr/bin/env python3
# 最小 Minecraft RCON 客户端：用于向隔离测试服发送 save-all/stop，不接触正式服。
import argparse, socket, struct

def packet(req_id, kind, text):
    body = struct.pack('<ii', req_id, kind) + text.encode('utf-8') + b'\0\0'
    return struct.pack('<i', len(body)) + body

def recv_packet(sock):
    raw = sock.recv(4)
    if len(raw) != 4:
        raise RuntimeError('RCON 响应长度头不完整')
    size = struct.unpack('<i', raw)[0]
    data = b''
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError('RCON 响应提前结束')
        data += chunk
    req_id, kind = struct.unpack('<ii', data[:8])
    return req_id, kind, data[8:-2].decode('utf-8', 'replace')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--password', required=True)
    p.add_argument('command')
    a = p.parse_args()
    with socket.create_connection(('127.0.0.1', a.port), timeout=10) as s:
        s.sendall(packet(1, 3, a.password))
        rid, _, text = recv_packet(s)
        if rid == -1:
            raise SystemExit('RCON 认证失败')
        s.sendall(packet(2, 2, a.command))
        rid, _, text = recv_packet(s)
        print(text)

if __name__ == '__main__':
    main()
