"""
主窗口
======
应用主窗口框架，包含无边框窗口、标题栏拖拽、
左侧可折叠导航栏、右侧内容区页面切换等 UI 交互。
"""

from PySide6.QtCore import Qt, QSize, Signal, QEvent
from PySide6.QtGui import QIcon, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QButtonGroup, QApplication,
)

from core import common
from core.callbacks import callbacks, CallbackEvent
from core.config import settings as configSettings
from ui.generated.ui_main_window import Ui_MainWindow
from views import _polishWidget
from views.appearance import applyTheme, resolveTheme
from views.event_controller import EventController
from views.event_edit_page import EventEditPage
from views.event_list_page import EventListPage
from views.main_window_helpers import (
    TitleBarDragHelper, DoubleClickFilter, NavBarController,
)
from views.settings_page import SettingsPage
from views.settings_controller import SettingsController
from views.tray import TrayManager


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QWidget):
    """应用主窗口"""

    # 跨线程信号（子线程 emit → 主线程槽函数）
    _wakeupSignal = Signal()
    _trayUpdateSignal = Signal()

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        # ---- 设置 UI ----
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle("HawkingHand")
        self.setWindowIcon(QIcon(":/icons/app.svg"))

        # ---- 标题栏应用图标 ----
        pixmap = QIcon(":/icons/app.svg").pixmap(QSize(20, 20))
        self.ui.appIcon.setPixmap(pixmap)
        self.ui.appIcon.setAlignment(Qt.AlignCenter)

        # ---- 无边框窗口 ----
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # ---- bodyFrame 圆角裁剪（防止子控件突出） ----
        self.ui.bodyFrame.installEventFilter(self)

        # ---- 最大化样式状态同步目标控件 ----
        self._maximizedStyleWidgets = (
            self.ui.windowFrame,
            self.ui.titleBar,
            self.ui.btnClose,
            self.ui.bodyFrame,
            self.ui.navBar,
        )
        self._applyWindowStateStyle(False)

        # ---- 标题栏拖拽 & 双击最大化 ----
        self._dragHelper = TitleBarDragHelper(self.ui.titleBar, self, self._toggleMaximize)

        # ---- 创建子页面 ----
        self.eventListPage = EventListPage()
        self.eventEditPage = EventEditPage()
        self.settingsPage = SettingsPage()

        # ---- 将子页面嵌入 contentStack ----
        self._embedPage(0, self.eventListPage)
        self._embedPage(1, self.eventEditPage)
        self._embedPage(2, self.settingsPage)

        # ---- 导航栏控制 ----
        self._navController = NavBarController(self.ui.navBar, self)

        # ---- 导航按钮互斥 + 页面切换 ----
        self._navGroup = QButtonGroup(self)
        self._navGroup.setExclusive(True)
        self._navGroup.addButton(self.ui.navBtnEvents, 0)
        self._navGroup.addButton(self.ui.navBtnSettings, 2)
        self._navGroup.idClicked.connect(self._onNavClicked)

        # ---- 导航栏展开/折叠按钮 ----
        self.ui.navBtnToggle.clicked.connect(self._navController.toggle)
        self.ui.navBtnToggle.setToolTip(self.tr("Toggle navigation bar"))

        # ---- 标题栏按钮 ----
        self.ui.btnMinimize.clicked.connect(self.showMinimized)
        self.ui.btnMaximize.clicked.connect(self._toggleMaximize)
        self.ui.btnClose.clicked.connect(self.close)

        # ---- 业务控制器 ----
        self._eventCtrl = EventController(
            self.eventListPage, self.eventEditPage,
            self.ui.contentStack, self.ui.navBtnEvents,
        )
        self._settingsCtrl = SettingsController(self.settingsPage)

        # ---- 主题快速切换（仅开发环境：双击版本号） ----
        if not common.is_frozen():
            self._currentTheme = resolveTheme(configSettings.theme)
            self._themeFilter = DoubleClickFilter(
                self.ui.versionLabel, self._toggleTheme
            )

        # ---- 跨线程信号 ----
        self._wakeupSignal.connect(self._onWakeup)
        self._trayUpdateSignal.connect(self._onTrayUpdate)

        # ---- 系统托盘 ----
        self._trayManager = TrayManager(self, self._onWakeup)

        # ---- 初始显示事件列表页 ----
        self.ui.contentStack.setCurrentIndex(0)

    # ---- 公共方法 ----

    def setVersion(self, version: str):
        """设置版本号显示

        Args:
            version: 版本号文本 (如 "v0.7.2")
        """
        self.ui.versionLabel.setText(version)
        self.settingsPage.setVersionText(version)

    def registerCallbacks(self):
        """注册 core 层回调"""
        callbacks.register(CallbackEvent.WAKEUP, self._wakeupSignal.emit)
        callbacks.register(CallbackEvent.TRAY_UPDATE, self._trayUpdateSignal.emit)

    def initSettings(self):
        """初始化设置页"""
        self._settingsCtrl.initSettings()

    def refreshEventList(self):
        """刷新事件列表"""
        self._eventCtrl.refreshEventList()

    def initTray(self):
        """根据配置初始化托盘显示状态"""
        self._trayManager.init()

    def cleanupTray(self):
        """退出时清理托盘图标"""
        self._trayManager.cleanup()

    # ---- 页面导航 ----

    def _onNavClicked(self, btnId: int):
        """导航按钮点击，切换到对应页面"""
        self.ui.contentStack.setCurrentIndex(btnId)

    # ---- 最大化 / 还原 ----

    def _toggleMaximize(self):
        """切换最大化 / 还原状态"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _updateMaximizeButton(self):
        """根据窗口状态更新最大化按钮图标"""
        if self.isMaximized():
            self.ui.btnMaximize.setText("❐")  # 还原图标
        else:
            self.ui.btnMaximize.setText("☐")  # 最大化图标

    def _updateWindowStyle(self):
        """最大化时去掉圆角和边距，还原时恢复"""
        maximized = self.isMaximized()
        if maximized:
            self.ui.rootLayout.setContentsMargins(0, 0, 0, 0)
        else:
            self.ui.rootLayout.setContentsMargins(5, 5, 5, 5)

        self._applyWindowStateStyle(maximized)

        # 更新 bodyFrame 的圆角裁剪 mask
        self._updateBodyMask()

    def _applyWindowStateStyle(self, maximized: bool):
        """同步窗口状态相关的动态属性并刷新对应控件样式"""
        for widget in self._maximizedStyleWidgets:
            widget.setProperty("windowMaximized", maximized)
            _polishWidget(widget)

    def changeEvent(self, event):
        """窗口状态变化时同步更新按钮和样式"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._updateMaximizeButton()
            self._updateWindowStyle()

    # ---- 窗口唤醒 ----

    def _onWakeup(self):
        """唤醒窗口：显示、置顶并激活（由跨线程信号触发，在主线程执行）"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---- 系统托盘 ----

    def _onTrayUpdate(self):
        """TRAY_UPDATE 回调：更新托盘显示状态"""
        self._trayManager.update()

    def closeEvent(self, event):
        """重写关闭事件：托盘启用时最小化到托盘，否则正常关闭"""
        if self._trayManager.shouldMinimizeToTray():
            self.hide()
            event.ignore()
        else:
            event.accept()

    # ---- 主题快速切换（仅开发环境） ----

    def _toggleTheme(self):
        """切换深色/浅色主题"""
        app = QApplication.instance()
        if app:
            self._currentTheme = "light" if self._currentTheme == "dark" else "dark"
            applyTheme(app, self._currentTheme)

    # ---- 内部方法 ----

    def _updateBodyMask(self):
        """更新 bodyFrame 的圆角裁剪 mask（最大化时不裁剪）"""
        body = self.ui.bodyFrame
        if self.isMaximized():
            body.clearMask()
        else:
            r = 10
            rect = body.rect()
            path = QPainterPath()
            path.addRoundedRect(0, -r, rect.width() - 1, rect.height() + r - 1, r, r)
            body.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self, obj, event):
        """bodyFrame resize 时更新圆角裁剪区域"""
        if obj is self.ui.bodyFrame and event.type() == QEvent.Type.Resize:
            self._updateBodyMask()
        return super().eventFilter(obj, event)

    def _embedPage(self, index: int, page: QWidget):
        """将子页面嵌入 contentStack 的指定索引页"""
        container = self.ui.contentStack.widget(index)
        if container.layout() is None:
            container.setLayout(QVBoxLayout())
        container.layout().setContentsMargins(0, 0, 0, 0)
        container.layout().addWidget(page)
