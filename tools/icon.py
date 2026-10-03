from pathlib import Path
import struct
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import QRectF, QByteArray, QBuffer, QIODevice
from PySide6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
app = QApplication.instance() or QApplication(['icon','-platform','offscreen'])
image = QImage(256,256,QImage.Format_ARGB32)
image.fill(QColor('#101218'))
painter = QPainter(image)
painter.setRenderHint(QPainter.Antialiasing)
svg = (root/'assets'/'zap.svg').read_text().replace('currentColor','#93e4c5')
QSvgRenderer(QByteArray(svg.encode())).render(painter,QRectF(40,40,176,176))
painter.end()
data = QByteArray()
buffer = QBuffer(data)
buffer.open(QIODevice.WriteOnly)
image.save(buffer,'PNG')
png = bytes(data)
(root/'assets'/'app.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(png),22)+png)
print('Application icon ready')
