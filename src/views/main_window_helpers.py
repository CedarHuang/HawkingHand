"""
主窗口辅助组件
=============
标题栏拖拽、双击事件过滤器、导航栏折叠/展开控制器。
"""

from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QPoint,
    QObject, QEvent, QCoreApplication,
)
from PySide6.QtWidgets import QWidget

# 导航栏尺寸常量
NAV_COLLAPSED_WIDTH = 48
NAV_EXPANDED_WIDTH = 160


# ============================================================
# 标题栏拖拽支持
# ============================================================

class TitleBarDragHelper(QObject):
    """为无边框窗口的标题栏提供拖拽移动和双击最大化支持"""

    def __init__(self, titleBar: QWidget, window: QWidget, toggleMaximize=None):
        super().__init__(titleBar)
        self._titleBar = titleBar
        self._window = window
        self._toggleMaximize = toggleMaximize
        self._dragging = False
        self._dragStartPos = QPoint()
        titleBar.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is not self._titleBar:
            return False

        if event.type() == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.LeftButton and self._toggleMaximize:
                self._toggleMaximize()
                return True

        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._dragging = True
                self._dragStartPos = (
                    event.globalPosition().toPoint()
                    - self._window.frameGeometry().topLeft()
                )
                return True

        elif event.type() == QEvent.Type.MouseMove:
            if self._dragging and event.buttons() & Qt.LeftButton:
                if self._window.isMaximized():
                    # 最大化状态下拖拽：先还原窗口，再开始拖拽
                    globalPos = event.globalPosition().toPoint()
                    # 计算鼠标在标题栏中的相对水平位置比例
                    oldWidth = self._window.width()
                    relativeX = self._dragStartPos.x()
                    # 在 showNormal() 之前获取还原后的宽度（避免异步时序问题）
                    newWidth = self._window.normalGeometry().width()
                    self._window.showNormal()
                    # 按比例映射到还原后的窗口宽度
                    newX = int(relativeX * newWidth / oldWidth)
                    self._dragStartPos = QPoint(newX, self._dragStartPos.y())
                    self._window.move(globalPos - self._dragStartPos)
                else:
                    self._window.move(
                        event.globalPosition().toPoint() - self._dragStartPos
                    )
                return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._dragging = False
            return True

        return False


# ============================================================
# 双击事件过滤器
# ============================================================

class DoubleClickFilter(QObject):
    """通用双击事件过滤器"""

    def __init__(self, target: QWidget, callback):
        super().__init__(target)
        self._target = target
        self._callback = callback
        target.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._target and event.type() == QEvent.Type.MouseButtonDblClick:
            self._callback()
            return True
        return False


# ============================================================
# 导航栏折叠/展开控制器
# ============================================================

class NavBarController:
    """控制导航栏的折叠/展开动画和按钮文字切换"""

    # 导航项配置：(objectName, 图标, 展开文字翻译键)
    # NOTE: QCoreApplication.translate 调用让 lupdate 能正确提取翻译条目到 MainWindow 上下文
    # 模块加载时翻译器尚未安装，translate 返回原始英文字符串，作为翻译键使用
    NAV_ITEMS = {
        "navBtnEvents": ("📋", QCoreApplication.translate("MainWindow", "Events")),
        "navBtnLogs": ("📜", QCoreApplication.translate("MainWindow", "Logs")),
        "navBtnSettings": ("⚙", QCoreApplication.translate("MainWindow", "Settings")),
        "navBtnToggle": ("☰", QCoreApplication.translate("MainWindow", "Collapse")),
    }

    def __init__(self, navBar: QWidget, mainWin: QWidget):
        self._navBar = navBar
        self._mainWin = mainWin
        self._expanded = False

        # 创建宽度动画（同时动画 min/max width）
        self._animMax = QPropertyAnimation(navBar, b"maximumWidth")
        self._animMax.setDuration(200)
        self._animMax.setEasingCurve(QEasingCurve.InOutCubic)

        self._animMin = QPropertyAnimation(navBar, b"minimumWidth")
        self._animMin.setDuration(200)
        self._animMin.setEasingCurve(QEasingCurve.InOutCubic)

        # 动画结束后更新按钮文字
        self._animMax.finished.connect(self._onAnimationFinished)

        # 缓存按钮引用
        self._buttons = {}
        for name in self.NAV_ITEMS:
            btn = mainWin.findChild(QWidget, name)
            if btn:
                self._buttons[name] = btn

        # 初始化为折叠态
        self._updateButtonTexts()

    @property
    def isExpanded(self) -> bool:
        return self._expanded

    def toggle(self):
        """切换折叠/展开状态"""
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        """展开导航栏"""
        self._animMax.stop()
        self._animMin.stop()

        currentWidth = self._navBar.width()
        self._animMax.setStartValue(currentWidth)
        self._animMax.setEndValue(NAV_EXPANDED_WIDTH)
        self._animMin.setStartValue(currentWidth)
        self._animMin.setEndValue(NAV_EXPANDED_WIDTH)

        # 展开前先更新文字
        self._expanded = True
        self._updateButtonTexts()

        self._animMax.start()
        self._animMin.start()

    def _collapse(self):
        """折叠导航栏"""
        self._animMax.stop()
        self._animMin.stop()

        currentWidth = self._navBar.width()
        self._animMax.setStartValue(currentWidth)
        self._animMax.setEndValue(NAV_COLLAPSED_WIDTH)
        self._animMin.setStartValue(currentWidth)
        self._animMin.setEndValue(NAV_COLLAPSED_WIDTH)

        self._expanded = False

        self._animMax.start()
        self._animMin.start()

    def _onAnimationFinished(self):
        """动画结束后更新按钮文字（折叠时在动画结束后切换为仅图标）"""
        if not self._expanded:
            self._updateButtonTexts()

    def _updateButtonTexts(self):
        """根据当前状态更新所有导航按钮的文字"""
        _tr = QCoreApplication.translate
        for name, (icon, textKey) in self.NAV_ITEMS.items():
            btn = self._buttons.get(name)
            if btn:
                translated = _tr("MainWindow", textKey)
                btn.setText(f"{icon} {translated}" if self._expanded else icon)
