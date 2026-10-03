import ctypes
import threading
from types import SimpleNamespace

import pytest

from engine import Engine, MOUSEHOOK, POINT, playback_events, balanced
from storage import Store, validate_macro
from test_core import mock_engine


def path_macro(mode='absolute', repeats=1):
    return {'name':'Path','mouse_mode':mode,'repeats':repeats,'speed':1,'gap':0,
        'events':[{'kind':'move','x':10,'y':5,'t':.01}],
        **({'mouse_start':{'x':0,'y':0}} if mode=='absolute' else {})}


def test_high_frequency_recording_keeps_every_reported_point(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('engine.time.perf_counter',lambda:now[0])
    e = mock_engine()
    e.user = SimpleNamespace(CallNextHookEx=lambda *args:0)
    e.accept_injected = False
    e.mouse_observer = None
    e.cursor_position = lambda:(100,200)
    e.record(keyboard=False,mouse=True)
    for index in range(1,501):
        now[0] = index/1000
        m = MOUSEHOOK(pt=POINT(100+index,200+index),data=0,flags=0,time=index,extra=0)
        e._mouse_hook(0,0x200,ctypes.addressof(m))
    events = e.finish_record()
    assert len(events)==501
    assert [(p['x'],p['y']) for p in events]==[(100+i,200+i) for i in range(501)]
    assert e.record_options['mouse_start']=={'x':100,'y':200}


def test_relative_recording_contains_offsets_not_screen_coordinates():
    e = mock_engine()
    e.user = SimpleNamespace(CallNextHookEx=lambda *args:0)
    e.accept_injected = False
    e.mouse_observer = None
    e.cursor_position = lambda:(789,456)
    e.record(keyboard=False,mouse=True,mouse_coordinates=False)
    m = MOUSEHOOK(pt=POINT(799,451),data=0,flags=0,time=0,extra=0)
    e._mouse_hook(0,0x200,ctypes.addressof(m))
    events = e.finish_record()
    assert [(p['x'],p['y']) for p in events]==[(0,0),(10,-5)]
    assert e.record_options=={'mouse_mode':'relative'}


def test_every_pixel_is_replayed_with_ordered_keyboard_events():
    m = path_macro()
    m['events'].insert(0,{'kind':'key','vk':65,'down':True,'t':.005})
    points = list(playback_events(m))
    mouse = [p for p in points if p['kind']=='move']
    assert len(mouse)==11
    assert (mouse[0]['x'],mouse[0]['y'])==(0,0)
    assert (mouse[-1]['x'],mouse[-1]['y'])==(10,5)
    assert all(max(abs(b['x']-a['x']),abs(b['y']-a['y']))==1 for a,b in zip(mouse,mouse[1:]))
    assert [e['t'] for e in points]==sorted(e['t'] for e in points)
    assert points.index(m['events'][0])==5


def test_silent_intervals_are_preserved():
    m = path_macro()
    m['events'][0]['t'] = 3
    points = list(playback_events(m))
    assert points[0]['t']==0
    assert points[1]['t']>=2.992
    assert points[-1]['t']==3


def test_absolute_repeats_reset_to_recorded_origin():
    e = mock_engine()
    sent = []
    e.send = sent.append
    e.play(path_macro(repeats=3))
    e.worker.join(2)
    assert e.mode=='idle'
    assert [(sent[i]['x'],sent[i]['y']) for i in (0,11,22)]==[(0,0)]*3
    assert [(sent[i]['x'],sent[i]['y']) for i in (10,21,32)]==[(10,5)]*3


def test_relative_repeats_continue_from_current_cursor_without_reset():
    e = mock_engine()
    current = [100,200]
    sent = []
    e.cursor_position = lambda:tuple(current)
    def send(event):
        sent.append(event)
        if event['kind']!='key':
            current[:] = [event['x'],event['y']]
    e.send = send
    e.play(path_macro(mode='relative',repeats=2))
    e.worker.join(2)
    assert e.mode=='idle'
    assert [(sent[i]['x'],sent[i]['y']) for i in (0,11)]==[(100,200),(110,205)]
    assert current==[120,210]


def test_stop_interrupts_long_mouse_motion_promptly():
    e = mock_engine()
    sent = threading.Event()
    e.send = lambda event:sent.set()
    m = path_macro()
    m['events'][0].update(x=10000,y=5000,t=10)
    e.play(m)
    assert sent.wait(.5)
    e.stop()
    e.worker.join(.2)
    assert not e.worker.is_alive() and e.mode=='idle'


def test_legacy_macro_still_uses_absolute_coordinates():
    m = {'name':'Legacy','events':[{'kind':'button','x':123,'y':234,'button':'left','down':True,'t':.5}]}
    points = list(playback_events(m))
    assert points[0]=={'kind':'move','x':123,'y':234,'t':0}
    assert points[-1]==m['events'][0]


def test_stopping_recorded_drag_releases_at_its_endpoint():
    result = balanced([
        {'kind':'button','button':'left','down':True,'x':10,'y':20,'t':0},
        {'kind':'move','x':30,'y':40,'t':1},
    ])
    assert result[-1]=={'kind':'button','button':'left','down':False,'x':30,'y':40,'t':1}


def test_pixel_movement_does_not_wait_for_slow_injected_input_hooks():
    e = mock_engine()
    moved = []
    e.accept_injected = False
    e.mouse_observer = None
    def unexpected_input(*args):
        raise AssertionError('Pixel movement must not go through SendInput')
    e.user = SimpleNamespace(SetCursorPos=lambda x,y:moved.append((x,y)) or 1,SendInput=unexpected_input)
    Engine.send_batch(e,[{'kind':'move','x':i,'y':10,'t':i*.001} for i in range(5)])
    assert moved==[(i,10) for i in range(5)]


def test_recorded_coordinate_mode_survives_reload(tmp_path):
    s = Store(tmp_path)
    s.add('Relative',path_macro('relative')['events'],mouse_mode='relative')
    s.add('Absolute',path_macro()['events'],mouse_mode='absolute',mouse_start={'x':0,'y':0})
    data = Store(tmp_path).data
    assert data['settings']['mouse_coordinates'] is True
    assert data['macros'][0]['mouse_mode']=='relative'
    assert data['macros'][1]['mouse_start']=={'x':0,'y':0}


@pytest.mark.parametrize('options',[
    {'mouse_mode':'invalid'}, {'mouse_start':{'x':0}}, {'mouse_start':{'x':'0','y':0}},
])
def test_rejects_invalid_mouse_metadata(options):
    with pytest.raises(ValueError):
        validate_macro({**path_macro(),**options})
