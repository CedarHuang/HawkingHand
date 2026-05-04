"""
脚本卡片组件
============
封装单张脚本卡片的 UI 创建、数据填充、
右键上下文菜单等交互逻辑。
"""

import os
from datetime import datetime

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QMenu, QWidget

from core.scripts import get_display_name, get_metadata, is_builtin
from ui.generated.ui_script_card import Ui_ScriptCard
from views import _polishWidget
from views.event_edit_page import getLocalizedText


class ScriptCard(QFrame):
    """脚本卡片控件，展示单个脚本文件的摘要信息"""

    # 信号定义
    editRequested = Signal()          # 双击或菜单 → 编辑
    copyRequested = Signal()          # 右键菜单 → 复制
    deleteRequested = Signal()        # 右键菜单 → 删除

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_ScriptCard()
        self.ui.setupUi(self)

        # 连接内部信号
        self.ui.btnMore.clicked.connect(self._showContextMenu)

        # 设置鼠标指针为手型，暗示可点击
        self.setCursor(Qt.PointingHandCursor)

        # 固定使用 Script 类型颜色条
        self.ui.typeColorBar.setProperty("eventType", "Script")
        _polishWidget(self.ui.typeColorBar)

        # 脚本文件路径
        self._filePath = ""

    @property
    def filePath(self) -> str:
        """获取脚本文件的完整路径"""
        return self._filePath

    @property
    def scriptName(self) -> str:
        """获取脚本名称（不含 .py 后缀）"""
        if self._filePath:
            return os.path.splitext(os.path.basename(self._filePath))[0]
        return ""

    def setScriptInfo(self, filePath: str):
        """填充卡片显示数据

        Args:
            filePath: 脚本文件的完整路径
        """
        self._filePath = filePath
        script_name = os.path.splitext(os.path.basename(filePath))[0]
        display_name = get_display_name(script_name)
        if isinstance(display_name, dict):
            display_name = getLocalizedText(display_name, fallback=script_name)
        self.ui.displayNameLabel.setText(display_name)
        if display_name != script_name:
            self.ui.scriptNameLabel.setText(f": {script_name}")
            self.ui.scriptNameLabel.setVisible(True)
        else:
            self.ui.scriptNameLabel.setVisible(False)
        self.ui.builtinBadgeLabel.setVisible(is_builtin(script_name))
        # 脚本描述
        desc = get_metadata(script_name).description
        if desc:
            if isinstance(desc, dict):
                desc = getLocalizedText(desc)
            self.ui.scriptDescLabel.setText(desc)
            self.ui.scriptDescLabel.setVisible(bool(desc))
        else:
            self.ui.scriptDescLabel.setVisible(False)

        # 获取文件信息
        try:
            stat = os.stat(filePath)
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime)

            # 格式化文件大小
            if size < 1024:
                sizeStr = f"{size} B"
            elif size < 1024 * 1024:
                sizeStr = f"{size / 1024:.1f} KB"
            else:
                sizeStr = f"{size / (1024 * 1024):.1f} MB"

            # 格式化修改时间
            mtimeStr = mtime.strftime("%Y-%m-%d %H:%M")
            self.ui.fileInfoLabel.setText(f"{sizeStr} · Modified: {mtimeStr}")
        except OSError:
            self.ui.fileInfoLabel.setText("")

    # ---- 鼠标事件 ----

    def mouseDoubleClickEvent(self, event):
        """双击卡片进入编辑"""
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not self.ui.btnMore:
                self.editRequested.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """右键点击卡片时显示上下文菜单"""
        self._showContextMenuAt(event.globalPos())

    # ---- 内部方法 ----

    def _showContextMenu(self):
        """更多按钮点击时显示上下文菜单"""
        btn = self.ui.btnMore
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._showContextMenuAt(pos)

    def _showContextMenuAt(self, globalPos):
        """在指定全局坐标显示上下文菜单"""
        menu = QMenu(self)
        # 移除 Qt 自动添加的 QGraphicsDropShadowEffect
        menu.aboutToShow.connect(lambda: menu.setGraphicsEffect(None))

        actionEdit = menu.addAction(self.tr("✏️  Edit"))
        actionCopy = menu.addAction(self.tr("📋  Copy"))
        menu.addSeparator()
        actionDelete = menu.addAction(self.tr("🗑️  Delete"))

        actionEdit.triggered.connect(self.editRequested.emit)
        actionCopy.triggered.connect(self.copyRequested.emit)
        actionDelete.triggered.connect(self.deleteRequested.emit)

        menu.exec(globalPos)
