#!/usr/bin/env python3
"""增量同步真源到 Serena 审计镜像并核验 SHA；不修改业务代码。"""
from pathlib import Path
import hashlib
import json
import subprocess
import shutil

SOURCE = Path('/mnt/d/numen-server-player')
MIRROR = Path('/home/wayne/.hermes/workspace/numen-serena-source')
paths = subprocess.check_output(['/usr/bin/git', '-C', str(SOURCE), 'ls-files', '-z']).decode().split('\0')
files = {}
for rel in paths:
    if not rel.endswith(('.java', '.py', '.html', '.gradle', '.json', '.md', '.service', '.sh')):
        continue
    source = SOURCE / rel
    target = MIRROR / rel
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != data:
        shutil.copy2(source, target)
    if rel.endswith(('.java', '.py')):
        files[rel] = hashlib.sha256(data).hexdigest()
# 移除不再跟踪的旧源码，防止镜像残留造成错误导航。
for target in MIRROR.rglob('*'):
    if target.is_file() and target.suffix in ('.java', '.py') and '.serena' not in target.parts:
        rel = str(target.relative_to(MIRROR))
        if rel not in files:
            target.unlink()
head = subprocess.check_output(['/usr/bin/git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).decode().strip()
(MIRROR / 'source-manifest.json').write_text(json.dumps({'source':str(SOURCE),'head':head,'files':files}, indent=2))
print(f'HEAD={head} source_files={len(files)} mirror={MIRROR}')
