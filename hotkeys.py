import ctypes

MODS = {'Ctrl': 2, 'Alt': 1, 'Shift': 4, 'Win': 8}
KEYS = {**{f'F{i}': 0x6F+i for i in range(1, 25)}, **{chr(i): i for i in range(65, 91)},
        **{str(i): 48+i for i in range(10)}, 'Space': 32, 'Pause': 19, 'Home': 36, 'End': 35,
        'Insert': 45, 'Delete': 46, 'PageUp': 33, 'PageDown': 34, 'Esc': 27}


def parse_hotkey(value):
    parts = value.replace(' ', '').split('+')
    if not parts or parts[-1] not in KEYS or len(set(parts)) != len(parts):
        raise ValueError('Используйте F6 или Ctrl+Alt+R')
    modifiers = 0
    for part in parts[:-1]:
        if part not in MODS:
            raise ValueError('Модификаторы: Ctrl, Alt, Shift, Win')
        modifiers |= MODS[part]
    if not modifiers and not (parts[-1].startswith('F') or parts[-1] == 'Pause'):
        raise ValueError('Для обычной клавиши добавьте Ctrl, Alt, Shift или Win')
    return modifiers, KEYS[parts[-1]]


class Hotkeys:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        self.user.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.values = {}

    def _register(self, values):
        registered = []
        for index, value in values.items():
            mod, vk = parse_hotkey(value)
            if not self.user.RegisterHotKey(self.hwnd, index, mod | 0x4000, vk):
                for i in registered:
                    self.user.UnregisterHotKey(self.hwnd, i)
                raise ValueError(f'{value} занята другой программой')
            registered.append(index)

    def configure(self, values):
        parsed = [parse_hotkey(v) for v in values.values()]
        if len(set(parsed)) != len(parsed):
            raise ValueError('Горячие клавиши должны отличаться')
        old = dict(self.values)
        self.close()
        try:
            self._register(values)
            self.values = dict(values)
        except ValueError:
            self._register(old)
            self.values = old
            raise

    def close(self):
        for index in self.values:
            self.user.UnregisterHotKey(self.hwnd, index)
        self.values = {}
