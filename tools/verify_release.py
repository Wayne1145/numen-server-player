"""候选发布门禁：校验嵌入 API、记录构件和依赖 SHA，不接受旧 jarJar。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

def verify(core: Path, api: Path):
    with zipfile.ZipFile(core) as jar:
        names=[n for n in jar.namelist() if n.startswith('META-INF/jarjar/') and n.endswith('.jar')]
        assert len(names)==1, names
        embedded=jar.read(names[0])
        assert embedded==api.read_bytes(), 'jarJar API 与本次发布源不一致'
        with zipfile.ZipFile(io.BytesIO(embedded)) as nested:
            for name in ['ActionGeneration','ActionBounds','TaskStopGuard','IdempotentDispatchRegistry']:
                assert f'com/dwinovo/numen/server/{name}.class' in nested.namelist(), name
    return {'core_sha256':hashlib.sha256(core.read_bytes()).hexdigest(),
            'api_sha256':hashlib.sha256(api.read_bytes()).hexdigest(), 'embedded':names[0]}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('core',type=Path);p.add_argument('api',type=Path)
    args=p.parse_args();print(json.dumps(verify(args.core,args.api),indent=2))
