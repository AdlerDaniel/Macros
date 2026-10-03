import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from app_config import VERSION

if not (root/'assets'/'app.ico').exists():
    subprocess.run([sys.executable,str(root/'tools'/'icon.py')],cwd=root,check=True)
version_file = root/'build'/'version.txt'
version_file.parent.mkdir(exist_ok=True)
parts = tuple(map(int,VERSION.split('.'))) + (0,)
version_file.write_text(f'''VSVersionInfo(
ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName','AdlerDaniel'),
StringStruct('FileDescription','Macros keyboard and mouse automation'), StringStruct('FileVersion','{VERSION}'),
StringStruct('ProductName','Macros'), StringStruct('ProductVersion','{VERSION}'), StringStruct('OriginalFilename','Macros.exe')])]),
VarFileInfo([VarStruct('Translation',[1033,1200])])])''',encoding='utf-8')
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
    '--name','Macros','--icon',str(root/'assets'/'app.ico'),'--version-file',str(version_file),'--add-data',str(root/'assets')+';assets',
    '--hidden-import','self_test','--exclude-module','PySide6.QtWebEngineCore',
    '--exclude-module','PySide6.QtWebEngineWidgets',str(root/'main.py')],cwd=root,check=True)
binary = root/'dist'/'Macros.exe'
digest = hashlib.sha256(binary.read_bytes()).hexdigest()
manifest = {'version':VERSION,'file':'Macros.exe','sha256':digest,'size':binary.stat().st_size}
(root/'dist'/'update.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(root/'dist'/'SHA256SUMS.txt').write_text(digest+'  Macros.exe\n',encoding='utf-8')
print(json.dumps(manifest))
