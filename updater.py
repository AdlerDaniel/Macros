import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from app_config import REPOSITORY, VERSION


def version_tuple(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Неверная версия обновления')
    return tuple(map(int, value.lstrip('v').split('.')))


def read_url(url, limit=2_000_000):
    request = urllib.request.Request(url, headers={'User-Agent': 'Macros/'+VERSION})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(limit+1)
    if len(data) > limit:
        raise ValueError('Слишком большой ответ сервера')
    return data


def check_and_download(messages, target=None):
    if not getattr(sys, 'frozen', False) and target is None:
        return
    try:
        messages.put(('update', 'Проверка обновлений…'))
        release = json.loads(read_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest'))
        tag = release['tag_name']
        if version_tuple(tag) <= version_tuple(VERSION):
            messages.put(('update', 'Последняя версия · '+VERSION))
            return
        assets = {a['name']: a['browser_download_url'] for a in release['assets']}
        prefix = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
        for name in ('update.json', 'Macros.exe'):
            if not assets.get(name, '').startswith(prefix):
                raise ValueError('В релизе отсутствует файл обновления')
        manifest = json.loads(read_url(assets['update.json']))
        if manifest['version'] != tag.lstrip('v') or manifest['file'] != 'Macros.exe' or not re.fullmatch('[a-f0-9]{64}', manifest['sha256']):
            raise ValueError('Неверный манифест обновления')
        expected = manifest['size']
        if type(expected) is not int or not 1000 < expected <= 300_000_000:
            raise ValueError('Неверный размер обновления')
        messages.put(('update', 'Загрузка '+manifest['version']+'…'))
        folder = Path(tempfile.mkdtemp(prefix='Macros-update-'))
        path = folder / 'Macros.exe'
        digest = hashlib.sha256()
        count = 0
        request = urllib.request.Request(assets['Macros.exe'], headers={'User-Agent':'Macros/'+VERSION})
        with urllib.request.urlopen(request, timeout=30) as response, path.open('wb') as out:
            while chunk := response.read(1024*1024):
                count += len(chunk)
                if count > expected:
                    raise ValueError('Размер обновления не совпадает')
                digest.update(chunk)
                out.write(chunk)
        if count != expected or digest.hexdigest() != manifest['sha256']:
            path.unlink(missing_ok=True)
            raise ValueError('Проверка целостности обновления не пройдена')
        messages.put(('update_ready', str(path)))
    except Exception as exc:
        messages.put(('update', 'Обновление недоступно: '+str(exc)))


def wait_parent(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 60000) != 0:
                raise RuntimeError('Программа не завершилась для установки обновления')
        finally:
            kernel.CloseHandle(handle)


def replace_and_launch(source, target, launch=None, timeout=40):
    source, target = Path(source), Path(target)
    backup = target.with_suffix('.previous.exe')
    staged = target.with_suffix('.new.exe')
    marker = source.parent / 'healthy'
    marker.unlink(missing_ok=True)
    shutil.copy2(source, staged)
    # A one-file bootloader may still hold the executable briefly after the GUI exits.
    deadline = time.monotonic()+12
    while True:
        try:
            os.replace(target, backup)
            break
        except PermissionError:
            if time.monotonic()>=deadline:
                raise
            time.sleep(.15)
    process = None
    try:
        os.replace(staged, target)
        process = (launch or subprocess.Popen)([str(target), '--updated', str(marker)], cwd=str(target.parent))
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if marker.exists():
                backup.unlink(missing_ok=True)
                return True
            if process.poll() is not None:
                break
            time.sleep(.1)
        raise RuntimeError('Новая версия не запустилась')
    except Exception:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.replace(backup, target)
        (launch or subprocess.Popen)([str(target), '--no-update'], cwd=str(target.parent))
        return False
    finally:
        staged.unlink(missing_ok=True)


def apply_update(source, target, pid):
    try:
        wait_parent(pid)
        replace_and_launch(source, target)
    except Exception as exc:
        (Path(source).parent/'update-error.txt').write_text(str(exc), encoding='utf-8')
        if Path(target).exists():
            subprocess.Popen([target,'--no-update'],cwd=str(Path(target).parent))


def start_install(path):
    # The downloaded standalone executable acts as its own updater after our process exits.
    with tempfile.NamedTemporaryFile(prefix='.macros-write-',dir=str(Path(sys.executable).parent)):
        pass
    subprocess.Popen([path, '--apply-update', sys.executable, str(os.getpid())], cwd=str(Path(path).parent))
