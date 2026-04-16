"""
脚本编辑页
==========
提供脚本编辑页面，支持加载/保存脚本、主题切换、代码补全等功能。
"""

import ast
import builtins
import inspect
import json
import keyword
import os

from PySide6.QtCore import Signal, Qt, QFileSystemWatcher, QFile, QIODevice, QTimer
from PySide6.QtGui import QShortcut, QKeySequence, QTextCursor
from PySide6.QtWidgets import QWidget, QCompleter

from pyqcodeeditor.QSyntaxStyle import QSyntaxStyle

from core import logger, common
from core.config import settings as configSettings
from ui.generated.ui_script_edit_page import Ui_ScriptEditPage
from views.appearance import resolveTheme
from views.script_editor import PythonCodeEditor, CompletionKind, CompletionModel, COMPLETION_TEXT_ROLE


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
        self._lastContextSymbols = []  # AST 提取的上下文符号缓存

        # ---- 创建代码编辑器 ----
        self._editor = PythonCodeEditor()
        self._editor.setObjectName("codeEditor")

        # 将编辑器添加到 editorFrame
        self.ui.editorLayout.addWidget(self._editor)

        # ---- 创建补全器 ----
        self._setupCompleter()

        # ---- 上下文补全防抖定时器 ----
        self._contextUpdateTimer = QTimer(self)
        self._contextUpdateTimer.setSingleShot(True)
        self._contextUpdateTimer.setInterval(500)  # 停止输入 500ms 后更新
        self._contextUpdateTimer.timeout.connect(self._updateContextCompletions)

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

        # 立即提取上下文符号（新文件加载后无需等待防抖）
        self._updateContextCompletions()

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
        """编辑器内容变化时标记为已修改，并触发上下文补全更新"""
        if not self._isModified:
            self._setModified(True)
        self._contextUpdateTimer.start()

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

    # Python 关键字中属于常量的子集，映射为 VARIABLE 而非 KEYWORD
    _KEYWORD_CONSTANTS = frozenset({"True", "False", "None"})

    def _setupCompleter(self):
        """创建并配置代码补全器

        补全词汇来源（按优先级从高到低）：
        1. __builtins__.py 中通过 AST 解析提取的 API 符号（高优先级）
        2. 用户上下文符号（高优先级，动态更新）
        3. Python builtins + keyword 模块自省的词汇（低优先级）
        4. 允许导入的内置模块名（低优先级）
        """
        baseItems: list[tuple[str, CompletionKind]] = []

        # 从 Python builtins + keyword 模块自省获取内置词汇
        baseItems.extend(self._buildPythonBuiltinItems())

        # 从 __builtins__.py 中通过 AST 解析提取 API 符号（高优先级）
        builtinsPath = common.builtins_path()
        try:
            with open(builtinsPath, "r", encoding="utf-8") as f:
                baseItems.extend(self._extractSymbolsWithKind(f.read()))
        except Exception as e:
            logger.app.warning(f"Failed to parse builtins for completion: {e}")

        # 添加允许导入的内置模块名
        baseItems.extend([
            ("math", CompletionKind.MODULE),
            ("time", CompletionKind.MODULE),
        ])

        self._baseCompletionItems = baseItems

        # 创建补全器
        model = CompletionModel()
        model.setItems(baseItems)
        completer = QCompleter()
        completer.setModel(model)
        completer.setCompletionRole(COMPLETION_TEXT_ROLE)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setModelSorting(QCompleter.ModelSorting.UnsortedModel)
        completer.setMaxVisibleItems(8)

        self._editor.setCompleter(completer)

        # 配置补全弹窗样式
        self._editor.setupCompleterPopupStyle()

    @classmethod
    def _buildPythonBuiltinItems(cls) -> list[tuple[str, CompletionKind]]:
        """通过 Python 运行时自省构建内置补全词汇

        数据来源：
        - keyword 模块：所有 Python 关键字（if/for/def/class/...）
        - builtins 模块：所有内置名称，通过 inspect 精确判断类型

        Returns:
            (符号名, CompletionKind) 元组列表
        """
        items: list[tuple[str, CompletionKind]] = []

        # 1. Python 关键字
        for kw in keyword.kwlist:
            if kw in cls._KEYWORD_CONSTANTS:
                items.append((kw, CompletionKind.VARIABLE))
            else:
                items.append((kw, CompletionKind.KEYWORD))
        # soft keywords（match/case/type 等，Python 3.10+）
        for skw in getattr(keyword, 'softkwlist', []):
            items.append((skw, CompletionKind.KEYWORD))

        # 2. builtins 模块中的所有公开名称
        for name in dir(builtins):
            if name.startswith('_'):
                continue
            obj = getattr(builtins, name, None)
            if obj is None:
                continue
            # 精确判断类型
            if inspect.isfunction(obj) or inspect.isbuiltin(obj):
                items.append((name, CompletionKind.FUNCTION))
            elif inspect.isclass(obj):
                if issubclass(obj, BaseException):
                    items.append((name, CompletionKind.EXCEPTION))
                else:
                    items.append((name, CompletionKind.CLASS))
            else:
                # NotImplemented, Ellipsis, __debug__ 等特殊常量
                items.append((name, CompletionKind.VARIABLE))

        return items

    @staticmethod
    def _extractSymbolsWithKind(source: str) -> list[tuple[str, CompletionKind]]:
        """通过 AST 解析提取源码中的顶层公开符号名及其类型

        提取函数名、类名、变量名、函数参数名、导入名等，
        过滤掉以下划线开头的私有符号。

        Args:
            source: Python 源码文本

        Returns:
            (符号名, CompletionKind) 元组列表；解析失败时返回空列表
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        symbols: dict[str, CompletionKind] = {}  # name -> kind（保留最高优先级）

        def _add(name: str, kind: CompletionKind):
            if name and not name.startswith('_'):
                if name not in symbols or kind < symbols[name]:
                    symbols[name] = kind

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _add(node.name, CompletionKind.FUNCTION)
                for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                    _add(arg.arg, CompletionKind.VARIABLE)
                if node.args.vararg:
                    _add(node.args.vararg.arg, CompletionKind.VARIABLE)
                if node.args.kwarg:
                    _add(node.args.kwarg.arg, CompletionKind.VARIABLE)
            elif isinstance(node, ast.ClassDef):
                _add(node.name, CompletionKind.CLASS)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                _add(node.id, CompletionKind.VARIABLE)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    _add(alias.asname or alias.name, CompletionKind.MODULE)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    _add(alias.asname or alias.name, CompletionKind.MODULE)
            elif isinstance(node, ast.Global):
                for name in node.names:
                    _add(name, CompletionKind.VARIABLE)

        return list(symbols.items())

    def _updateContextCompletions(self):
        """从当前文档中提取用户定义的标识符，合并到补全模型

        使用 AST 解析精确提取函数名、类名、变量名、参数名、导入名等。
        语法错误时沿用上一次成功解析的结果，保证编辑过程中补全不中断。
        补全弹窗可见时跳过更新，避免模型刷新导致选中态丢失。
        """
        # 补全弹窗正在显示时，不更新模型，避免打断用户交互
        completer = self._editor.completer()
        if completer and completer.popup() and completer.popup().isVisible():
            return

        text = self._editor.toPlainText()
        extracted = self._extractSymbolsWithKind(text)
        if extracted or not self._lastContextSymbols:
            # 解析成功（或从未成功过），更新缓存
            baseTextSet = {item[0] for item in self._baseCompletionItems}
            contextSymbols = [(t, k) for t, k in extracted if t not in baseTextSet]
            self._lastContextSymbols = contextSymbols
        else:
            # 解析失败（语法错误），沿用上次成功的结果
            contextSymbols = self._lastContextSymbols

        # 合并基础词汇和上下文符号，更新模型
        merged = self._baseCompletionItems + contextSymbols
        completer = self._editor.completer()
        if completer:
            completer.model().setItems(merged)

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
