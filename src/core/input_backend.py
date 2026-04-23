import keyboard
import win32api
import win32con
from contextlib import contextmanager

from core import _DocumentedStr, _DocumentedTuple


MOUSE_LEFT: str = _DocumentedStr('mouse_left',
    """鼠标左键的标准值。

    用于 click()、down()、up() 等函数的 button 参数，表示鼠标左键。
    """
)

MOUSE_RIGHT: str = _DocumentedStr('mouse_right',
    """鼠标右键的标准值。

    用于 click()、down()、up() 等函数的 button 参数，表示鼠标右键。
    """
)

_MOUSE_FLAGS: dict[str, tuple[int, int]] = {
    MOUSE_LEFT: (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
    MOUSE_RIGHT: (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
}
_DOWN: int = 0
_UP: int = 1

MOUSE_BUTTON: tuple[str, ...] = _DocumentedTuple(tuple(_MOUSE_FLAGS),
    """所有鼠标按键的集合。

    用于快速判断一个值是否为鼠标按键。
    """
)

def _resolve_button(button: str) -> tuple[bool, str]:
    button = button.lower()
    if button in _MOUSE_FLAGS:
        return True, button
    return False, button

@contextmanager
def _safe_keyboard():
    """安全执行 keyboard 操作的上下文管理器。

    keyboard 库的 send() 在执行时会将 is_replaying 设为 True 以避免重放事件，
    但如果 parse_hotkey() 抛出异常，is_replaying 将永远不会被重置为 False，
    导致所有后续键盘事件被静默忽略（热键全部失效）。
    此上下文管理器确保异常发生时重置该状态。
    """
    try:
        yield
    except ValueError:
        keyboard._listener.is_replaying = False
        raise

def position(x: int = -1, y: int = -1) -> tuple[int, int]:
    """获取鼠标的当前位置。

    如果提供了有效的 x 和 y 坐标（大于等于 0），则直接返回这些坐标。
    如果 x 与 y 为 -1，则获取当前鼠标位置的对应坐标。

    Args:
        x: X坐标。如果为 -1，则使用当前鼠标的X坐标。默认为 -1。
        y: Y坐标。如果为 -1，则使用当前鼠标的Y坐标。默认为 -1。

    Returns:
        鼠标的坐标 (x, y)。
    """
    cx, cy = win32api.GetCursorPos()
    return (x if x >= 0 else cx, y if y >= 0 else cy)

def _mouse_event(button: str, action: int, x: int, y: int) -> None:
    if x >= 0 or y >= 0:
        win32api.SetCursorPos(position(x, y))
    win32api.mouse_event(_MOUSE_FLAGS[button][action], 0, 0, 0, 0)

def click(button: str, x: int = -1, y: int = -1) -> None:
    """模拟鼠标或键盘按键的点击操作。

    Args:
        button: 要点击的按钮或按键的名称。
        x: 鼠标点击的X坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标X坐标）。
        y: 鼠标点击的Y坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标Y坐标）。
    """
    is_mouse, actual = _resolve_button(button)
    if is_mouse:
        _mouse_event(actual, _DOWN, x, y)
        _mouse_event(actual, _UP, x, y)
    else:
        with _safe_keyboard():
            keyboard.press_and_release(actual)

def down(button: str, x: int = -1, y: int = -1) -> None:
    """模拟鼠标或键盘按键的按下操作（不释放）。

    Args:
        button: 要按下的按钮或按键的名称。
        x: 鼠标按下的X坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标X坐标）。
        y: 鼠标按下的Y坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标Y坐标）。
    """
    is_mouse, actual = _resolve_button(button)
    if is_mouse:
        _mouse_event(actual, _DOWN, x, y)
    else:
        with _safe_keyboard():
            keyboard.press(actual)
def up(button: str, x: int = -1, y: int = -1) -> None:
    """模拟鼠标或键盘按键的释放操作。

    Args:
        button: 要释放的按钮或按键的名称。
            如果 'button' 在 MOUSE_BUTTON 中，则执行鼠标释放。
            否则，视为键盘按键。
        x: 鼠标释放的X坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标X坐标）。
        y: 鼠标释放的Y坐标。仅当 'button' 为鼠标按钮时有效。
            默认为 -1（表示当前鼠标Y坐标）。
    """
    is_mouse, actual = _resolve_button(button)
    if is_mouse:
        _mouse_event(actual, _UP, x, y)
    else:
        with _safe_keyboard():
            keyboard.release(actual)

def move(x_offset: int, y_offset: int) -> None:
    """相对当前鼠标位置移动鼠标。

    Args:
        x_offset: X轴方向的偏移量。正值向右，负值向左。
        y_offset: Y轴方向的偏移量。正值向下，负值向上。
    """
    cx, cy = win32api.GetCursorPos()
    win32api.SetCursorPos((cx + x_offset, cy + y_offset))

def move_to(x: int, y: int) -> None:
    """将鼠标移动到屏幕上的指定绝对坐标。

    Args:
        x: 目标X坐标。
        y: 目标Y坐标。
    """
    win32api.SetCursorPos((x, y))

def is_caps_lock_on() -> int:
    """检查 Caps Lock 是否开启。

    Returns:
        如果 Caps Lock 开启则返回 1，否则返回 0。
    """
    return win32api.GetKeyState(win32con.VK_CAPITAL)