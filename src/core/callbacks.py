from enum import Enum, auto

class CallbackEvent(Enum):
    """回调事件类型枚举"""

    EVENTS_CHANGED = auto()
    """事件配置变更"""

    SCRIPTS_CHANGED = auto()
    """脚本文件修改"""

    TRAY_UPDATE = auto()
    """托盘更新事件"""

    WAKEUP = auto()
    """窗口唤醒事件"""

class CallbackManager:
    def __init__(self):
        self._callbacks: dict[CallbackEvent, callable] = {}

    def on(self, event: CallbackEvent):
        def decorator(func):
            self._callbacks[event] = func
            return func
        return decorator

    def register(self, event: CallbackEvent, func: callable):
        self._callbacks[event] = func

    def trigger(self, event: CallbackEvent, *args, **kwargs):
        callback = self._callbacks.get(event)
        if callback:
            return callback(*args, **kwargs)

callbacks = CallbackManager()
