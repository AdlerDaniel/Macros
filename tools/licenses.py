from pathlib import Path
import urllib.request
root = Path(__file__).resolve().parents[1]
for name,url in [('LGPL-3.0.txt','https://www.gnu.org/licenses/lgpl-3.0.txt'),('GPL-3.0.txt','https://www.gnu.org/licenses/gpl-3.0.txt')]:
    (root/'assets'/name).write_bytes(urllib.request.urlopen(url,timeout=30).read())
(root/'assets'/'THIRD-PARTY.txt').write_text('''Macros uses Qt / PySide6 6.8.3, dynamically linked libraries provided by The Qt Company under LGPLv3.
Copyright (C) The Qt Company Ltd. and contributors.
Qt source: https://download.qt.io/archive/qt/6.8/6.8.3/
PySide6 source: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
LGPL-3.0.txt and GPL-3.0.txt contain the applicable license texts.
The Macros source and build scripts are available at https://github.com/AdlerDaniel/Macros .
You may rebuild and replace the Qt/PySide6 libraries; reverse engineering for debugging such modifications is permitted.

Lucide icons: https://lucide.dev/ (ISC license in LUCIDE-LICENSE.txt).
Python: https://www.python.org/ (Python Software Foundation license).
PyInstaller: GPLv2 with bootloader exception permitting distribution of bundled applications.
''',encoding='utf-8')
print('Third-party license notices included')
