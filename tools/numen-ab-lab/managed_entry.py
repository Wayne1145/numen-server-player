"""托管模式入口：父 JVM 的本地连接消失后退出，保留磁盘恢复快照。"""
import argparse
import os
import runpy
import socket
import sys
import threading


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--parent-port', type=int, required=True)
    p.add_argument('--entry', default='active_agent_cli.py')
    args, rest = p.parse_known_args()
    conn = socket.create_connection(('127.0.0.1', args.parent_port), timeout=10)
    conn.sendall((os.environ.pop('NUMEN_PARENT_NONCE') + '\n').encode())
    response = b''
    while len(response) < 3:
        part = conn.recv(3 - len(response))
        if not part:
            break
        response += part
    if response != b'ok\n':
        raise RuntimeError('parent authentication failed')
    conn.settimeout(None)

    def watch_parent():
        try:
            while conn.recv(1):
                pass
        except OSError:
            pass
        # 回合动作先写 checkpoint；强制结束不能删除快照或冒充取消。
        os._exit(0)

    os.environ['NUMEN_EVENT_START_MS'] = str(__import__('time').time_ns() // 1_000_000)
    threading.Thread(target=watch_parent, daemon=True).start()
    sys.argv = [args.entry, *rest]
    runpy.run_path(args.entry, run_name='__main__')

if __name__ == '__main__': main()
