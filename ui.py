import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import sys
import threading
import time

from PySide6.QtCore import Qt, QSize, QTimer, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QKeySequence, QFont
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QLineEdit, QSpinBox, QDoubleSpinBox,
    QDialog, QFormLayout, QDialogButtonBox, QKeySequenceEdit, QCheckBox, QMessageBox,
    QFileDialog, QFrame)

from app_config import VERSION
from engine import Engine
from hotkeys import Hotkeys, parse_hotkey
from storage import Store, validate_macro
import updater

ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'


def icon(name, color='#c4cad7', size=24):
    svg = (ASSETS / (name+'.svg')).read_text('utf-8').replace('currentColor',color)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    image = QPixmap(size,size)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QIcon(image)


def button(name, hint, checkable=False, text=''):
    b = QPushButton(text)
    b.setIcon(icon(name))
    b.setIconSize(QSize(20,20))
    b.setToolTip(hint)
    b.setAccessibleName(hint)
    b.setCheckable(checkable)
    b.setMinimumSize(42,42)
    b.setCursor(Qt.PointingHandCursor)
    return b


STYLE = '''
QWidget { background:#101218; color:#f0f2f7; font-family:'Segoe UI'; font-size:14px; }
QMainWindow { background:#101218; }
QLabel#brand { font-size:25px; font-weight:700; }
QLabel#caption, QLabel#meta { color:#9ca6ba; font-size:12px; }
QLabel#headline { font-size:30px; font-weight:600; }
QLabel#status { color:#9fe8cf; }
QFrame#panel { background:#181c25; border:1px solid #2b3140; border-radius:16px; }
QFrame#panel QLabel { background:transparent; }
QPushButton { background:#202633; border:1px solid #30394b; border-radius:10px; padding:8px; }
QPushButton:hover { background:#30394b; border-color:#75839e; }
QPushButton:pressed { background:#3d4860; }
QPushButton:focus { border:2px solid #94e2c5; }
QPushButton:checked { background:#264e43; border-color:#80dcbc; }
QPushButton:disabled { color:#788195; background:#191d27; border-color:#272c39; }
QPushButton#primary { background:#93e4c5; color:#0f2c24; font-weight:600; border:0; }
QPushButton#primary:hover { background:#b1efda; }
QPushButton#recording { background:#923c4c; border-color:#ef8c9e; }
QListWidget { background:transparent; border:0; outline:0; }
QListWidget::item { padding:16px 12px; border-radius:10px; margin:3px 0; color:#bac3d3; }
QListWidget::item:selected { background:#27392f; color:#c4f4e2; }
QListWidget::item:hover { background:#232936; }
QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit { background:#1c222e; border:1px solid #374155; border-radius:8px; padding:10px; selection-background-color:#427e68; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus { border-color:#94e2c5; }
QLineEdit#name { background:transparent; border:0; padding:0; font-size:28px; font-weight:600; }
QLineEdit#name:focus { border-bottom:1px solid #94e2c5; }
QCheckBox { spacing:10px; }
QCheckBox::indicator { width:18px; height:18px; }
QDialog { background:#151922; }
QToolTip { background:#30394b; color:#f0f2f7; border:1px solid #657089; padding:7px; }
'''


