import threading

import mss
from mss.base import MSSBase
from mss.screenshot import ScreenShot

from core import logger

_mss_local = threading.local()


def _get_mss() -> MSSBase | None:
    """获取当前线程的 mss 实例。

    mss 的 Windows 实现使用 threading.local() 存储 GDI 句柄（srcdc、memdc 等），
    这些句柄只对创建实例的线程可见，跨线程访问会导致 '_thread._local' object has no attribute 'srcdc' 错误。
    因此必须为每个线程维护独立的 mss 实例。
    """
    sct = getattr(_mss_local, 'instance', None)
    if sct is None:
        try:
            sct = mss.mss()
            _mss_local.instance = sct
        except Exception:
            logger.app.warning('Failed to create mss instance:', exc_info=True)
    return sct


def close() -> None:
    """关闭当前线程的 mss 实例并释放 GDI 资源。"""
    sct = getattr(_mss_local, 'instance', None)
    if sct is not None:
        sct.close()
        _mss_local.instance = None


def _capture_region(x: int, y: int, width: int, height: int) -> ScreenShot | None:
    """截取屏幕指定矩形区域的像素数据。

    使用 mss 截取以屏幕绝对逻辑坐标 (x, y) 为左上角、宽为 width、高为 height 的矩形区域。
    如果请求区域超出屏幕范围，会自动裁剪到屏幕有效范围内。

    Args:
        x: 区域左上角的屏幕绝对 X 坐标。
        y: 区域左上角的屏幕绝对 Y 坐标。
        width: 区域宽度（像素）。
        height: 区域高度（像素）。

    Returns:
        截图对象，包含区域的坐标、尺寸和像素数据；
        如果截图失败、参数无效或 mss 实例不可用，则返回 None。
    """
    if width <= 0 or height <= 0:
        return None

    sct = _get_mss()
    if sct is None:
        return None

    # 获取全屏虚拟显示器区域，用于裁剪越界请求
    monitor = sct.monitors[0]
    screen_left = monitor["left"]
    screen_top = monitor["top"]
    screen_right = screen_left + monitor["width"]
    screen_bottom = screen_top + monitor["height"]

    # 裁剪到屏幕有效范围
    clamped_left = max(x, screen_left)
    clamped_top = max(y, screen_top)
    clamped_right = min(x + width, screen_right)
    clamped_bottom = min(y + height, screen_bottom)

    if clamped_right <= clamped_left or clamped_bottom <= clamped_top:
        return None

    try:
        grab_monitor = {
            "left": clamped_left,
            "top": clamped_top,
            "width": clamped_right - clamped_left,
            "height": clamped_bottom - clamped_top,
        }
        return sct.grab(grab_monitor)
    except Exception:
        logger.app.warning('Screen capture failed:', exc_info=True)
        return None


def get_pixel(x: int, y: int) -> tuple[int, int, int] | None:
    """获取屏幕上指定坐标的 RGB 颜色值。

    通过截取 1×1 像素区域获取指定屏幕绝对逻辑坐标处的颜色。
    坐标体系与输入后端中的 position()、click(x, y) 一致。

    Args:
        x: 屏幕绝对 X 坐标。
        y: 屏幕绝对 Y 坐标。

    Returns:
        像素的 RGB 值 (r, g, b)；
        如果坐标超出屏幕范围或截图失败，则返回 None。
    """
    shot = _capture_region(x, y, 1, 1)
    if shot is None:
        return None

    return shot.pixel(0, 0)
