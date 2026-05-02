"""
事件卡片组件
============
封装单张事件卡片的 UI 创建、数据填充、类型颜色标识、
启用/禁用开关、右键上下文菜单等交互逻辑。
"""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QMenu, QWidget

from core.scripts import is_builtin
from ui.generated.ui_event_card import Ui_EventCard
from views import _polishWidget


class EventCard(QFrame):
    """事件卡片控件，展示单条事件配置的摘要信息"""

    # 信号定义
    clicked = Signal()                # 卡片被双击（进入编辑）
    enabledToggled = Signal(bool)     # 启用/禁用开关切换
    editRequested = Signal()          # 右键菜单 → 编辑
    copyRequested = Signal()          # 右键菜单 → 复制
    deleteRequested = Signal()        # 右键菜单 → 删除

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_EventCard()
        self.ui.setupUi(self)

        # 连接内部信号
        self.ui.btnToggleEnabled.toggled.connect(self._onEnabledToggled)
        self.ui.btnMore.clicked.connect(self._showContextMenu)

        # 设置鼠标指针为手型，暗示可点击
        self.setCursor(Qt.PointingHandCursor)

    # ---- 数据填充 ----

    def setEventData(self, eventType: str, hotkey: str, display_name: str,
                     script_name: str = "", scope: str = "*",
                     extra: str = "", enabled: bool = True):
        """填充卡片显示数据

        Args:
            eventType: 事件类型 (Toggle/Hold)
            hotkey: 热键文本 (如 "Ctrl+F1")
            display_name: 脚本展示名
            script_name: 脚本原始名（与展示名不同时显示）
            scope: 范围摘要
            extra: 额外信息
            enabled: 是否启用
        """
        # 设置类型标签和颜色条的动态属性（驱动 QSS 颜色）
        self.ui.typeLabel.setText(eventType)
        self.ui.typeLabel.setProperty("eventType", eventType)
        _polishWidget(self.ui.typeLabel)

        self.ui.typeColorBar.setProperty("eventType", eventType)
        _polishWidget(self.ui.typeColorBar)

        # topRow: 热键 → 展示名
        self.ui.hotkeyLabel.setText(hotkey)
        self.ui.displayNameLabel.setText(display_name)
        if script_name and script_name != display_name:
            self.ui.scriptNameLabel.setText(f": {script_name}")
            self.ui.scriptNameLabel.setVisible(True)
        else:
            self.ui.scriptNameLabel.setVisible(False)
        self.ui.builtinBadgeLabel.setVisible(is_builtin(script_name))

        # bottomRow: 范围 · 额外信息
        self.ui.scopeLabel.setText(self.tr("Scope: {scope}").format(scope=scope))
        self.ui.extraInfoLabel.setText(extra)

        # 启用状态（阻断信号避免触发回调）
        self.ui.btnToggleEnabled.blockSignals(True)
        self.ui.btnToggleEnabled.setChecked(enabled)
        self.ui.btnToggleEnabled.blockSignals(False)

    # ---- 鼠标事件 ----

    def mousePressEvent(self, event):
        """记录左键按下状态（用于拖拽判断）"""
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child not in (self.ui.btnToggleEnabled, self.ui.btnMore):
                self._clickPending = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """左键释放时重置点击状态"""
        if event.button() == Qt.LeftButton:
            self._clickPending = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击卡片进入编辑"""
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child not in (self.ui.btnToggleEnabled, self.ui.btnMore):
                self.clicked.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """右键点击卡片时显示上下文菜单"""
        self._showContextMenuAt(event.globalPos())

    # ---- 内部方法 ----

    def _onEnabledToggled(self, checked: bool):
        """启用/禁用开关切换"""
        self.enabledToggled.emit(checked)

    def _showContextMenu(self):
        """更多按钮点击时显示上下文菜单"""
        btn = self.ui.btnMore
        # 在按钮下方弹出菜单
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._showContextMenuAt(pos)

    def _showContextMenuAt(self, globalPos):
        """在指定全局坐标显示上下文菜单"""
        menu = QMenu(self)
        # 移除 Qt 自动添加的 QGraphicsDropShadowEffect（偏移右下，导致圆角右下角露黑边）
        menu.aboutToShow.connect(lambda: menu.setGraphicsEffect(None))

        actionEdit = menu.addAction(self.tr("✏️  Edit"))
        actionCopy = menu.addAction(self.tr("📋  Copy"))
        menu.addSeparator()
        actionDelete = menu.addAction(self.tr("🗑️  Delete"))

        # 连接信号
        actionEdit.triggered.connect(self.editRequested.emit)
        actionCopy.triggered.connect(self.copyRequested.emit)
        actionDelete.triggered.connect(self.deleteRequested.emit)

        menu.exec(globalPos)


