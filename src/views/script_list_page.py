"""
脚本列表页
==========
展示脚本目录中所有 .py 脚本文件的卡片列表，
支持新建、打开目录、编辑、复制、删除等操作。
"""

import os

from PySide6.QtCore import Signal, QFileSystemWatcher
from PySide6.QtWidgets import QWidget, QSpacerItem, QSizePolicy

from core import common
from ui.generated.ui_script_list_page import Ui_ScriptListPage
from views.script_card import ScriptCard


class ScriptListPage(QWidget):
    """脚本列表页面"""

    # 信号定义
    editScriptRequested = Signal(str)     # 请求编辑脚本（参数：文件路径）
    copyScriptRequested = Signal(str)     # 请求复制脚本（参数：文件路径）
    deleteScriptRequested = Signal(str)   # 请求删除脚本（参数：文件路径）
    newScriptRequested = Signal()         # 请求新建脚本
    openDirRequested = Signal()           # 请求打开脚本目录

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_ScriptListPage()
        self.ui.setupUi(self)

        # 连接按钮信号
        self.ui.btnOpenScriptsDirSmall.clicked.connect(self.openDirRequested.emit)
        self.ui.btnNewScript.clicked.connect(self.newScriptRequested.emit)
        self.ui.btnAddFirstScript.clicked.connect(self.newScriptRequested.emit)

        # 卡片列表
        self._cards: list[ScriptCard] = []
        # 底部弹性空间
        self._bottomSpacer: QSpacerItem | None = None

        # ---- 文件系统监听 ----
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._onDirChanged)
        scriptsDir = common.scripts_path()
        if os.path.isdir(scriptsDir):
            self._watcher.addPath(scriptsDir)

    def refreshList(self):
        """扫描脚本目录并刷新卡片列表"""
        # 清除现有卡片
        self._clearCards()

        scriptsDir = common.scripts_path()
        scriptFiles = []

        if os.path.isdir(scriptsDir):
            files = [f for f in os.listdir(scriptsDir) if f.endswith(".py")]
            # _ 开头脚本排最前，其余按字母序
            files.sort(key=lambda n: (not n.startswith("_"), n.lower()))
            for f in files:
                scriptFiles.append(os.path.join(scriptsDir, f))

        # 切换空状态/列表显示
        hasScripts = len(scriptFiles) > 0
        self.ui.scrollArea.setVisible(hasScripts)
        self.ui.emptyState.setVisible(not hasScripts)

        if not hasScripts:
            return

        # 创建卡片
        layout = self.ui.scriptListLayout
        for filePath in scriptFiles:
            card = ScriptCard()
            card.setScriptInfo(filePath)

            # 连接卡片信号
            card.editRequested.connect(lambda fp=filePath: self.editScriptRequested.emit(fp))
            card.copyRequested.connect(lambda fp=filePath: self.copyScriptRequested.emit(fp))
            card.deleteRequested.connect(lambda fp=filePath: self.deleteScriptRequested.emit(fp))

            layout.addWidget(card)
            self._cards.append(card)

        # 添加底部弹性空间
        self._bottomSpacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        layout.addItem(self._bottomSpacer)

    def _clearCards(self):
        """清除所有脚本卡片"""
        layout = self.ui.scriptListLayout

        # 移除底部弹性空间
        if self._bottomSpacer is not None:
            layout.removeItem(self._bottomSpacer)
            self._bottomSpacer = None

        # 移除并销毁所有卡片
        for card in self._cards:
            layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _onDirChanged(self, _path: str):
        """脚本目录发生变化时自动刷新列表"""
        self.refreshList()
