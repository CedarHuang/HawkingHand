import dataclasses
from dataclasses import dataclass, field, fields


@dataclass
class ClickParams:
    position: list = field(default_factory=lambda: [-1, -1])

@dataclass
class PressParams:
    position: list = field(default_factory=lambda: [-1, -1])

@dataclass
class MultiParams:
    position: list = field(default_factory=lambda: [-1, -1])
    interval: int = 100
    clicks: int = -1

@dataclass
class ScriptParams:
    pass


PARAMS_CLASS = {
    'Click': ClickParams,
    'Press': PressParams,
    'Multi': MultiParams,
    'Script': ScriptParams,
}


@dataclass
class Event:
    type: str = 'Click'
    hotkey: str = ''
    target: str = 'mouse_left'
    scope: str = '*'
    trigger_on_release: bool = False
    enabled: bool = True
    params: ClickParams | PressParams | MultiParams | ScriptParams = field(default_factory=ClickParams)


    @property
    def position(self) -> list:
        return getattr(self.params, 'position', [-1, -1])

    @property
    def posX(self) -> int:
        pos = self.position
        if isinstance(pos, (list, tuple)) and len(pos) >= 1:
            return pos[0]
        return -1

    @property
    def posY(self) -> int:
        pos = self.position
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            return pos[1]
        return -1

    @property
    def interval(self) -> int | None:
        return getattr(self.params, 'interval', None)

    @property
    def clicks(self) -> int | None:
        return getattr(self.params, 'clicks', None)


    @classmethod
    def from_dict(cls, data: dict) -> 'Event':
        event_type = data.get('type', 'Click')
        params_cls = PARAMS_CLASS.get(event_type, ClickParams)

        params_data = data.get('params', {})
        known_params = {f.name for f in fields(params_cls)}
        params = params_cls(**{k: v for k, v in params_data.items() if k in known_params})

        event_fields = {f.name for f in fields(cls)} - {'params'}
        base = {k: v for k, v in data.items() if k in event_fields}
        return cls(**base, params=params)

    def to_dict(self) -> dict:
        result = {}
        for f in fields(self):
            if f.name == 'params':
                result['params'] = dataclasses.asdict(self.params)
            else:
                result[f.name] = getattr(self, f.name)
        return result


@dataclass
class Settings:
    enable_tray: bool = False
    startup: bool = False
    startup_as_admin: bool = False
    theme: str = 'system'
    language: str = 'system'

    @classmethod
    def from_dict(cls, data: dict) -> 'Settings':
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}
