"""
脚本编辑页
==========
提供脚本编辑页面，支持加载/保存脚本、主题切换、代码补全等功能。
"""

import json
import os

from PySide6.QtCore import Signal, Qt, QStringListModel, QFileSystemWatcher, QFile, QIODevice
from PySide6.QtGui import QShortcut, QKeySequence, QTextCursor
from PySide6.QtWidgets import QWidget, QCompleter

from pyqcodeeditor.QSyntaxStyle import QSyntaxStyle

from core import logger, api
from core.config import settings as configSettings
from ui.generated.ui_script_edit_page import Ui_ScriptEditPage
from views.appearance import resolveTheme
from views.script_editor import PythonCodeEditor


# ============================================================
# 脚本编辑页
# ============================================================

class ScriptEditPage(QWidget):
    """脚本编辑页面"""

    # 信号定义
    backRequested = Signal()    # 请求返回列表页

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_ScriptEditPage()
        self.ui.setupUi(self)

        self._filePath = ""
        self._isModified = False
        self._savingInProgress = False  # 保存时临时禁用文件监听

        # ---- 创建代码编辑器 ----
        self._editor = PythonCodeEditor()
        self._editor.setObjectName("codeEditor")

        # 将编辑器添加到 editorFrame
        self.ui.editorLayout.addWidget(self._editor)

        # ---- 创建补全器 ----
        self._setupCompleter()

        # ---- 加载语法主题 ----
        self._syntaxStyleLight = QSyntaxStyle()
        self._syntaxStyleDark = QSyntaxStyle()
        self._loadSyntaxStyles()
        self._applyCurrentTheme()

        # ---- 连接信号 ----
        self.ui.btnBack.clicked.connect(self.backRequested.emit)
        self.ui.btnSave.clicked.connect(self.saveScript)
        self._editor.textChanged.connect(self._onTextChanged)

        # ---- Ctrl+S 快捷键 ----
        self._saveShortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._saveShortcut.activated.connect(self.saveScript)

        # ---- 文件系统监听 ----
        self._fileWatcher = QFileSystemWatcher(self)
        self._fileWatcher.fileChanged.connect(self._onFileChanged)

    # ---- 公共方法 ----

    def loadScript(self, filePath: str):
        """加载脚本文件到编辑器

        Args:
            filePath: 脚本文件的完整路径
        """
        # 移除旧文件的监听
        oldPaths = self._fileWatcher.files()
        if oldPaths:
            self._fileWatcher.removePaths(oldPaths)

        self._filePath = filePath
        name = os.path.splitext(os.path.basename(filePath))[0]
        self.ui.scriptTitleLabel.setText(name)

        try:
            with open(filePath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.app.error(f"Failed to read script file: {filePath}, error: {e}")
            content = ""

        # 添加新文件的监听
        if os.path.isfile(filePath):
            self._fileWatcher.addPath(filePath)

        # 阻断 textChanged 信号避免标记为已修改
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)

        # 重置修改状态
        self._setModified(False)

        # 光标移到开头
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self._editor.setTextCursor(cursor)

    def saveScript(self):
        """保存编辑器内容到脚本文件"""
        if not self._filePath or not self._isModified:
            return

        try:
            # 保存时临时移除监听，避免触发不必要的重载
            self._savingInProgress = True
            watchedFiles = self._fileWatcher.files()
            if watchedFiles:
                self._fileWatcher.removePaths(watchedFiles)

            with open(self._filePath, "w", encoding="utf-8") as f:
                f.write(self._editor.toPlainText())
            self._setModified(False)
        except Exception as e:
            logger.app.error(f"Failed to save script file: {self._filePath}, error: {e}")
        finally:
            # 保存完成后重新添加监听
            self._savingInProgress = False
            if self._filePath and os.path.isfile(self._filePath):
                self._fileWatcher.addPath(self._filePath)

    def hasUnsavedChanges(self) -> bool:
        """检查是否有未保存的修改"""
        return self._isModified

    def currentFilePath(self) -> str:
        """获取当前编辑的文件路径"""
        return self._filePath

    def updateTheme(self, isDark: bool):
        """更新编辑器主题

        Args:
            isDark: 是否为深色主题
        """
        style = self._syntaxStyleDark if isDark else self._syntaxStyleLight
        self._editor.setSyntaxStyle(style)

    # ---- 内部方法 ----

    def _onTextChanged(self):
        """编辑器内容变化时标记为已修改"""
        if not self._isModified:
            self._setModified(True)

    def _setModified(self, modified: bool):
        """设置修改状态"""
        self._isModified = modified
        self.ui.btnSave.setEnabled(modified)

        # 更新标题栏 * 后缀
        name = os.path.splitext(os.path.basename(self._filePath))[0] if self._filePath else ""
        if modified:
            self.ui.scriptTitleLabel.setText(f"{name} *")
        else:
            self.ui.scriptTitleLabel.setText(name)

    def _loadSyntaxStyles(self):
        """从 Qt 资源系统加载 One Light 和 One Dark 语法主题"""
        if not self._loadStyleFromResource(":/styles/syntax_light.json", self._syntaxStyleLight):
            logger.app.warning("Failed to load light syntax style from Qt resource")

        if not self._loadStyleFromResource(":/styles/syntax_dark.json", self._syntaxStyleDark):
            logger.app.warning("Failed to load dark syntax style from Qt resource")

    @staticmethod
    def _loadStyleFromResource(resourcePath: str, style: QSyntaxStyle) -> bool:
        """从 Qt 资源路径加载语法样式

        QSyntaxStyle.load() 只接受文件系统路径，无法直接读取 Qt 资源。
        此方法通过 QFile 读取资源内容后，直接调用内部的 _processStyleSchema。

        Args:
            resourcePath: Qt 资源路径，如 ":/styles/syntax_dark.json"
            style: 要填充的 QSyntaxStyle 实例

        Returns:
            是否加载成功
        """
        f = QFile(resourcePath)
        if not f.open(QIODevice.ReadOnly):
            return False
        try:
            data = json.loads(bytes(f.readAll()))
            if isinstance(data, dict):
                style._processStyleSchema(data)
                return style.isLoaded()
        except Exception:
            return False
        finally:
            f.close()
        return False

    def _applyCurrentTheme(self):
        """根据当前应用主题应用对应的语法样式"""
        currentTheme = resolveTheme(configSettings.theme)
        isDark = currentTheme == "dark"
        self.updateTheme(isDark)

    def _setupCompleter(self):
        """创建并配置代码补全器

        补全词汇来源：
        1. python_lang.json 中的所有词汇（与语法高亮共享同一数据源）
        2. 脚本 API 暴露的函数名
        3. 允许导入的内置模块名
        """
        completionWords = []

        # 从语言定义文件加载 Python 关键字、内置函数、类型、异常等
        completionWords.extend(self._editor.loadLanguageWords())

        # 从 API 获取函数名列表
        try:
            ctx = api._create_context(None)
            for name in ctx:
                if not name.startswith("_"):
                    completionWords.append(name)
        except Exception as e:
            logger.app.warning(f"Failed to get API context for completion: {e}")

        # 添加允许导入的内置模块名
        completionWords.extend(["math", "time"])

        # 去重并按大小写不敏感的字典序排序
        completionWords = sorted(set(completionWords), key=str.lower)

        # 创建补全器
        model = QStringListModel(completionWords)
        completer = QCompleter()
        completer.setModel(model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(8)

        self._editor.setCompleter(completer)

        # 配置补全弹窗样式
        self._editor.setupCompleterPopupStyle()

    def _onFileChanged(self, path: str):
        """文件被外部修改时的处理"""
        if self._savingInProgress:
            return

        # 文件可能被删除后重建（某些编辑器的保存方式），重新添加监听
        if os.path.isfile(path) and path not in self._fileWatcher.files():
            self._fileWatcher.addPath(path)

        # 若编辑器无未保存修改 → 自动重载文件内容
        if not self._isModified:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._editor.blockSignals(True)
                self._editor.setPlainText(content)
                self._editor.blockSignals(False)
                self._setModified(False)
            except Exception:
                pass
        # 若编辑器有未保存修改 → 保留编辑器内容不变
