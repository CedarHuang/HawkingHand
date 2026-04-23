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


def _read_pixel_rgb(shot: ScreenShot, x: int, y: int) -> tuple[int, int, int]:
    """直接从截图的 raw 数据中读取指定坐标的 RGB 值。

    绕过 ScreenShot.pixel()，避免触发 pixels 属性的全量构建。
    mss 的 raw 数据为 BGRA 格式，每个像素占 4 字节。

    Args:
        shot: mss 截图对象。
        x: 像素的 X 坐标（相对于截图左上角）。
        y: 像素的 Y 坐标（相对于截图左上角）。

    Returns:
        像素的 RGB 值 (r, g, b)。
    """
    offset = (y * shot.width + x) * 4
    return shot.raw[offset + 2], shot.raw[offset + 1], shot.raw[offset]


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

    return _read_pixel_rgb(shot, 0, 0)


def get_pixels(coordinates: list[tuple[int, int]]) -> list[tuple[int, int, int] | None]:
    """批量获取屏幕上多个坐标的 RGB 颜色值。

    通过一次截图覆盖所有目标坐标的最小包围矩形，然后从中读取各像素值，
    比逐个调用 get_pixel() 更高效。

    Args:
        coordinates: 坐标列表，每个元素为 (x, y) 的屏幕绝对逻辑坐标。

    Returns:
        与输入坐标一一对应的 RGB 值列表。
        如果截图失败，所有元素为 None；
        如果某个坐标超出截图范围，对应元素为 None。
    """
    if not coordinates:
        return []

    xs, ys = zip(*coordinates)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    shot = _capture_region(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    if shot is None:
        return [None] * len(coordinates)

    result = []
    for x, y in coordinates:
        rel_x = x - min_x
        rel_y = y - min_y
        if 0 <= rel_x < shot.width and 0 <= rel_y < shot.height:
            result.append(_read_pixel_rgb(shot, rel_x, rel_y))
        else:
            result.append(None)

    return result
