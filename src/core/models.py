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


class ParamRef:
    """参数引用代理对象。

    提取沙箱中 params() 返回此类型，携带参数名和默认值。
    所有运算自动解包为默认值，switch() 通过 ._name 获取参数标识。
    """

    __slots__ = ('_name', '_value')

    def __init__(self, name, value):
        self._name = name
        self._value = value

    @property
    def name(self):
        return self._name

    def __eq__(self, o):  return self._value == o
    def __ne__(self, o):  return self._value != o
    def __lt__(self, o):  return self._value < o
    def __gt__(self, o):  return self._value > o
    def __hash__(self):   return hash(self._value)

    def __bool__(self):   return bool(self._value)
    def __int__(self):    return int(self._value)
    def __float__(self):  return float(self._value)
    def __str__(self):    return str(self._value)

    def __iter__(self):   return iter(self._value)
    def __getitem__(self, i): return self._value[i]

    def __repr__(self):   return f'ParamRef({self._name!r}, {self._value!r})'


@dataclass
class ParamDef:
    name: str
    type: ParamType
    default: int | float | str | bool | list | tuple
    label: str | dict[str, str] | None = None
    description: str | dict[str, str] | None = None
    options: list | dict | None = None
    switch_cases: dict | None = None


@dataclass
class ScriptParams:
    script_args: dict[str, int | float | str | bool | list] = field(default_factory=dict)


@dataclass
class Event:
    type: str = 'Toggle'
    hotkey: str = ''
    script: str = '__click__'
    scope: str = '*'
    enabled: bool = True
    params: ScriptParams = field(default_factory=ScriptParams)

    @classmethod
    def from_dict(cls, data: dict) -> 'Event':
        event_fields = {f.name for f in fields(cls)} - {'params'}
        base = {k: v for k, v in data.items() if k in event_fields}
        params_data = data.get('params', {})
        params = ScriptParams(script_args=params_data.get('script_args', {}))
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
