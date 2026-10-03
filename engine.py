"""Windows low-level input hooks and interruptible, timestamped playback."""
import ctypes as c
from ctypes import wintypes as w
import queue
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


def balanced(events):
    """Drop orphan releases and close held inputs at the end of a recording."""
    result, held = [], {}
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
    end = result[-1]['t'] if result else 0
    for e in held.values():
        result.append({**e, 'down': False, 't': end})
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
        msg = w.MSG()
        self.user.PeekMessageW(c.byref(msg), None, 0, 0, 0)
        if not all(handles):
            self.hook_error = f'Ошибка Windows hooks: {c.get_last_error()}'
        self.ready.set()
        try:
            if not self.hook_error:
                while self.user.GetMessageW(c.byref(msg), None, 0, 0) > 0:
                    self.user.TranslateMessage(c.byref(msg))
                    self.user.DispatchMessageW(c.byref(msg))
        finally:
            for handle in handles:
                if handle:
                    self.user.UnhookWindowsHookEx(handle)

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
            if m.extra != MAGIC and (self.accept_injected or not m.flags & 1):
                with self.lock:
                    if self.mode == 'recording' and self.capture_mouse:
                        e = {'x': m.pt.x, 'y': m.pt.y}
                        if message == 0x200:
                            # Coalesce only adjacent movement, preserving clicks and their order.
                            if self.events and self.events[-1]['kind'] == 'move' and time.perf_counter()-self.started-self.events[-1]['t'] < .008:
                                self.events.pop()
                            e['kind'] = 'move'
                        elif message in (0x201, 0x202, 0x204, 0x205, 0x207, 0x208, 0x20B, 0x20C):
                            e.update(kind='button', button={0x201:'left',0x202:'left',0x204:'right',0x205:'right',0x207:'middle',0x208:'middle'}.get(message, 'x1' if m.data >> 16 == 1 else 'x2'), down=message in (0x201,0x204,0x207,0x20B))
                        elif message in (0x20A, 0x20E):
                            e.update(kind='scroll', delta=c.c_short(m.data >> 16).value, horizontal=message == 0x20E)
                        else:
                            return self.user.CallNextHookEx(None, code, message, pointer)
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

    def record(self, keyboard=True, mouse=True):
        with self.lock:
            if self.mode != 'idle' or not (keyboard or mouse):
                raise ValueError('Запись сейчас недоступна')
            self.events = []
            self.limit_reported = False
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

    def send(self, e):
        if e['kind'] == 'key':
            flags = (0 if e['down'] else 2) | (1 if e.get('extended') else 0)
            packet = INPUT(type=1, ki=KEYINPUT(e['vk'], e.get('scan', 0), flags, 0, MAGIC))
        else:
            # Absolute virtual desktop coordinates support monitors left of the primary display.
            left, top = self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77)
            width, height = self.user.GetSystemMetrics(78), self.user.GetSystemMetrics(79)
            x = round((e['x']-left)*65535/max(1,width-1))
            y = round((e['y']-top)*65535/max(1,height-1))
            flags, data = 0x8000 | 0x4000 | 1, 0
            if e['kind'] == 'button':
                flag = {'left':(2,4),'right':(8,16),'middle':(32,64),'x1':(128,256),'x2':(128,256)}[e['button']]
                flags |= flag[0 if e['down'] else 1]
                data = {'x1':1,'x2':2}.get(e['button'],0)
            elif e['kind'] == 'scroll':
                flags |= 0x1000 if e.get('horizontal') else 0x800
                data = e['delta'] & 0xFFFFFFFF
            packet = INPUT(type=0, mi=MOUSEINPUT(x,y,data,flags,0,MAGIC))
        if self.user.SendInput(1, c.byref(packet), c.sizeof(INPUT)) != 1:
            raise RuntimeError('Windows заблокировала ввод. Проверьте права целевой программы.')

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
        try:
            while not self.stop_event.is_set() and (macro.get('repeats',1) == 0 or count < macro.get('repeats',1)):
                start = time.perf_counter()
                for e in macro['events']:
                    delay = e['t']/macro.get('speed',1) - (time.perf_counter()-start)
                    if self.stop_event.wait(max(0,delay)):
                        break
                    self.send(e)
                    if e['kind'] in ('key','button'):
                        identity = (e['kind'], e.get('vk',e.get('button')))
                        if e['down']:
                            held[identity] = e
                        else:
                            held.pop(identity,None)
                for e in held.values():
                    self.send({**e,'down':False})
                held.clear()
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
                    self.send({**e,'down':False})
                except Exception:
                    pass
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
