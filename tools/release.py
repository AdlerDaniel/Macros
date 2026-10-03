"""Publish a validated application update: python tools/release.py 1.0.1."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from app_config import VERSION, REPOSITORY
from updater import version_tuple


def command(*args, capture=False):
    return subprocess.run(list(args),cwd=root,check=True,text=True,encoding='utf-8',capture_output=capture)


def release(version):
    if version_tuple(version)<=version_tuple(VERSION):
        raise ValueError('Новая версия должна быть выше текущей')
    origin = command('git','remote','get-url','origin',capture=True).stdout.strip()
    if origin.removesuffix('.git') not in (f'https://github.com/{REPOSITORY}',f'git@github.com:{REPOSITORY}'):
        raise ValueError('Неожиданный репозиторий origin')
    branch = command('git','branch','--show-current',capture=True).stdout.strip()
    if branch!='main':
        raise ValueError('Публикация разрешена только из main')
    config = root/'app_config.py'
    original = config.read_text('utf-8')
    config.write_text(re.sub(r"VERSION = '[^']+'",f"VERSION = '{version}'",original),encoding='utf-8')
    try:
        command(sys.executable,'-m','pytest','-q')
        command(sys.executable,'tools/build.py')
        command(str(root/'dist'/'Macros.exe'),'--self-test',str(root/'artifacts'/'release-test'))
        result = json.loads((root/'artifacts'/'release-test'/'self-test.json').read_text('utf-8'))
        if result.get('success') is not True:
            raise RuntimeError('EXE verification failed')
    except Exception:
        config.write_text(original,encoding='utf-8')
        raise
    command('git','add','.')
    command('git','commit','-m',f'Release Macros {version}')
    command('git','push','origin','main')
    tag = 'v'+version
    deadline = time.monotonic()+900
    while time.monotonic()<deadline:
        result = subprocess.run(['gh','release','view',tag,'--json','url,tagName,assets'],cwd=root,text=True,encoding='utf-8',capture_output=True)
        if result.returncode==0:
            remote = json.loads(result.stdout)
            names = {a['name'] for a in remote['assets']}
            if {'Macros.exe','update.json','SHA256SUMS.txt'}<=names:
                folder = root/'artifacts'/'published'/version
                folder.mkdir(parents=True,exist_ok=True)
                command('gh','release','download',tag,'--dir',str(folder),'--clobber')
                manifest = json.loads((folder/'update.json').read_text('utf-8'))
                binary = (folder/'Macros.exe').read_bytes()
                if manifest['version']!=version or len(binary)!=manifest['size'] or hashlib.sha256(binary).hexdigest()!=manifest['sha256']:
                    raise RuntimeError('Published release checksum mismatch')
                print(remote['url'])
                return
        time.sleep(10)
    raise RuntimeError('GitHub release not completed; inspect GitHub Actions. Source has been pushed.')


if __name__=='__main__':
    if len(sys.argv)!=2 or not re.fullmatch(r'\d+\.\d+\.\d+',sys.argv[1]):
        raise SystemExit('Usage: python tools/release.py 1.0.1')
    release(sys.argv[1])
