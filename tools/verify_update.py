"""Verify the real packaged replacement/restart path in an isolated profile."""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from storage import Store


def close_app(target):
    user = c.WinDLL('user32',use_last_error=True)
    kernel = c.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype = c.c_void_p
    kernel.OpenProcess.argtypes = [w.DWORD,w.BOOL,w.DWORD]
    kernel.QueryFullProcessImageNameW.argtypes = [c.c_void_p,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [c.c_void_p]
    user.GetWindowThreadProcessId.argtypes = [c.c_void_p,c.POINTER(w.DWORD)]
    user.PostMessageW.argtypes = [c.c_void_p,w.UINT,w.WPARAM,w.LPARAM]
    callback_type = c.WINFUNCTYPE(w.BOOL,c.c_void_p,w.LPARAM)
    closed = []
    @callback_type
    def callback(hwnd,lparam):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd,c.byref(pid))
        handle = kernel.OpenProcess(0x1000,False,pid.value)
        if handle:
            try:
                size = w.DWORD(2048)
                name = c.create_unicode_buffer(2048)
                if kernel.QueryFullProcessImageNameW(handle,0,name,c.byref(size)) and Path(name.value).resolve()==target.resolve():
                    user.PostMessageW(hwnd,0x10,0,0)
                    closed.append(pid.value)
            finally:
                kernel.CloseHandle(handle)
        return True
    user.EnumWindows(callback,0)
    return closed


def verify(binary):
    folder = Path(tempfile.mkdtemp(prefix='Macros-upgrade-verification-'))
    stage = folder/'download'
    stage.mkdir()
    source = stage/'Macros.exe'
    shutil.copy2(binary,source)
    target_folder = folder/'installed'
    target_folder.mkdir()
    target = target_folder/'Macros.exe'
    target.write_bytes(b'previous-version-fixture')
    profile = folder/'profile'
    store = Store(profile/'Macros')
    store.data['settings']['auto_update'] = False
    store.add('Сохранённый макрос',[{'kind':'key','vk':65,'down':True,'t':0},{'kind':'key','vk':65,'down':False,'t':.05}],repeats=7)
    before = store.path.read_bytes()
    parent_script = folder/'parent.py'
    parent_script.write_text('import time\ntime.sleep(3)\n',encoding='utf-8')
    env = {**os.environ,'LOCALAPPDATA':str(profile)}
    parent = subprocess.Popen([sys.executable,str(parent_script)],env=env)
    worker = subprocess.Popen([str(source),'--apply-update',str(target),str(parent.pid)],env=env,cwd=stage)
    try:
        worker.wait(timeout=75)
        marker = stage/'healthy'
        assert worker.returncode==0,worker.returncode
        assert marker.exists(), (stage/'update-error.txt').read_text('utf-8') if (stage/'update-error.txt').exists() else 'Missing health marker'
        assert target.read_bytes()==binary.read_bytes()
        assert not target.with_suffix('.previous.exe').exists()
        assert store.path.read_bytes()==before
        assert Store(profile/'Macros').data['macros'][0]['repeats']==7
        closed = close_app(target)
        assert closed,'Updated app window not found'
        report = {'success':True,'replaced_and_restarted':True,'saved_macros_preserved':True,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'closed_processes':sorted(set(closed)),'isolated_profile':str(profile)}
        output = root/'artifacts'/'update-verification.json'
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        close_app(target)
        if worker.poll() is None:
            worker.terminate()
            worker.wait(10)
        if parent.poll() is None:
            parent.terminate()
            parent.wait(10)


if __name__=='__main__':
    verify(Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist'/'Macros.exe')
