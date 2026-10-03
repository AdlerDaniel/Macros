import json
import os
import uuid
from pathlib import Path

DEFAULTS = {'record_hotkey': 'F6', 'play_hotkey': 'F8', 'stop_hotkey': 'F10', 'auto_update': True}


def validate_macro(m):
    if not isinstance(m, dict) or not isinstance(m.get('name'), str) or not m['name'].strip():
        raise ValueError('У макроса должно быть имя')
    events = m.get('events')
    if not isinstance(events, list) or len(events) > 1_000_000:
        raise ValueError('Неверный список событий')
    previous = 0
    for e in events:
        if not isinstance(e, dict) or e.get('kind') not in ('key', 'move', 'button', 'scroll'):
            raise ValueError('Неизвестное событие')
        t = e.get('t')
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not previous <= t <= 86400:
            raise ValueError('Неверное время события')
        previous = t
        kind = e['kind']
        if kind == 'key' and (type(e.get('vk')) is not int or not 1 <= e['vk'] <= 255 or type(e.get('down')) is not bool):
            raise ValueError('Неверная клавиша')
        if kind == 'key' and (type(e.get('scan',0)) is not int or not 0 <= e.get('scan',0) <= 65535 or type(e.get('extended',False)) is not bool):
            raise ValueError('Неверный код клавиши')
        if kind in ('move', 'button', 'scroll') and any(type(e.get(k)) is not int or abs(e[k]) > 1_000_000 for k in ('x', 'y')):
            raise ValueError('Неверные координаты')
        if kind == 'button' and (e.get('button') not in ('left', 'right', 'middle', 'x1', 'x2') or type(e.get('down')) is not bool):
            raise ValueError('Неверная кнопка')
        if kind == 'scroll' and (type(e.get('delta')) is not int or abs(e['delta']) > 100000 or type(e.get('horizontal', False)) is not bool):
            raise ValueError('Неверная прокрутка')
    if type(m.get('repeats', 1)) is not int or not 0 <= m.get('repeats', 1) <= 1_000_000:
        raise ValueError('Неверное число повторов')
    if not isinstance(m.get('speed', 1), (int, float)) or not .1 <= m.get('speed', 1) <= 10:
        raise ValueError('Неверная скорость')
    if not isinstance(m.get('gap', .2), (int, float)) or not 0 <= m.get('gap', .2) <= 3600:
        raise ValueError('Неверная пауза')
    return m


class Store:
    def __init__(self, root=None):
        self.root = Path(root or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Macros')
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'library.json'
        self.data = {'settings': dict(DEFAULTS), 'macros': []}
        self.error = ''
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text('utf-8'))
                for m in data['macros']:
                    validate_macro(m)
                self.data = data
                self.data['settings'] = {**DEFAULTS, **data.get('settings', {})}
            except (ValueError, TypeError, KeyError, OSError) as exc:
                backup = self.path.with_name(f'library-damaged-{uuid.uuid4().hex[:8]}.json')
                self.path.rename(backup)
                self.error = f'Повреждённая библиотека сохранена: {backup.name}. {exc}'

    def save(self):
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    def add(self, name, events, **options):
        m = {'id': uuid.uuid4().hex, 'name': name, 'events': events, 'repeats': 1, 'speed': 1, 'gap': .2, **options}
        validate_macro(m)
        self.data['macros'].append(m)
        self.save()
        return m
