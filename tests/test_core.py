import json
import threading
import time
import pytest
from storage import Store, validate_macro
from hotkeys import parse_hotkey
from engine import Engine, balanced, INPUT
import ctypes
from updater import version_tuple, replace_and_launch


def sample(**extra):
    return {'name':'Test','events':[{'kind':'key','vk':65,'down':True,'t':0},
        {'kind':'key','vk':65,'down':False,'t':.03}], 'repeats':1,'speed':1,'gap':0,**extra}


def test_persistence(tmp_path):
    s = Store(tmp_path)
    m = sample()
    saved = s.add(m['name'],m['events'],repeats=0)
    assert Store(tmp_path).data['macros'][0]==saved


def test_damaged_library_preserved(tmp_path):
    (tmp_path/'library.json').write_text('broken')
    s = Store(tmp_path)
    assert s.error and not s.data['macros']
    assert list(tmp_path.glob('library-damaged-*.json'))[0].read_text()=='broken'


@pytest.mark.parametrize('event',[
    {'kind':'key','vk':1000,'down':True,'t':0},
    {'kind':'key','vk':65,'down':True,'t':float('nan')},
    {'kind':'shell','t':0},
    {'kind':'button','x':0,'y':0,'button':'unknown','down':True,'t':0},
    {'kind':'scroll','x':0,'y':0,'delta':'bad','t':0},
])
def test_import_validation(event):
    with pytest.raises(ValueError):
        validate_macro(sample(events=[event]))


def test_hotkey_parse():
    assert parse_hotkey('Ctrl+Alt+R')==(3,82)
    assert parse_hotkey('F10')==(0,121)
    with pytest.raises(ValueError):
        parse_hotkey('R')


def test_balance():
    events = [{'kind':'key','vk':65,'down':False,'t':0},
        {'kind':'key','vk':66,'down':True,'t':1}]
    result = balanced(events)
    assert [e['down'] for e in result]==[True,False]
    assert all(e['vk']==66 for e in result)


def test_sendinput_layout():
    assert ctypes.sizeof(INPUT)==(40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)


def mock_engine():
    import queue
    e = Engine.__new__(Engine)
    e.lock = threading.RLock()
    e.mode = 'idle'
    e.stop_event = threading.Event()
    e.messages = queue.SimpleQueue()
    e.worker = None
    return e


def test_finite_and_infinite_playback():
    e = mock_engine()
    sent = []
    e.send = lambda event:sent.append(event)
    e.play(sample(repeats=3))
    e.worker.join(2)
    assert len(sent)==6 and e.mode=='idle'
    sent.clear()
    e.play(sample(repeats=0))
    time.sleep(.12)
    e.stop()
    e.worker.join(1)
    assert not e.worker.is_alive() and len(sent)>=4
    assert sent[-1]['down'] is False


def test_interrupt_releases_held_key():
    e = mock_engine()
    sent = []
    e.send = lambda event:sent.append(event)
    e.play(sample(events=[{'kind':'key','vk':65,'down':True,'t':0},
        {'kind':'key','vk':65,'down':False,'t':10}]))
    time.sleep(.05)
    e.stop()
    e.worker.join(1)
    assert [x['down'] for x in sent]==[True,False]


def test_failed_playback_returns_idle_and_releases():
    e = mock_engine()
    sent = []
    def send(event):
        sent.append(event)
        if len(sent)==2:
            raise RuntimeError('blocked')
    e.send = send
    e.play(sample())
    e.worker.join(1)
    assert e.mode=='idle' and sent[-1]['down'] is False
    assert any(e.messages.get()[0]=='error' for _ in range(e.messages.qsize()))


def test_version_order():
    assert version_tuple('v1.10.0')>version_tuple('1.9.9')
    with pytest.raises(ValueError):
        version_tuple('main')


class DummyProcess:
    def poll(self):
        return 0


def test_updater_success(tmp_path):
    source = tmp_path/'download'/'Macros.exe'
    source.parent.mkdir()
    source.write_bytes(b'new')
    target = tmp_path/'Macros.exe'
    target.write_bytes(b'old')
    def launch(args,**kwargs):
        from pathlib import Path
        Path(args[2]).write_text('ready')
        return DummyProcess()
    assert replace_and_launch(source,target,launch,timeout=.1)
    assert target.read_bytes()==b'new'
    assert not target.with_suffix('.previous.exe').exists()


def test_updater_rolls_back_unhealthy_binary(tmp_path):
    source = tmp_path/'download'/'Macros.exe'
    source.parent.mkdir()
    source.write_bytes(b'bad')
    target = tmp_path/'Macros.exe'
    target.write_bytes(b'good')
    calls = []
    def launch(args,**kwargs):
        calls.append(args)
        return DummyProcess()
    assert not replace_and_launch(source,target,launch,timeout=.1)
    assert target.read_bytes()==b'good'
    assert calls[-1][1]=='--no-update'