class Settings(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Настройки')
        self.setMinimumWidth(390)
        self.window = window
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for key,label in [('record_hotkey','Запись'),('play_hotkey','Воспроизведение'),('stop_hotkey','Остановка')]:
            edit = QKeySequenceEdit(QKeySequence(window.store.data['settings'][key]))
            edit.setMaximumSequenceLength(1)
            self.fields[key] = edit
            form.addRow(label,edit)
        self.auto = QCheckBox('Обновлять при запуске')
        self.auto.setChecked(window.store.data['settings']['auto_update'])
        form.addRow(self.auto)
        self.coordinates = QCheckBox('Учитывать координаты мыши при записи')
        self.coordinates.setChecked(window.store.data['settings']['mouse_coordinates'])
        self.coordinates.setToolTip('Включено: возврат в начальную точку перед каждым повтором.\nВыключено: движение от текущего положения курсора.')
        form.addRow(self.coordinates)
        layout.addLayout(form)
        version = QLabel('Версия '+VERSION)
        version.setObjectName('caption')
        layout.addWidget(version)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#ffafbb;')
        layout.addWidget(self.error)
        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Save).setText('Сохранить')
        actions.button(QDialogButtonBox.Cancel).setText('Отмена')
        actions.accepted.connect(self.save)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def save(self):
        values = {key:edit.keySequence().toString(QKeySequence.PortableText) for key,edit in self.fields.items()}
        try:
            self.window.hotkeys.configure({i+1:values[k] for i,k in enumerate(self.fields)})
            self.window.store.data['settings'].update(values,auto_update=self.auto.isChecked(),mouse_coordinates=self.coordinates.isChecked())
            self.window.store.save()
        except (ValueError,OSError) as exc:
            self.error.setText(str(exc))
            return
        self.window.refresh_hints()
        self.window.coordinates_btn.setChecked(self.coordinates.isChecked())
        self.accept()


