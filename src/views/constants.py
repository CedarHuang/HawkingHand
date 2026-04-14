"""
视图层常量
=========
存放 views 包内共享的常量定义，避免循环导入。
"""

from enum import IntEnum


class PageIndex(IntEnum):
    """contentStack 页面索引枚举"""
    EVENT_LIST = 0
    EVENT_EDIT = 1
    SCRIPT_LIST = 2
    SCRIPT_EDIT = 3
    LOG = 4
    SETTINGS = 5
