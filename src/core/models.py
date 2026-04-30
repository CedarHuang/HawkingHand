import dataclasses
from dataclasses import dataclass, field, fields
from enum import StrEnum


class ParamType(StrEnum):
    BOOL = 'bool'
    CHOICE = 'choice'
    COORD = 'coord'
    FLOAT = 'float'
    HOTKEY = 'hotkey'
    INT = 'int'
    STR = 'str'

    @classmethod
    def infer_from(cls, default, options=None) -> 'ParamType':
        """从 default 值推断参数类型。

        Args:
            default: 参数默认值。
            options: 选项列表或字典，不为 None 时返回 CHOICE。

        Returns:
            ParamType: 推断出的参数类型。
        """
        if options is not None:
            return cls.CHOICE
        # 注意：bool 是 int 的子类，必须先检查 bool
        if isinstance(default, bool):
            return cls.BOOL
        if isinstance(default, int):
            return cls.INT
        if isinstance(default, float):
            return cls.FLOAT
        if isinstance(default, str):
            return cls.STR
        if isinstance(default, (list, tuple)):
            return cls.COORD
        return cls.STR


@dataclass
class ParamDef:
    name: str
    type: ParamType
    default: int | float | str | bool | list | tuple
    label: str | dict[str, str] | None = None
    description: str | dict[str, str] | None = None
    options: list | dict | None = None


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
    script_args: dict[str, int | float | str | bool | list] = field(default_factory=dict)


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