class Window(QMainWindow):
    def __init__(self, store=None, engine=None, no_update=False):
        super().__init__()
        self.store = store or Store()
        self.engine = engine or Engine()
        self.setWindowTitle('Macros')
        self.setWindowIcon(icon('zap','#93e4c5',64))
        self.resize(1000,650)
        self.setMinimumSize(820,570)
        self.loading = False
        self.pending = False
        self.pending_update = None
        self.record_target = None
        self.bus = queue.SimpleQueue()
        self.hotkeys = Hotkeys(int(self.winId()))
        self.build()
        self.populate()
        self.refresh_hints()
        settings = self.store.data['settings']
        try:
            self.hotkeys.configure({1:settings['record_hotkey'],2:settings['play_hotkey'],3:settings['stop_hotkey']})
        except ValueError as exc:
            QTimer.singleShot(100, lambda message=str(exc):self.error(message))
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(50)
        if self.store.error:
            QTimer.singleShot(300,lambda:self.error(self.store.error))
        if settings['auto_update'] and not no_update:
            QTimer.singleShot(1200, lambda:threading.Thread(target=updater.check_and_download,args=(self.bus,),daemon=True).start())

    def build(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(28,24,28,20)
        main.setSpacing(24)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon('zap','#93e4c5',30).pixmap(30,30))
        header.addWidget(logo)
        brand = QLabel('Macros')
        brand.setObjectName('brand')
        header.addWidget(brand)
        header.addStretch()
        self.state = QLabel('Готово')
        self.state.setObjectName('status')
        header.addWidget(self.state)
        self.settings_btn = button('settings-2','Настройки')
        self.settings_btn.clicked.connect(lambda:Settings(self).exec())
        header.addWidget(self.settings_btn)
        main.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(24)
        sidebar = QVBoxLayout()
        sidebar.setSpacing(12)
        sidehead = QHBoxLayout()
        library = QLabel('Библиотека')
        library.setObjectName('caption')
        sidehead.addWidget(library)
        sidehead.addStretch()
        self.add_btn = button('plus','Новый макрос')
        self.add_btn.clicked.connect(self.new_macro)
        sidehead.addWidget(self.add_btn)
        sidebar.addLayout(sidehead)
        self.list = QListWidget()
        self.list.setFixedWidth(245)
        self.list.setAccessibleName('Библиотека макросов')
        self.list.currentRowChanged.connect(self.select)
        sidebar.addWidget(self.list,1)
        sidebottom = QHBoxLayout()
        self.clicker_btn = button('mouse-pointer-2','Создать кликер')
        self.clicker_btn.clicked.connect(self.create_clicker)
        self.import_btn = button('download','Импорт макроса')
        self.import_btn.clicked.connect(self.import_macro)
        sidebottom.addWidget(self.clicker_btn)
        sidebottom.addWidget(self.import_btn)
        sidebottom.addStretch()
        sidebar.addLayout(sidebottom)
        body.addLayout(sidebar)
        right = QVBoxLayout()
        right.setSpacing(20)
        title = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setObjectName('name')
        self.name.setPlaceholderText('Новый макрос')
        self.name.setAccessibleName('Имя макроса')
        self.name.editingFinished.connect(self.rename)
        title.addWidget(self.name,1)
        self.export_btn = button('upload','Экспорт макроса')
        self.export_btn.clicked.connect(self.export_macro)
        self.duplicate_btn = button('copy','Дублировать')
        self.duplicate_btn.clicked.connect(self.duplicate)
        self.delete_btn = button('trash-2','Удалить макрос')
        self.delete_btn.clicked.connect(self.delete)
        for b in (self.export_btn,self.duplicate_btn,self.delete_btn):
            title.addWidget(b)
        right.addLayout(title)
        self.preview = QFrame()
        self.preview.setObjectName('panel')
        preview = QVBoxLayout(self.preview)
        preview.setContentsMargins(28,28,28,28)
        preview.addStretch()
        self.preview_icon = QLabel()
        self.preview_icon.setAlignment(Qt.AlignCenter)
        self.preview_icon.setPixmap(icon('circle-dot','#93e4c5',64).pixmap(64,64))
        preview.addWidget(self.preview_icon)
        self.duration = QLabel('00:00.0')
        self.duration.setObjectName('headline')
        self.duration.setAlignment(Qt.AlignCenter)
        preview.addWidget(self.duration)
        self.meta = QLabel('Начните запись')
        self.meta.setObjectName('meta')
        self.meta.setAlignment(Qt.AlignCenter)
        preview.addWidget(self.meta)
        preview.addStretch()
        self.preview.setMinimumHeight(230)
        right.addWidget(self.preview,1)
        controls = QHBoxLayout()
        self.keyboard_btn = button('keyboard','Записывать клавиатуру',True)
        self.mouse_btn = button('mouse','Записывать мышь',True)
        self.keyboard_btn.setChecked(True)
        self.mouse_btn.setChecked(True)
        controls.addWidget(self.keyboard_btn)
        controls.addWidget(self.mouse_btn)
        self.coordinates_btn = button('crosshair','Учитывать координаты мыши при записи',True)
        self.coordinates_btn.setToolTip('Координаты мыши при записи\nВключено: старт и каждый повтор из записанной точки.\nВыключено: движение относительно текущего курсора.')
        self.coordinates_btn.setChecked(self.store.data['settings']['mouse_coordinates'])
        self.coordinates_btn.toggled.connect(self.save_coordinate_setting)
        controls.addWidget(self.coordinates_btn)
        controls.addStretch()
        self.record_btn = button('circle','Запись',text='Запись')
        self.record_btn.clicked.connect(self.toggle_record)
        self.play_btn = button('play','Воспроизвести',text='Запуск')
        self.play_btn.setObjectName('primary')
        self.play_btn.setIcon(icon('play','#12372b'))
        self.play_btn.clicked.connect(self.toggle_play)
        controls.addWidget(self.record_btn)
        controls.addWidget(self.play_btn)
        right.addLayout(controls)
        options = QHBoxLayout()
        self.repeats = QSpinBox()
        self.repeats.setRange(1,1_000_000)
        self.repeats.setSuffix(' ×')
        self.repeats.setToolTip('Количество повторов')
        self.repeats.setAccessibleName('Количество повторов')
        self.forever = button('infinity','Бесконечно',True)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.1,10)
        self.speed.setSingleStep(.1)
        self.speed.setValue(1)
        self.speed.setSuffix(' ×')
        self.speed.setToolTip('Скорость воспроизведения')
        self.speed.setAccessibleName('Скорость воспроизведения')
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0,3600)
        self.gap.setSingleStep(.1)
        self.gap.setSuffix(' с')
        self.gap.setToolTip('Пауза между повторами')
        self.gap.setAccessibleName('Пауза между повторами')
        for glyph,control in [('repeat-2',self.repeats),('gauge',self.speed),('timer',self.gap)]:
            label = QLabel()
            label.setPixmap(icon(glyph).pixmap(18,18))
            options.addWidget(label)
            options.addWidget(control)
            if control is self.repeats:
                options.addWidget(self.forever)
        right.addLayout(options)
        for control in (self.repeats,self.speed,self.gap):
            control.valueChanged.connect(self.save_options)
        self.forever.toggled.connect(self.save_options)
        body.addLayout(right,1)
        main.addLayout(body,1)
        footer = QHBoxLayout()
        self.hints = QLabel()
        self.hints.setObjectName('caption')
        footer.addWidget(self.hints)
        footer.addStretch()
        self.update_label = QLabel('v'+VERSION)
        self.update_label.setObjectName('caption')
        self.update_label.setMaximumWidth(300)
        footer.addWidget(self.update_label)
        main.addLayout(footer)

    def refresh_hints(self):
        s = self.store.data['settings']
        self.hints.setText(f"{s['record_hotkey']}  запись    ·    {s['play_hotkey']}  запуск    ·    {s['stop_hotkey']}  стоп")

    def current(self):
        row = self.list.currentRow()
        macros = self.store.data['macros']
        return macros[row] if 0 <= row < len(macros) else None

    def populate(self, selected=None):
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.store.data['macros']:
            self.list.addItem(QListWidgetItem(icon('mouse-pointer-2' if m.get('clicker') else 'zap'),m['name']))
        if self.list.count():
            self.list.setCurrentRow(min(selected if selected is not None else 0,self.list.count()-1))
        self.list.blockSignals(False)
        self.select()

    def select(self, *_):
        self.loading = True
        m = self.current()
        self.name.setText(m['name'] if m else '')
        self.name.setEnabled(bool(m))
        if m:
            self.forever.setChecked(m.get('repeats',1)==0)
            self.repeats.setValue(max(1,m.get('repeats',1)))
            self.speed.setValue(m.get('speed',1))
            self.gap.setValue(m.get('gap',.2))
        self.loading = False
        self.refresh()

    def save_options(self, *_):
        if self.loading:
            return
        m = self.current()
        if m:
            m.update(repeats=0 if self.forever.isChecked() else self.repeats.value(),speed=self.speed.value(),gap=self.gap.value())
            self.save()
        self.repeats.setEnabled(not self.forever.isChecked() and self.engine.mode=='idle')

    def save_coordinate_setting(self, enabled):
        self.store.data['settings']['mouse_coordinates'] = enabled
        self.save()

    def save(self):
        try:
            self.store.save()
            return True
        except OSError as exc:
            self.error('Не удалось сохранить: '+str(exc))
            return False

    def rename(self):
        m = self.current()
        if m:
            name = self.name.text().strip()[:100]
            if name:
                m['name'] = name
                self.list.currentItem().setText(name)
                self.save()
            else:
                self.name.setText(m['name'])

    def new_macro(self):
        m = self.store.add(f'Макрос {len(self.store.data["macros"])+1:02}',[])
        self.populate(len(self.store.data['macros'])-1)
        self.name.setFocus()
        self.name.selectAll()
        return m

    def duplicate(self):
        m = self.current()
        if m:
            self.store.add(m['name']+' · копия',json.loads(json.dumps(m['events'])),repeats=m['repeats'],speed=m['speed'],gap=m['gap'],clicker=m.get('clicker',False),**{k:m[k] for k in ('mouse_mode','mouse_start') if k in m})
            self.populate(len(self.store.data['macros'])-1)

    def delete(self):
        m = self.current()
        if m and QMessageBox.question(self,'Удалить',f'Удалить «{m["name"]}»?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            row = self.list.currentRow()
            self.store.data['macros'].pop(row)
            self.save()
            self.populate(row)

    def toggle_record(self, checked=False, trigger=None):
        if self.pending:
            self.pending = False
            self.refresh()
            return
        if self.engine.mode == 'recording':
            events = self.engine.finish_record(trigger)
            if events:
                m = self.record_target
                if m:
                    m['events'] = events
                    m.pop('mouse_start',None)
                    m.update(self.engine.record_options)
                    m.pop('clicker',None)
                    self.save()
                else:
                    self.store.add(f'Макрос {len(self.store.data["macros"])+1:02}',events,**self.engine.record_options)
                    self.populate(len(self.store.data['macros'])-1)
            self.record_target = None
            self.showNormal()
            self.refresh()
        elif self.engine.mode == 'idle':
            if not self.keyboard_btn.isChecked() and not self.mouse_btn.isChecked():
                self.error('Выберите клавиатуру, мышь или оба устройства')
                return
            # Recording a populated macro creates a new one; an empty selected macro is filled.
            m = self.current()
            self.record_target = m if m and not m['events'] else None
            self.pending = True
            self.showMinimized()
            self.refresh()
            QTimer.singleShot(450,self.begin_record)

    def begin_record(self):
        if not self.pending:
            return
        self.pending = False
        self.engine.record(self.keyboard_btn.isChecked(),self.mouse_btn.isChecked(),self.coordinates_btn.isChecked())
        self.refresh()

    def toggle_play(self, checked=False):
        if self.engine.mode=='playing':
            self.engine.stop()
        elif self.engine.mode=='idle' and not self.pending:
            m = self.current()
            if not m or not m['events']:
                return
            snapshot = json.loads(json.dumps(m))
            self.pending = True
            self.showMinimized()
            self.refresh()
            def begin():
                if not self.pending:
                    return
                self.pending = False
                try:
                    self.engine.play(snapshot)
                except (ValueError,RuntimeError) as exc:
                    self.error(str(exc))
                self.refresh()
            QTimer.singleShot(450,begin)

    def stop_all(self, trigger=None):
        self.pending = False
        if self.engine.mode=='recording':
            self.toggle_record(trigger=trigger)
        else:
            self.engine.stop()
        self.refresh()

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message==0x312:
            vk = (int(msg.lParam) >> 16) & 0xFFFF
            if self.engine.key_origin.get(vk,False):
                return True,0
            if self.active_dialog():
                if msg.wParam == 3:
                    self.stop_all(parse_hotkey(self.store.data['settings']['stop_hotkey']))
                return True,0
            if msg.wParam==1:
                self.toggle_record(trigger=parse_hotkey(self.store.data['settings']['record_hotkey']))
            elif msg.wParam==2:
                self.toggle_play()
            elif msg.wParam==3:
                self.stop_all(parse_hotkey(self.store.data['settings']['stop_hotkey']))
            return True,0
        return super().nativeEvent(event_type,message)

    def active_dialog(self):
        return QApplication.activeModalWidget() is not None

    def refresh(self):
        mode = self.engine.mode
        busy = mode!='idle' or self.pending
        m = self.current()
        self.state.setText('Подготовка…' if self.pending else {'idle':'Готово','recording':'Запись','playing':'Воспроизведение'}[mode])
        self.preview.setToolTip(('Относительное движение: каждый повтор от текущего курсора' if m.get('mouse_mode')=='relative' else 'Абсолютные координаты: каждый повтор из начальной точки') if m else '')
        self.record_btn.setText('Стоп' if mode=='recording' else 'Запись')
        self.record_btn.setObjectName('recording' if mode=='recording' else '')
        self.record_btn.setIcon(icon('square' if mode=='recording' else 'circle','#ffafbb' if mode=='recording' else '#c4cad7'))
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.play_btn.setText('Стоп' if mode=='playing' else 'Запуск')
        self.play_btn.setIcon(icon('square' if mode=='playing' else 'play','#12372b'))
        self.record_btn.setEnabled(mode!='playing')
        self.play_btn.setEnabled(mode!='recording' and bool(m and m['events']))
        for control in (self.list,self.name,self.add_btn,self.clicker_btn,self.import_btn,self.settings_btn,self.keyboard_btn,self.mouse_btn,self.coordinates_btn,self.speed,self.gap,self.forever):
            control.setEnabled(not busy)
        self.name.setEnabled(not busy and bool(m))
        self.repeats.setEnabled(not busy and not self.forever.isChecked())
        for b in (self.export_btn,self.duplicate_btn,self.delete_btn):
            b.setEnabled(not busy and bool(m))
        if mode=='recording':
            elapsed = time.perf_counter()-self.engine.started
            count = len(self.engine.events)
            self.meta.setText(f'{count:,} событий')
        else:
            elapsed = m['events'][-1]['t'] if m and m['events'] else 0
            if mode!='playing':
                self.meta.setText(f'{len(m["events"]):,} событий' if m and m['events'] else 'Начните запись')
        self.duration.setText(f'{int(elapsed//60):02}:{elapsed%60:04.1f}')

    def poll(self):
        for bus in (self.engine.messages,self.bus):
            while not bus.empty():
                kind,value = bus.get()
                if kind=='error':
                    self.error(value)
                elif kind=='limit':
                    self.stop_all()
                    self.error(value)
                elif kind=='progress':
                    self.meta.setText(f'Повтор {value}')
                elif kind=='finished':
                    self.showNormal()
                    self.refresh()
                elif kind=='update':
                    self.update_label.setText(value)
                    self.update_label.setToolTip(value)
                elif kind=='update_ready':
                    self.pending_update = value
                    self.update_label.setText('Обновление готово')
        if self.engine.mode=='recording':
            self.refresh()
        if self.pending_update and self.engine.mode=='idle' and not self.pending and not self.active_dialog():
            self.update_label.setText('Установка обновления…')
            try:
                updater.start_install(self.pending_update)
                self.pending_update = None
                self.close()
            except OSError as exc:
                self.pending_update = None
                self.error('Не удалось установить обновление: '+str(exc))

    def create_clicker(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Кликер')
        form = QFormLayout(dialog)
        name = QLineEdit('Кликер')
        interval = QDoubleSpinBox()
        interval.setRange(.02,3600)
        interval.setValue(.5)
        interval.setSuffix(' с')
        form.addRow('Имя',name)
        form.addRow('Интервал',interval)
        coordinates = self.coordinates_btn.isChecked()
        info = QLabel('После «Создать» наведите мышь на точку.\nПозиция сохранится через 3 секунды.' if coordinates else 'Клики в текущем положении курсора.')
        form.addRow(info)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Создать')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec()==QDialog.Accepted:
            self.pending = True
            self.showMinimized()
            self.refresh()
            def capture():
                if not self.pending:
                    return
                from engine import POINT
                pt = POINT()
                if coordinates:
                    self.engine.user.GetCursorPos(ctypes.byref(pt))
                events = [{'kind':'button','x':pt.x,'y':pt.y,'button':'left','down':True,'t':0},
                          {'kind':'button','x':pt.x,'y':pt.y,'button':'left','down':False,'t':.01}]
                options = {'mouse_mode':'absolute' if coordinates else 'relative'}
                if coordinates:
                    options['mouse_start'] = {'x':pt.x,'y':pt.y}
                self.store.add(name.text().strip() or 'Кликер',events,repeats=0,gap=max(.01,interval.value()-.01),clicker=True,**options)
                self.pending = False
                self.populate(len(self.store.data['macros'])-1)
                self.showNormal()
            QTimer.singleShot(3000 if coordinates else 0,capture)

    def import_macro(self):
        path,_ = QFileDialog.getOpenFileName(self,'Импорт','','Macros (*.json)')
        if path:
            try:
                if Path(path).stat().st_size > 30_000_000:
                    raise ValueError('Файл больше 30 МБ')
                m = validate_macro(json.loads(Path(path).read_text('utf-8')))
                self.store.add(m['name'],m['events'],repeats=m.get('repeats',1),speed=m.get('speed',1),gap=m.get('gap',.2),clicker=m.get('clicker',False),**{k:m[k] for k in ('mouse_mode','mouse_start') if k in m})
                self.populate(len(self.store.data['macros'])-1)
            except (OSError,ValueError,TypeError) as exc:
                self.error('Не удалось импортировать: '+str(exc))

    def export_macro(self):
        m = self.current()
        if m:
            path,_ = QFileDialog.getSaveFileName(self,'Экспорт','macro.json','Macros (*.json)')
            if path:
                try:
                    Path(path).write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
                except OSError as exc:
                    self.error(str(exc))

    def error(self, text):
        self.showNormal()
        QMessageBox.warning(self,'Macros',text)

    def closeEvent(self, event):
        self.pending = False
        if self.engine.mode=='recording':
            self.toggle_record()
        self.hotkeys.close()
        self.engine.close()
        self.save()
        event.accept()
