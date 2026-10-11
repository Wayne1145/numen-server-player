"""标准库跨平台锁：Windows 锁固定字节，POSIX 保留 flock 语义。"""
import os
import time

LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

if os.name != 'nt':
    from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
else:
    import msvcrt

    def flock(fd, operation):
        # 锁定固定首字节；即使文件长度为零也可以锁定 EOF 之外的区域。
        position = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK if operation & LOCK_UN else msvcrt.LK_NBLCK, 1)
                    return
                except OSError as exc:
                    if operation & LOCK_UN:
                        raise
                    if operation & LOCK_NB:
                        raise BlockingIOError('file lock already held') from exc
                    time.sleep(0.05)
        finally:
            os.lseek(fd, position, os.SEEK_SET)
