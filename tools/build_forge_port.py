"""从固定Git源码导出Forge 1.20实验构建；不改主树，不扩大兼容声明。"""
from __future__ import annotations
import argparse,io,json,os,subprocess,zipfile
from pathlib import Path

TARGETS={'1.20':{'forge':'46.0.14','core':'0.0.11-mc1.20-exp.1','api':'0.0.7-fix4-mc1.20-exp.1'}}

def replace_once(path,old,new):
    text=path.read_text(encoding='utf-8')
    if text.count(old)!=1:raise ValueError(f'补丁锚点不唯一：{path}: {old}')
    path.write_text(text.replace(old,new),encoding='utf-8')

def prepare_port(repo: Path, output: Path, target: str, ref: str, *, use_index=False):
    if target not in TARGETS:raise ValueError('目标未通过小改适配门禁，不允许仅改版本发布')
    if output.exists():raise FileExistsError(output)
    cfg=TARGETS[target]
    tree=subprocess.check_output(['git','write-tree'],cwd=repo).decode().strip() if use_index else ref
    data=subprocess.check_output(['git','archive','--format=zip',tree],cwd=repo)
    output.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:archive.extractall(output)
    for module,version in [('numen-api',cfg['api']),('minecraft-numen',cfg['core'])]:
        base=output/'components'/module;p=base/'gradle.properties'
        original='0.0.7-fix4' if module=='numen-api' else '0.0.11'
        for old,new in [(f'version={original}\n',f'version={version}\n'),('minecraft_version=1.20.1\n',f'minecraft_version={target}\n'),('minecraft_version_range=[1.20.1, 1.21)',f'minecraft_version_range=[{target}]'),('forge_version=47.2.30',f'forge_version={cfg["forge"]}'),('forge_version_range=[47,)','forge_version_range=[46,47)'),('forge_loader_version_range=[47,)','forge_loader_version_range=[46,47)')]:replace_once(p,old,new)
        replace_once(base/'settings.gradle',"include('fabric')","// 实验移植只配置Forge，避免解析未适配Fabric接口。")
    for relative in ['common/build.gradle','forge/build.gradle']:
        p=output/'components/minecraft-numen'/relative
        text=p.read_text(encoding='utf-8').replace('numen-api-common-1.20.1:0.0.7-fix4',f'numen-api-common-{target}:{cfg["api"]}').replace('numen-api-forge-1.20.1:0.0.7-fix4',f'numen-api-forge-{target}:{cfg["api"]}').replace("name: 'numen-api-forge-1.20.1', version: '0.0.7-fix4'",f"name: 'numen-api-forge-{target}', version: '{cfg['api']}'")
        p.write_text(text,encoding='utf-8')
    replace_once(output/'components/numen-api/common/src/main/java/com/dwinovo/numen/client/screen/SimpleButton.java','protected void renderWidget(','public void renderWidget(')
    required=output/'components/numen-api/common/src/main/java/com/dwinovo/numen/server/AuthTokens.java'
    if not required.exists():raise RuntimeError('导出的源码缺鉴权实现，拒绝构建')
    return cfg

def build(output: Path):
    for component,tasks in [('numen-api',[':common:test',':common:publish',':forge:publish']),('minecraft-numen',[':common:test',':forge:build'])]:
        cwd=output/'components'/component
        if os.name=='nt':command=['cmd','/c','gradlew.bat',*tasks,'--no-daemon','--console=plain']
        elif Path('/mnt/c/Windows/System32/cmd.exe').exists():command=['/mnt/c/Windows/System32/cmd.exe','/c','gradlew.bat',*tasks,'--no-daemon','--console=plain']
        else:
            (cwd/'gradlew').chmod(0o755);command=['./gradlew',*tasks,'--no-daemon','--console=plain']
        subprocess.run(command,cwd=cwd,check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',required=True,choices=TARGETS);p.add_argument('--output',type=Path,required=True);p.add_argument('--ref',default='HEAD');p.add_argument('--build',action='store_true');p.add_argument('--index',action='store_true',help='仅开发测试：导出当前暂存树')
    a=p.parse_args();repo=Path(__file__).resolve().parents[1]
    cfg=prepare_port(repo,a.output.resolve(),a.target,a.ref,use_index=a.index)
    print(json.dumps(cfg,ensure_ascii=False),flush=True)
    if a.build:build(a.output.resolve())
