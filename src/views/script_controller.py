"""
脚本业务控制器
=============
管理脚本的新建、编辑、复制、删除等业务逻辑。
将 UI 层的信号与文件系统操作解耦。
"""

import os
import re
import shutil

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QStackedWidget, QPushButton, QMessageBox, QInputDialog, QLineEdit,
)

from core import common, logger
from views.constants import PageIndex
from views.script_edit_page import ScriptEditPage
from views.script_list_page import ScriptListPage

# 合法脚本名正则：仅允许字母、数字、下划线，不能以数字开头
_VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ScriptController:
    """脚本业务控制器

    Args:
        scriptListPage: 脚本列表页实例
        scriptEditPage: 脚本编辑页实例
        contentStack: 内容区 QStackedWidget
        navBtnScripts: 脚本列表导航按钮
    """

    def __init__(self, scriptListPage: ScriptListPage, scriptEditPage: ScriptEditPage,
                 contentStack: QStackedWidget, navBtnScripts: QPushButton):
        self._scriptListPage = scriptListPage
        self._scriptEditPage = scriptEditPage
        self._contentStack = contentStack
        self._navBtnScripts = navBtnScripts

        # 连接列表页信号
        self._scriptListPage.editScriptRequested.connect(self.goToEditScript)
        self._scriptListPage.copyScriptRequested.connect(self.onCopyScript)
        self._scriptListPage.deleteScriptRequested.connect(self.onDeleteScript)
        self._scriptListPage.newScriptRequested.connect(self.onNewScript)
        self._scriptListPage.openDirRequested.connect(self.onOpenDir)

        # 连接编辑页信号
        self._scriptEditPage.backRequested.connect(self._onBackFromEdit)

    # ---- 页面导航 ----

    def goToScriptList(self):
        """切换到脚本列表页"""
        self._scriptListPage.refreshList()
        self._contentStack.setCurrentIndex(PageIndex.SCRIPT_LIST)
        self._navBtnScripts.setChecked(True)

    def goToEditScript(self, filePath: str):
        """切换到脚本编辑页

        Args:
            filePath: 脚本文件的完整路径
        """
        self._scriptEditPage.loadScript(filePath)
        self._contentStack.setCurrentIndex(PageIndex.SCRIPT_EDIT)

    def isOnEditPage(self) -> bool:
        """检查当前是否在脚本编辑页"""
        return self._contentStack.currentIndex() == PageIndex.SCRIPT_EDIT

    # ---- 脚本操作 ----

    def onNewScript(self):
        """新建脚本：弹出输入框，校验名称，创建空文件，进入编辑页"""
        parent = self._scriptListPage

        name, ok = QInputDialog.getText(
            parent,
            QCoreApplication.translate("ScriptController", "New Script"),
            QCoreApplication.translate("ScriptController", "Script name (letters, digits, underscores):"),
            QLineEdit.Normal,
            "",
        )
        if not ok or not name.strip():
            return

        name = name.strip()

        # 校验名称合法性
        if not _VALID_NAME_RE.match(name):
            QMessageBox.warning(
                parent,
                QCoreApplication.translate("ScriptController", "Invalid Name"),
                QCoreApplication.translate(
                    "ScriptController",
                    "Script name can only contain letters, digits, and underscores, "
                    "and cannot start with a digit."
                ),
            )
            return

        # 检查是否已存在
        filePath = os.path.join(common.scripts_path(), f"{name}.py")
        if os.path.exists(filePath):
            QMessageBox.warning(
                parent,
                QCoreApplication.translate("ScriptController", "Name Conflict"),
                QCoreApplication.translate(
                    "ScriptController",
                    "A script named '{name}' already exists."
                ).format(name=name),
            )
            return

        # 创建空文件
        try:
            with open(filePath, "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            logger.app.error(f"Failed to create script file: {filePath}, error: {e}")
            QMessageBox.critical(
                parent,
                QCoreApplication.translate("ScriptController", "Error"),
                QCoreApplication.translate(
                    "ScriptController",
                    "Failed to create script file."
                ),
            )
            return

        # 进入编辑页
        self.goToEditScript(filePath)

    def onCopyScript(self, filePath: str):
        """复制脚本文件"""
        if not os.path.isfile(filePath):
            return

        dirPath = os.path.dirname(filePath)
        baseName = os.path.splitext(os.path.basename(filePath))[0]

        # 生成不冲突的副本名称
        copyName = f"{baseName}_copy"
        copyPath = os.path.join(dirPath, f"{copyName}.py")
        counter = 1
        while os.path.exists(copyPath):
            counter += 1
            copyName = f"{baseName}_copy{counter}"
            copyPath = os.path.join(dirPath, f"{copyName}.py")

        try:
            shutil.copy2(filePath, copyPath)
        except Exception as e:
            logger.app.error(f"Failed to copy script: {filePath} -> {copyPath}, error: {e}")
            QMessageBox.critical(
                self._scriptListPage,
                QCoreApplication.translate("ScriptController", "Error"),
                QCoreApplication.translate(
                    "ScriptController",
                    "Failed to copy script file."
                ),
            )
            return

        self._scriptListPage.refreshList()

    def onDeleteScript(self, filePath: str):
        """删除脚本文件（带确认对话框）"""
        if not os.path.isfile(filePath):
            return

        name = os.path.splitext(os.path.basename(filePath))[0]
        parent = self._scriptListPage

        reply = QMessageBox.question(
            parent,
            QCoreApplication.translate("ScriptController", "Delete Script"),
            QCoreApplication.translate(
                "ScriptController",
                "Are you sure you want to delete '{name}'?\nThis action cannot be undone."
            ).format(name=name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            os.remove(filePath)
        except Exception as e:
            logger.app.error(f"Failed to delete script: {filePath}, error: {e}")
            QMessageBox.critical(
                parent,
                QCoreApplication.translate("ScriptController", "Error"),
                QCoreApplication.translate(
                    "ScriptController",
                    "Failed to delete script file."
                ),
            )
            return

        self._scriptListPage.refreshList()

    @staticmethod
    def onOpenDir():
        """打开脚本目录"""
        scriptsDir = common.scripts_path()
        os.startfile(scriptsDir)

    # ---- 未保存修改检查 ----

    def checkUnsavedBeforeLeave(self) -> bool:
        """检查编辑页是否有未保存修改，弹出确认对话框。

        Returns:
            True: 允许离开（已保存或用户选择不保存）
            False: 用户取消，不允许离开
        """
        if not self._scriptEditPage.hasUnsavedChanges():
            return True

        parent = self._scriptEditPage
        reply = QMessageBox.question(
            parent,
            QCoreApplication.translate("ScriptController", "Unsaved Changes"),
            QCoreApplication.translate(
                "ScriptController",
                "You have unsaved changes. Do you want to save before leaving?"
            ),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )

        if reply == QMessageBox.Save:
            self._scriptEditPage.saveScript()
            return True
        elif reply == QMessageBox.Discard:
            return True
        else:
            # Cancel
            return False

    # ---- 内部方法 ----

    def _onBackFromEdit(self):
        """编辑页返回按钮点击"""
        if self.checkUnsavedBeforeLeave():
            self.goToScriptList()
