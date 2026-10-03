"""End-to-end test against actual Windows hooks, hotkeys and a disposable input window."""
import ctypes as c
import json
from pathlib import Path
import tempfile
import time


def run_tests(output):
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton
    from PySide6.QtCore import Qt
    from ui import Window, STYLE
    from engine import Engine, INPUT, KEYINPUT, MOUSEINPUT, POINT
    from storage import Store
    output.mkdir(parents=True,exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    root = Path(tempfile.mkdtemp(prefix='Macros-selftest-'))
    engine = Engine(accept_injected=True)
    window = Window(Store(root),engine,no_update=True)
    target = QWidget()
    target.setWindowTitle('Macros input test')
    target.resize(430,160)
    layout = QVBoxLayout(target)
    edit = QLineEdit()
    layout.addWidget(edit)
    clicks = []
    click = QPushButton('Test target')
    click.clicked.connect(lambda:clicks.append(1))
    layout.addWidget(click)
    original = POINT()
    engine.user.GetCursorPos(c.byref(original))
    foreground = engine.user.GetForegroundWindow()
    report = {'passed':[]}

    def pump(seconds=.15):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents()
            time.sleep(.005)

    def focus():
        target.show()
        target.raise_()
        target.activateWindow()
        engine.user.SetForegroundWindow(c.c_void_p(int(target.winId())))
        edit.setFocus()
        pump(.2)

    def key(vk,down):
        packet = INPUT(type=1,ki=KEYINPUT(vk,0,0 if down else 2,0,0))
        assert engine.user.SendInput(1,c.byref(packet),c.sizeof(INPUT))==1
        pump(.045)

    def tap(vk):
        key(vk,True)
        key(vk,False)

    def mouse(flags,data=0):
        packet = INPUT(type=0,mi=MOUSEINPUT(0,0,data,flags,0,0))
        assert engine.user.SendInput(1,c.byref(packet),c.sizeof(INPUT))==1
        pump(.06)

    try:
        window.show()
        pump(.3)
        window.hotkeys.configure({1:'F6',2:'F8',3:'F10'})
        focus()
        tap(0x75)  # F6 global recording toggle
        pump(.6)
        assert engine.mode=='recording',engine.mode
        focus()
        for vk in (65,66,67):
            tap(vk)
        # Record genuine WM mouse hook movement, click and wheel events.
        pos = edit.mapToGlobal(edit.rect().center())
        engine.user.SetCursorPos(pos.x(),pos.y())
        pump(.1)
        packet = INPUT(type=0,mi=MOUSEINPUT(2,0,0,1,0,0))
        assert engine.user.SendInput(1,c.byref(packet),c.sizeof(INPUT))==1
        pump(.1)
        mouse(2)
        mouse(4)
        mouse(0x800,120)
        tap(0x75)
        pump(.3)
        assert engine.mode=='idle'
        macro = window.current()
        report['recorded_events'] = macro['events'] if macro else []
        report['target_text'] = edit.text()
        assert macro and len(macro['events']) >= 9
        assert not any(e.get('vk')==0x75 for e in macro['events'])
        assert {'key','move','button','scroll'} <= {e['kind'] for e in macro['events']}
        report['passed'].append('Windows global hotkeys + keyboard/mouse recording + stop-key exclusion')
        expected_text = edit.text()
        assert len(expected_text)==3,expected_text
        # Play the recorded keys twice through SendInput, using the F8 global trigger.
        keyboard_events = [e for e in macro['events'] if e['kind']=='key']
        macro.update(events=keyboard_events,repeats=2,speed=3,gap=.05)
        window.store.save()
        window.select()
        edit.clear()
        focus()
        tap(0x77)
        pump(.6)
        focus()
        deadline = time.monotonic()+5
        while engine.mode!='idle' and time.monotonic()<deadline:
            pump(.05)
        pump(.1)
        assert edit.text()==expected_text*2,edit.text()
        assert engine.mode=='idle'
        report['passed'].append('Recorded input replayed twice into actual Windows text field')
        macro.update(repeats=0,gap=.05)
        window.select()
        focus()
        tap(0x77)
        pump(.6)
        focus()
        pump(.3)
        tap(0x79)
        pump(.2)
        assert engine.mode=='idle'
        report['passed'].append('Infinite playback interrupted by global F10')
        # Held-input cleanup during interruption and keyboard/mouse selection.
        engine.record(keyboard=False,mouse=True)
        tap(65)
        engine.user.SetCursorPos(pos.x()+2,pos.y()+2)
        packet = INPUT(type=0,mi=MOUSEINPUT(2,0,0,1,0,0))
        engine.user.SendInput(1,c.byref(packet),c.sizeof(INPUT))
        pump(.1)
        events = engine.finish_record()
        assert events and all(e['kind']!='key' for e in events)
        engine.record(keyboard=True,mouse=False)
        mouse(2)
        mouse(4)
        key(66,True)
        events = engine.finish_record()
        key(66,False)
        assert [e['down'] for e in events]==[True,False]
        assert all(e['kind']=='key' for e in events)
        report['passed'].append('Device selection + balanced held-key recording')
        # Mouse playback must hit a real widget, not just issue successful Win32 calls.
        focus()
        click_pos = click.mapToGlobal(click.rect().center())
        click_macro = {'name':'Mouse test','repeats':3,'speed':1,'gap':.04,'events':[
            {'kind':'button','x':click_pos.x(),'y':click_pos.y(),'button':'left','down':True,'t':.02},
            {'kind':'button','x':click_pos.x(),'y':click_pos.y(),'button':'left','down':False,'t':.04}]}
        engine.play(click_macro)
        pump(.5)
        assert len(clicks)==3,len(clicks)
        report['passed'].append('Mouse replay clicks a real target three times')
        # Own replay events must never activate control hotkeys.
        focus()
        guarded = {'name':'Reserved key','repeats':1,'speed':1,'gap':0,'events':[
            {'kind':'key','vk':119,'down':True,'t':.02},
            {'kind':'key','vk':119,'down':False,'t':.04},
            {'kind':'key','vk':65,'down':True,'t':.08},
            {'kind':'key','vk':65,'down':False,'t':.1}]}
        before = len(edit.text())
        engine.play(guarded)
        pump(.4)
        assert len(edit.text())==before+1
        report['passed'].append('Replayed control keys do not trigger global hotkeys')
        # Reassign all controls, start/stop with a modifier chord and verify trimming.
        window.hotkeys.configure({1:'Ctrl+Alt+Shift+F12',2:'F9',3:'F11'})
        window.store.data['settings'].update(record_hotkey='Ctrl+Alt+Shift+F12',play_hotkey='F9',stop_hotkey='F11')
        focus()
        key(17,True)
        key(18,True)
        key(16,True)
        tap(123)
        key(16,False)
        key(18,False)
        key(17,False)
        pump(.6)
        assert engine.mode=='recording'
        focus()
        tap(65)
        key(17,True)
        key(18,True)
        key(16,True)
        tap(123)
        key(16,False)
        key(18,False)
        key(17,False)
        pump(.2)
        assert engine.mode=='idle'
        reassigned = window.current()
        assert {e.get('vk') for e in reassigned['events']}=={65}
        report['passed'].append('Reassigned modifier hotkeys work and entire stop chord is removed')
        window.hotkeys.configure({1:'F6',2:'F8',3:'F10'})
        window.store.data['settings'].update(record_hotkey='F6',play_hotkey='F8',stop_hotkey='F10')
        restored = Store(root)
        assert restored.data['macros'][0]['name']==macro['name']
        report['passed'].append('Library and playback settings survive reload')
        macro.update(events=keyboard_events,repeats=5,speed=1,gap=.2,name='Рабочий макрос')
        window.store.add('Кликер', [{'kind':'button','x':pos.x(),'y':pos.y(),'button':'left','down':True,'t':0},
            {'kind':'button','x':pos.x(),'y':pos.y(),'button':'left','down':False,'t':.01}],repeats=0,clicker=True)
        window.populate(0)
        window.refresh_hints()
        target.hide()
        window.showNormal()
        pump(.25)
        window.grab().save(str(output/'interface.png'))
        window.resize(820,570)
        pump(.1)
        window.grab().save(str(output/'interface-small.png'))
        report['passed'].append('UI screenshots at default and minimum window sizes')
        report['success'] = True
    except Exception as exc:
        report['success'] = False
        report['error'] = repr(exc)
        import traceback
        report['traceback'] = traceback.format_exc()
        raise
    finally:
        window.close()
        target.close()
        engine.user.SetCursorPos(original.x,original.y)
        engine.user.SetForegroundWindow(c.c_void_p(foreground))
        (output/'self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
