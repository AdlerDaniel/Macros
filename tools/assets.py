"""Fetch the official Lucide SVGs and license once; runtime works offline."""
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1]
dest = root/'assets'
dest.mkdir(exist_ok=True)
names = ['circle','square','play','plus','settings-2','keyboard','mouse','infinity','trash-2','copy','download','upload','mouse-pointer-2','repeat-2','timer','gauge','folder-open','circle-dot','zap','chevron-right']
base = 'https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0/'
for name in names:
    (dest/f'{name}.svg').write_bytes(urllib.request.urlopen(base+f'icons/{name}.svg', timeout=30).read())
(dest/'LUCIDE-LICENSE.txt').write_bytes(urllib.request.urlopen(base+'LICENSE',timeout=30).read())
print('Lucide assets downloaded:', len(names))
