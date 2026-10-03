import ctypes
import logging
import os
from pathlib import Path
import sys


def run():
    if '--apply-update' in sys.argv:
        import updater
        pos = sys.argv.index('--apply-update')
        updater.apply_update(sys.executable,sys.argv[pos+1],int(sys.argv[pos+2]))
        return
    if '--self-test' in sys.argv:
        from self_test import run_tests
        run_tests(Path(sys.argv[sys.argv.index('--self-test')+1]))
        return
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    from ui import Window, STYLE
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setApplicationName('Macros')
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None,False,'Local\\Macros-'+os.environ.get('USERNAME','user'))
    if ctypes.get_last_error()==183:
        QMessageBox.information(None,'Macros','Программа уже запущена. Используйте её окно или горячие клавиши.')
        return
    try:
        window = Window(no_update='--no-update' in sys.argv)
        window.show()
        if '--updated' in sys.argv:
            marker = Path(sys.argv[sys.argv.index('--updated')+1])
            QTimer.singleShot(700,lambda:marker.write_text('ready',encoding='utf-8'))
        app.exec()
    finally:
        if mutex:
            kernel.CloseHandle(mutex)


if __name__=='__main__':
    folder = Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'Macros'
    folder.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=str(folder/'app.log'),level=logging.ERROR,encoding='utf-8')
    try:
        run()
    except Exception:
        logging.exception('Application failed')
        if '--self-test' not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None,'Не удалось запустить Macros. Подробности: '+str(folder/'app.log'),'Macros',0x10)
        sys.exit(1)
