"""Windows low-level input hooks and interruptible, timestamped playback."""
import ctypes as c
from ctypes import wintypes as w
import heapq
import queue
import sys
import threading
import time

MAGIC = 0x4D414352
ULONG_PTR = c.c_size_t
LRESULT = c.c_ssize_t


class POINT(c.Structure):
    _fields_ = [('x', w.LONG), ('y', w.LONG)]


class KBD(c.Structure):
    _fields_ = [('vk', w.DWORD), ('scan', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ULONG_PTR)]


class MOUSEHOOK(c.Structure):
    _fields_ = [('pt', POINT), ('data', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ULONG_PTR)]


class MOUSEINPUT(c.Structure):
    _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('data', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ULONG_PTR)]


class KEYINPUT(c.Structure):
    _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ULONG_PTR)]


class HARDWARE(c.Structure):
    _fields_ = [('msg', w.DWORD), ('low', w.WORD), ('high', w.WORD)]


class INPUTUNION(c.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYINPUT), ('hi', HARDWARE)]


class INPUT(c.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', w.DWORD), ('u', INPUTUNION)]


class PlaybackClock:
    """High-resolution Windows waits, with cancellation checked at least every 4 ms."""
    def __init__(self, stop):
        self.stop = stop
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateWaitableTimerExW.restype = c.c_void_p
        self.kernel.CreateWaitableTimerExW.argtypes = [c.c_void_p,w.LPCWSTR,w.DWORD,w.DWORD]
        self.kernel.SetWaitableTimer.argtypes = [c.c_void_p,c.POINTER(c.c_longlong),w.LONG,c.c_void_p,c.c_void_p,w.BOOL]
        self.kernel.WaitForSingleObject.argtypes = [c.c_void_p,w.DWORD]
        self.kernel.CloseHandle.argtypes = [c.c_void_p]
        self.timer = self.kernel.CreateWaitableTimerExW(None,None,2,0x100002)
        self.winmm = c.WinDLL('winmm')
        self.period = self.winmm.timeBeginPeriod(1)==0

    def until(self, deadline):
        while not self.stop.is_set():
            remaining = deadline-time.perf_counter()
            if remaining <= 0:
                return True
            delay = min(remaining,.004)
            due = c.c_longlong(-max(1,round(delay*10_000_000)))
            if self.timer and self.kernel.SetWaitableTimer(self.timer,c.byref(due),0,None,None,False):
                self.kernel.WaitForSingleObject(self.timer,100)
            else:
                self.stop.wait(delay)
        return False

    def close(self):
        if self.timer:
            self.kernel.CloseHandle(self.timer)
        if self.period:
            self.winmm.timeEndPeriod(1)


def playback_events(macro, anchor=None):
    """Merge keys and pixel-by-pixel mouse segments without changing event order or pauses."""
    events = macro['events']
    first = next((e for e in events if e['kind']!='key'),None)
    if first is None:
        yield from events
        return
    relative = macro.get('mouse_mode','absolute')=='relative'
    offset_x,offset_y = anchor if relative else (0,0)
    origin = {'x':0,'y':0} if relative else macro.get('mouse_start',first)

    def mouse_track():
        px,py = origin['x'],origin['y']
        previous_t = 0
        yield (0,-1,{'kind':'move','x':px+offset_x,'y':py+offset_y,'t':0})
        for index,e in enumerate(events):
            if e['kind']=='key':
                continue
            dx,dy = e['x']-px,e['y']-py
            steps = max(abs(dx),abs(dy))
            # A long silent interval is a pause, not a slow movement across the screen.
            begin = previous_t if e['t']-previous_t <= .02 else max(previous_t,e['t']-.008)
            for step in range(1,steps+1):
                x,y = px+round(dx*step/steps),py+round(dy*step/steps)
                t = begin+(e['t']-begin)*step/steps
                yield (t,index,{'kind':'move','x':x+offset_x,'y':y+offset_y,'t':t})
            if e['kind']!='move' or (not steps and e['t']>0):
                yield (e['t'],index,{**e,'x':e['x']+offset_x,'y':e['y']+offset_y})
            px,py,previous_t = e['x'],e['y'],e['t']

    keys = ((e['t'],index,e) for index,e in enumerate(events) if e['kind']=='key')
    for _,_,event in heapq.merge(keys,mouse_track(),key=lambda row:(row[0],row[1])):
        yield event


def balanced(events):
    """Drop orphan releases and close held inputs at the end of a recording."""
    result, held = [], {}
    last_mouse = None
    for event in events:
        e = dict(event)
        if e['kind'] in ('key', 'button'):
            identity = (e['kind'], e.get('vk', e.get('button')))
            if e['down']:
                held[identity] = e
            elif identity not in held:
                continue
            else:
                held.pop(identity)
        result.append(e)
        if e['kind']!='key':
            last_mouse = (e['x'],e['y'])
    end = result[-1]['t'] if result else 0
    for e in held.values():
        release = {**e, 'down': False, 't': end}
        if e['kind']=='button' and last_mouse:
            release.update(x=last_mouse[0],y=last_mouse[1])
        result.append(release)
    return result


class Engine:
    def __init__(self, accept_injected=False):
        self.user = c.WinDLL('user32', use_last_error=True)
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.user.SetWindowsHookExW.restype = c.c_void_p
        self.user.SetWindowsHookExW.argtypes = [c.c_int, c.c_void_p, c.c_void_p, w.DWORD]
        self.user.CallNextHookEx.restype = LRESULT
        self.user.CallNextHookEx.argtypes = [c.c_void_p, c.c_int, w.WPARAM, w.LPARAM]
        self.user.UnhookWindowsHookEx.argtypes = [c.c_void_p]
        self.user.SendInput.argtypes = [w.UINT, c.POINTER(INPUT), c.c_int]
        self.user.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
        self.kernel.GetModuleHandleW.restype = c.c_void_p
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.accept_injected = accept_injected
        self.mouse_observer = None
        self.mouse_bounds = None
        self.messages = queue.SimpleQueue()
        self.lock = threading.RLock()
        self.mode = 'idle'
        self.events = []
        self.pressed = {}
        self.key_origin = {}
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.hook_error = None
        self.thread = threading.Thread(target=self._hooks, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Не удалось запустить обработчик ввода')
        if self.hook_error:
            raise RuntimeError(self.hook_error)
        self.worker = None

    def _hooks(self):
        self.thread_id = self.kernel.GetCurrentThreadId()
        callback = c.WINFUNCTYPE(LRESULT, c.c_int, w.WPARAM, w.LPARAM)
        self.key_callback = callback(self._key_hook)
        self.mouse_callback = callback(self._mouse_hook)
        module = self.kernel.GetModuleHandleW(None)
        handles = [self.user.SetWindowsHookExW(13, self.key_callback, module, 0),
                   self.user.SetWindowsHookExW(14, self.mouse_callback, module, 0)]
        self.hook_handles = handles
        self.mouse_resumed = threading.Event()
        msg = w.MSG()
        self.user.PeekMessageW(c.byref(msg), None, 0, 0, 0)
        if not all(handles):
            self.hook_error = f'Ошибка Windows hooks: {c.get_last_error()}'
        self.ready.set()
        try:
            if not self.hook_error:
                while self.user.GetMessageW(c.byref(msg), None, 0, 0) > 0:
                    if msg.message==0x8001:
                        if not handles[1]:
                            handles[1] = self.user.SetWindowsHookExW(14,self.mouse_callback,module,0)
                        self.mouse_resumed.set()
                        continue
                    self.user.TranslateMessage(c.byref(msg))
                    self.user.DispatchMessageW(c.byref(msg))
        finally:
            for handle in handles:
                if handle:
                    self.user.UnhookWindowsHookEx(handle)

    def _suspend_mouse_hook(self):
        # Capture and playback are mutually exclusive. Avoid a Python hook round-trip for every
        # synthetic pixel; keyboard hooks remain active for immediate physical stop hotkeys.
        if self.mouse_observer:
            return False
        handle = self.hook_handles[1]
        if handle and self.user.UnhookWindowsHookEx(handle):
            self.hook_handles[1] = None
            return True
        return False

    def _resume_mouse_hook(self):
        self.mouse_resumed.clear()
        self.user.PostThreadMessageW(self.thread_id,0x8001,0,0)
        if not self.mouse_resumed.wait(3) or not self.hook_handles[1]:
            self.messages.put(('error','Не удалось восстановить запись мыши'))

    def _key_hook(self, code, message, pointer):
        if code >= 0:
            k = c.cast(pointer, c.POINTER(KBD)).contents
            if message in (0x100,0x104):
                self.key_origin[k.vk] = k.extra == MAGIC or (bool(k.flags & 0x10) and not self.accept_injected)
            if k.extra != MAGIC and (self.accept_injected or not k.flags & 0x10):
                down = message in (0x100, 0x104)
                with self.lock:
                    if down:
                        self.pressed.setdefault(k.vk, time.perf_counter())
                    else:
                        self.pressed.pop(k.vk, None)
                    if self.mode == 'recording' and self.capture_keyboard:
                        self._append({'kind': 'key', 'vk': k.vk, 'scan': k.scan,
                                      'extended': bool(k.flags & 1), 'down': down})
        return self.user.CallNextHookEx(None, code, message, pointer)

    def _mouse_hook(self, code, message, pointer):
        if code >= 0:
            m = c.cast(pointer, c.POINTER(MOUSEHOOK)).contents
            if self.accept_injected and self.mouse_observer and m.extra==MAGIC and message==0x200:
                self.mouse_observer(m.pt.x,m.pt.y,time.perf_counter())
            if m.extra != MAGIC and (self.accept_injected or not m.flags & 1):
                with self.lock:
                    if self.mode == 'recording' and self.capture_mouse:
                        e = {'x': m.pt.x, 'y': m.pt.y}
                        if message == 0x200:
                            e['kind'] = 'move'
                        elif message in (0x201, 0x202, 0x204, 0x205, 0x207, 0x208, 0x20B, 0x20C):
                            e.update(kind='button', button={0x201:'left',0x202:'left',0x204:'right',0x205:'right',0x207:'middle',0x208:'middle'}.get(message, 'x1' if m.data >> 16 == 1 else 'x2'), down=message in (0x201,0x204,0x207,0x20B))
                        elif message in (0x20A, 0x20E):
                            e.update(kind='scroll', delta=c.c_short(m.data >> 16).value, horizontal=message == 0x20E)
                        else:
                            return self.user.CallNextHookEx(None, code, message, pointer)
                        if not self.record_coordinates:
                            e['x'] -= self.record_origin[0]
                            e['y'] -= self.record_origin[1]
                        self._append(e)
        return self.user.CallNextHookEx(None, code, message, pointer)

    def _append(self, event):
        t = time.perf_counter() - self.started
        if len(self.events) >= 1_000_000 or t > 86400:
            if not getattr(self, 'limit_reported', False):
                self.limit_reported = True
                self.messages.put(('limit', 'Достигнут предел записи'))
            return
        self.events.append({**event, 't': t})

    def cursor_position(self):
        point = POINT()
        if not self.user.GetCursorPos(c.byref(point)):
            raise RuntimeError('Не удалось получить координаты мыши')
        return point.x,point.y

    def record(self, keyboard=True, mouse=True, mouse_coordinates=True):
        with self.lock:
            if self.mode != 'idle' or not (keyboard or mouse):
                raise ValueError('Запись сейчас недоступна')
            self.events = []
            self.limit_reported = False
            self.record_coordinates = mouse_coordinates
            self.record_origin = self.cursor_position() if mouse else (0,0)
            self.record_options = {'mouse_mode':'absolute' if mouse_coordinates else 'relative'}
            if mouse:
                x,y = self.record_origin if mouse_coordinates else (0,0)
                self.events.append({'kind':'move','x':x,'y':y,'t':0})
                if mouse_coordinates:
                    self.record_options['mouse_start'] = {'x':x,'y':y}
            self.started = time.perf_counter()
            self.capture_keyboard, self.capture_mouse = keyboard, mouse
            self.mode = 'recording'

    def finish_record(self, trigger=None):
        with self.lock:
            self.mode = 'idle'
            events = self.events
            if trigger:
                # Remove the whole stop chord, including modifiers already recorded before WM_HOTKEY.
                mod, vk = trigger
                keys = [vk]
                for bit, codes in [(2,(17,162,163)),(1,(18,164,165)),(4,(16,160,161)),(8,(91,92))]:
                    if mod & bit:
                        keys.extend(codes)
                times = [self.pressed[k] for k in keys if k in self.pressed]
                if times:
                    cutoff = min(times)-self.started
                    events = [e for e in events if e['t'] < cutoff]
            self.events = []
            return balanced(events)

    def _packet(self, e):
        if e['kind'] == 'key':
            flags = (0 if e['down'] else 2) | (1 if e.get('extended') else 0)
            packet = INPUT(type=1, ki=KEYINPUT(e['vk'], e.get('scan', 0), flags, 0, MAGIC))
        else:
            # Absolute virtual desktop coordinates support monitors left of the primary display.
            bounds = self.mouse_bounds or tuple(self.user.GetSystemMetrics(i) for i in (76,77,78,79))
            left,top,width,height = bounds
            # Use the centre of each pixel's normalized range, avoiding one-pixel rounding drift.
            x = min(65535,max(0,((e['x']-left)*65536+32768)//max(1,width)))
            y = min(65535,max(0,((e['y']-top)*65536+32768)//max(1,height)))
            flags, data = 0x8000 | 0x4000 | 0x2000 | 1, 0
            if e['kind'] == 'button':
                flag = {'left':(2,4),'right':(8,16),'middle':(32,64),'x1':(128,256),'x2':(128,256)}[e['button']]
                flags |= flag[0 if e['down'] else 1]
                data = {'x1':1,'x2':2}.get(e['button'],0)
            elif e['kind'] == 'scroll':
                flags |= 0x1000 if e.get('horizontal') else 0x800
                data = e['delta'] & 0xFFFFFFFF
            packet = INPUT(type=0, mi=MOUSEINPUT(x,y,data,flags,0,MAGIC))
        return packet

    def send(self, e):
        packet = self._packet(e)
        if self.user.SendInput(1, c.byref(packet), c.sizeof(INPUT)) != 1:
            raise RuntimeError('Windows заблокировала ввод. Проверьте права целевой программы.')

    def send_batch(self, events):
        # SendInput can block for several milliseconds per pixel in other global input hooks.
        # SetCursorPos moves the actual cursor directly, while Windows still delivers mouse
        # movement/drag messages. Clicks and wheel input continue to use SendInput.
        for e in events:
            if self.stop_event.is_set():
                break
            if not self.user.SetCursorPos(e['x'],e['y']):
                raise RuntimeError('Windows заблокировала перемещение курсора')
            if self.accept_injected and self.mouse_observer:
                x,y = self.cursor_position()
                self.mouse_observer(x,y,time.perf_counter())

    def _release_input(self, event):
        release = {**event,'down':False}
        if event['kind']=='button':
            release['x'],release['y'] = self.cursor_position()
        self.send(release)

    def play(self, macro):
        from storage import validate_macro
        validate_macro(macro)
        with self.lock:
            if self.mode != 'idle' or not macro['events']:
                raise ValueError('Выберите непустой макрос')
            self.mode = 'playing'
            self.stop_event.clear()
        self.worker = threading.Thread(target=self._play, args=(macro,), daemon=True)
        self.worker.start()

    def _play(self, macro):
        held = {}
        count = 0
        clock = None
        mouse_suspended = False
        play_started = time.perf_counter()
        previous_switch_interval = sys.getswitchinterval()
        self.playback_cycles = []
        try:
            # GUI mouse messages must not hold the GIL for the default 5 ms between pixel batches.
            sys.setswitchinterval(.001)
            clock = PlaybackClock(self.stop_event)
            mouse_suspended = self._suspend_mouse_hook()
            self.playback_mouse_hook_suspended = mouse_suspended
            if any(e['kind']!='key' for e in macro['events']):
                self.mouse_bounds = tuple(self.user.GetSystemMetrics(i) for i in (76,77,78,79))
            self.playback_setup_elapsed = time.perf_counter()-play_started
            while not self.stop_event.is_set() and (macro.get('repeats',1) == 0 or count < macro.get('repeats',1)):
                has_mouse = any(e['kind']!='key' for e in macro['events'])
                anchor = self.cursor_position() if has_mouse and macro.get('mouse_mode')=='relative' else None
                start = time.perf_counter()
                speed = macro.get('speed',1)
                iterator = iter(playback_events(macro,anchor))
                e = next(iterator,None)
                while e is not None:
                    batch = [e]
                    following = next(iterator,None)
                    if e['kind']=='move':
                        while following is not None and following['kind']=='move' and len(batch)<64 and (following['t']-batch[0]['t'])/speed <= .0005:
                            batch.append(following)
                            following = next(iterator,None)
                    if not clock.until(start+batch[-1]['t']/speed):
                        break
                    if e['kind']=='move':
                        self.send_batch(batch)
                    else:
                        self.send(e)
                    if e['kind'] in ('key','button'):
                        identity = (e['kind'], e.get('vk',e.get('button')))
                        if e['down']:
                            held[identity] = e
                        else:
                            held.pop(identity,None)
                    e = following
                for e in held.values():
                    self._release_input(e)
                held.clear()
                if getattr(self,'accept_injected',False):
                    self.playback_cycles.append(time.perf_counter()-start)
                    if len(self.playback_cycles)>128:
                        self.playback_cycles.pop(0)
                if self.stop_event.is_set():
                    break
                count += 1
                self.messages.put(('progress',count))
                if macro.get('repeats',1) != 0 and count >= macro['repeats']:
                    break
                if self.stop_event.wait(max(.01,macro.get('gap',.2))):
                    break
        except Exception as exc:
            self.messages.put(('error', str(exc)))
        finally:
            for e in held.values():
                try:
                    self._release_input(e)
                except Exception:
                    pass
            if clock:
                clock.close()
            self.playback_elapsed = time.perf_counter()-play_started
            if mouse_suspended:
                self._resume_mouse_hook()
            self.mouse_bounds = None
            sys.setswitchinterval(previous_switch_interval)
            with self.lock:
                self.mode = 'idle'
            self.messages.put(('finished',count))

    def stop(self):
        self.stop_event.set()

    def close(self):
        self.stop()
        if self.worker:
            self.worker.join(3)
        self.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        self.thread.join(3)
