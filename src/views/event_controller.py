"""
事件业务控制器
=============
管理事件的增删改查、数据转换、脚本扫描等业务逻辑。
将 UI 层的信号与 core 层的数据操作解耦。
"""

import copy
import glob
import os

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QStackedWidget, QPushButton

from core import common
from core.input_backend import MOUSE_LEFT, MOUSE_RIGHT
from core.config import events as configEvents
from core.models import Event, ClickParams, PressParams, MultiParams, ScriptParams
from views.event_edit_page import EventEditPage
from views.event_list_page import EventListPage

# 鼠标按钮内部值 → 显示名称映射
_MOUSE_BUTTON_DISPLAY = {
    MOUSE_LEFT: 'Left',
    MOUSE_RIGHT: 'Right',
}

def _displayTarget(target: str) -> str:
    """将内部按钮值转换为用户友好的显示名称"""
    return _MOUSE_BUTTON_DISPLAY.get(target, target)


class EventController:
    """事件业务控制器

    Args:
        eventListPage: 事件列表页实例
        eventEditPage: 事件编辑页实例
        contentStack: 内容区 QStackedWidget
        navBtnEvents: 事件列表导航按钮
    """

    def __init__(self, eventListPage: EventListPage, eventEditPage: EventEditPage,
                 contentStack: QStackedWidget, navBtnEvents: QPushButton):
        self._eventListPage = eventListPage
        self._eventEditPage = eventEditPage
        self._contentStack = contentStack
        self._navBtnEvents = navBtnEvents
        self._editingIndex = -1  # 当前正在编辑的事件索引（-1 表示新建）

        # 连接信号
        self._eventListPage.addEventRequested.connect(self.goToNewEvent)
        self._eventListPage.editEventRequested.connect(self.goToEditEvent)
        self._eventListPage.deleteEventRequested.connect(self.onDeleteEvent)
        self._eventListPage.copyEventRequested.connect(self.onCopyEvent)
        self._eventListPage.moveEventRequested.connect(self.onMoveEvent)
        self._eventListPage.enabledToggled.connect(self.onEnabledToggled)
        self._eventEditPage.backRequested.connect(self.goToEventList)
        self._eventEditPage.saveRequested.connect(self.onEventSaved)

    # ---- 事件数据加载与刷新 ----

    def refreshEventList(self):
        """从 config.events 加载真实数据并刷新事件列表卡片"""
        cardDataList = []
        for event in configEvents:
            cardDataList.append(self._eventToCardData(event))
        self._eventListPage.rebuildCards(cardDataList)

    @staticmethod
    def _eventToCardData(event: Event) -> tuple:
        """将 Event 对象转换为卡片显示所需的参数元组

        Returns:
            (eventType, hotkey, target, scope, extra, enabled)
        """
        eventType = event.type or "Click"
        hotkey = event.hotkey or ""
        target = _displayTarget(event.target or "")
        scope = event.scope or "*"
        enabled = event.enabled

        # 构建额外信息文本
        extraParts = []

        # 位置信息
        if event.posX != -1 or event.posY != -1:
            extraParts.append(
                QCoreApplication.translate("EventController", "Position: ({x}, {y})").format(x=event.posX, y=event.posY))

        if not extraParts and eventType in ("Click", "Press"):
            extraParts.append(QCoreApplication.translate("EventController", "Position: Current"))

        # Multi 类型的频率和次数
        if eventType == "Multi":
            interval = event.interval if event.interval is not None else 100
            clicks = event.clicks if event.clicks is not None else -1
            extraParts.append(
                QCoreApplication.translate("EventController", "Interval: {interval}ms").format(interval=interval))
            clicksText = "∞" if clicks == -1 else str(clicks)
            extraParts.append(
                QCoreApplication.translate("EventController", "Count: {clicks}").format(clicks=clicksText))

        extra = " · ".join(extraParts)
        return eventType, hotkey, target, scope, extra, enabled

    @staticmethod
    def _scanScripts() -> list[str]:
        """扫描 scripts 目录，返回脚本名称列表（不含 .py 后缀和 __builtins__）"""
        scriptsDir = common.scripts_path()
        if not os.path.isdir(scriptsDir):
            return []
        scripts = []
        for f in sorted(glob.glob(os.path.join(scriptsDir, "*.py"))):
            name = os.path.splitext(os.path.basename(f))[0]
            if name.startswith("__"):
                continue
            scripts.append(name)
        return scripts

    # ---- 页面导航 ----

    def goToEventList(self):
        """切换到事件列表页"""
        self._contentStack.setCurrentIndex(0)
        self._navBtnEvents.setChecked(True)

    def goToNewEvent(self):
        """切换到新建事件编辑页"""
        self._editingIndex = -1
        self._eventEditPage.resetForm(isEditing=False)
        self._eventEditPage.setScriptList(self._scanScripts())
        self._contentStack.setCurrentIndex(1)

    def goToEditEvent(self, index: int):
        """切换到编辑事件页，加载真实事件数据"""
        if index < 0 or index >= len(configEvents):
            return
        self._editingIndex = index
        event = configEvents[index]

        self._eventEditPage.resetForm(isEditing=True)
        self._eventEditPage.setScriptList(self._scanScripts())

        # 将 Event 对象转换为表单数据字典
        formData = {
            "type": event.type or "Click",
            "hotkey": event.hotkey or "",
            "target": event.target or MOUSE_LEFT,
            "scope": event.scope or "*",
            "trigger_on_release": event.trigger_on_release,
            "posX": event.posX,
            "posY": event.posY,
            "interval": event.interval if event.interval is not None else 100,
            "clicks": event.clicks if event.clicks is not None else -1,
        }

        if event.type == "Script":
            formData["script"] = event.target or ""

        self._eventEditPage.setFormData(formData)
        self._contentStack.setCurrentIndex(1)

    # ---- 事件保存 ----

    def onEventSaved(self, data: dict):
        """事件保存：将表单数据写入 config.events 并刷新列表"""
        eventType = data.get("type", "Click")
        hotkey = data.get("hotkey", "")

        # scope 为空时默认为 "*"
        scopeVal = data.get("scope", "").strip()
        scopeVal = scopeVal if scopeVal else "*"

        # 编辑已有事件时保留原有启用状态，新建事件默认启用
        if (0 <= self._editingIndex < len(configEvents)
                and configEvents[self._editingIndex].enabled is not None):
            enabled = configEvents[self._editingIndex].enabled
        else:
            enabled = True

        posX = data.get("posX", -1)
        posY = data.get("posY", -1)

        # 根据类型构建对应的 params
        if eventType == "Script":
            target = data.get("script", "")
            params = ScriptParams()
        elif eventType == "Multi":
            target = data.get("target", MOUSE_LEFT)
            params = MultiParams(
                position=[posX, posY],
                interval=data.get("interval", 100),
                clicks=data.get("clicks", -1),
            )
        elif eventType == "Press":
            target = data.get("target", MOUSE_LEFT)
            params = PressParams(position=[posX, posY])
        else:
            target = data.get("target", MOUSE_LEFT)
            params = ClickParams(position=[posX, posY])

        trigger_on_release = data.get("trigger_on_release", False)

        event = Event(
            type=eventType,
            hotkey=hotkey,
            target=target,
            scope=scopeVal,
            trigger_on_release=trigger_on_release,
            enabled=enabled,
            params=params,
        )

        # 保存到配置
        configEvents.update(self._editingIndex, event)

        # 刷新列表并返回
        self.refreshEventList()
        self.goToEventList()

    # ---- 事件列表操作 ----

    def onDeleteEvent(self, index: int):
        """删除事件"""
        if 0 <= index < len(configEvents):
            configEvents.pop(index)
            self.refreshEventList()

    def onCopyEvent(self, index: int):
        """复制事件（深拷贝并插入到被复制事件的下方，默认禁用）"""
        if 0 <= index < len(configEvents):
            newEvent = Event.from_dict(copy.deepcopy(configEvents[index].to_dict()))
            newEvent.enabled = False
            configEvents.insert(index + 1, newEvent)
            self.refreshEventList()

    def onMoveEvent(self, fromIndex: int, toIndex: int):
        """移动事件到目标位置"""
        if (0 <= fromIndex < len(configEvents) and
                0 <= toIndex < len(configEvents) and
                fromIndex != toIndex):
            configEvents.move(fromIndex, toIndex)
            self.refreshEventList()

    @staticmethod
    def onEnabledToggled(index: int, enabled: bool):
        """切换事件启用/禁用状态"""
        if 0 <= index < len(configEvents):
            configEvents[index].enabled = enabled
            configEvents.save()
