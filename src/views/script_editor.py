"""
Python 代码编辑器组件
====================
基于 QCodeEditor 的 Python 编辑器定制组件，包括：
- PythonCodeEditor：支持冒号自动增加缩进、单字符触发补全、
  扩充版 Python 语法高亮（含多行字符串修复）、补全弹窗样式定制
- FixedLineNumberArea：修复行号区域当前行/普通行颜色区分
"""

import json
from enum import IntEnum

from PySide6.QtCore import Qt, QPointF, QRectF, QRegularExpression, QFile, QIODevice, QTimer, Property, QAbstractListModel, QModelIndex
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen, QTextCursor, QTextFormat
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTextEdit

from pyqcodeeditor import utils as qce_utils
from pyqcodeeditor.highlighters.QHighlightBlockRule import QHighlightBlockRule
from pyqcodeeditor.highlighters.QHighlightRule import QHighlightRule
from pyqcodeeditor.QCodeEditor import QCodeEditor
from pyqcodeeditor.QLanguage import QLanguage
from pyqcodeeditor.QLineNumberArea import QLineNumberArea as _QLineNumberArea
from pyqcodeeditor.QStyleSyntaxHighlighter import QStyleSyntaxHighlighter
from pyqcodeeditor.QSyntaxStyle import QSyntaxStyle

from core import logger


# ============================================================
# 补全数据模型
# ============================================================

class CompletionKind(IntEnum):
    """补全候选项的类型，数值即为排序优先级（越小越靠前）"""
    FUNCTION = 0    # 函数（API 函数、用户定义函数）
    VARIABLE = 1    # 变量 / 常量
    CLASS = 2       # 类
    MODULE = 3      # 模块
    KEYWORD = 4     # Python 关键字
    EXCEPTION = 5   # 异常类

    @property
    def icon(self) -> str:
        """类型对应的图标字符"""
        return _KIND_ICONS[self]

    @property
    def color(self) -> QColor:
        """类型对应的图标颜色"""
        return QColor(_KIND_COLORS[self])


# 图标字符映射
_KIND_ICONS = {
    CompletionKind.FUNCTION:  "ƒ",
    CompletionKind.VARIABLE:  "𝑥",
    CompletionKind.CLASS:     "C",
    CompletionKind.MODULE:    "M",
    CompletionKind.KEYWORD:   "⌘",
    CompletionKind.EXCEPTION: "E",
}

# 图标颜色映射
_KIND_COLORS = {
    CompletionKind.FUNCTION:  "#C678DD",  # 紫色
    CompletionKind.VARIABLE:  "#61AFEF",  # 蓝色
    CompletionKind.CLASS:     "#E5C07B",  # 橙黄色
    CompletionKind.MODULE:    "#98C379",  # 绿色
    CompletionKind.KEYWORD:   "#ABB2BF",  # 灰色
    CompletionKind.EXCEPTION: "#E06C75",  # 红色
}

# QCompleter 用于获取补全文本的自定义 role
COMPLETION_TEXT_ROLE = Qt.UserRole + 1
COMPLETION_KIND_ROLE = Qt.UserRole + 2
COMPLETION_IS_API_ROLE = Qt.UserRole + 3  # 是否为项目 API 符号


class CompletionModel(QAbstractListModel):
    """补全候选项模型，存储带类型信息的补全词汇

    排序规则：先按 CompletionKind 优先级（数值越小越靠前），
    同优先级内按大小写不敏感的字典序排列。
    QCompleter 通过 completionRole=COMPLETION_TEXT_ROLE 进行前缀匹配。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, CompletionKind, bool]] = []  # (text, kind, is_api)

    def setItems(self, items: list[tuple[str, CompletionKind]] | list[tuple[str, CompletionKind, bool]]):
        """设置补全词汇列表（自动排序去重）

        items 中的元素可以是 (text, kind) 或 (text, kind, is_api) 格式。
        """
        self.beginResetModel()
        # 去重：相同 text 保留优先级最高（数值最小）的 kind，is_api 取 True 优先
        seen: dict[str, tuple[CompletionKind, bool]] = {}
        for item in items:
            text, kind = item[0], item[1]
            is_api = item[2] if len(item) > 2 else False
            if text not in seen or kind < seen[text][0]:
                seen[text] = (kind, is_api)
            elif kind == seen[text][0] and is_api:
                seen[text] = (kind, True)
        # 排序：先按 kind 优先级，同 kind 按字典序
        self._items = sorted(
            ((t, k, a) for t, (k, a) in seen.items()),
            key=lambda x: (x[1], x[0].lower()),
        )
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        text, kind, is_api = self._items[index.row()]
        if role == Qt.DisplayRole or role == COMPLETION_TEXT_ROLE:
            return text
        if role == COMPLETION_KIND_ROLE:
            return kind
        if role == COMPLETION_IS_API_ROLE:
            return is_api
        return None


# ============================================================
# Python 代码编辑器：集中所有 QCodeEditor 定制逻辑
# ============================================================

class PythonCodeEditor(QCodeEditor):
    """扩展 QCodeEditor，为 Python 编辑提供完整的定制支持

    包括：冒号行自动增加缩进、单字符触发补全、扩充版语法高亮、
    多行字符串高亮修复、补全弹窗样式定制、等宽字体与缩进配置。
    """

    _TAB_SPACES = 4  # Tab/缩进统一使用 4 个空格

    def __init__(self, parent=None):
        # 跳过父类对 QSyntaxStyle.defaultStyle() 的调用，
        # 避免加载 pyqcodeeditor 内置的 default_style.json 资源文件。
        # 项目使用自定义的语法主题，会在 ScriptEditPage 中立即覆盖。
        QTextEdit.__init__(self, parent)

        self._highlighter = None
        self._syntaxStyle = QSyntaxStyle()  # 空样式占位，不加载任何资源
        self._completer = None
        self._autoIndentation = True
        self._autoParentheses = True
        self._replaceTab = True
        self._tabReplace = " " * self._TAB_SPACES
        self._defaultIndent = self.tabReplaceSize()

        # 创建修复版行号区域
        self._lineNumberArea = FixedLineNumberArea(self)

        self._performConnections()

        # 创建语法高亮器
        self._setupHighlighter()

        self._updateLineNumberAreaWidth(0)

    # ---- 键盘事件定制 ----

    def keyPressEvent(self, e, **kwargs):
        key = e.key()
        if (
            self._autoIndentation
            and (key == Qt.Key_Return or key == Qt.Key_Enter)
            and e.modifiers() == Qt.NoModifier
        ):
            # 在 super().keyPressEvent 之前获取当前行信息
            indentationLevel = self.getIndentationSpaces()
            lineText = self.textCursor().block().text()
            # 去掉行内注释和尾部空白后判断是否以冒号结尾
            stripped = self._stripComment(lineText).rstrip()
            if stripped.endswith(":"):
                # 冒号行：插入换行 + 当前缩进 + 额外一级缩进
                extraIndent = self.tabReplaceSize() if self._replaceTab else 1
                if self._replaceTab:
                    indentStr = " " * (indentationLevel + extraIndent)
                else:
                    tabCounts = self._tabCounts(indentationLevel)
                    indentStr = "\t" * (tabCounts + 1)
                self.insertPlainText("\n" + indentStr)
                return
        # Tab / Shift+Tab：选区缩进/反缩进
        if key == Qt.Key_Tab and e.modifiers() == Qt.NoModifier:
            if self.textCursor().hasSelection():
                self._indentSelection()
                return
        if key == Qt.Key_Backtab:
            self._unindentSelection()
            return

        # Ctrl+/ 切换注释：对当前行或选中行添加/移除 # 注释
        if key == Qt.Key_Slash and e.modifiers() == Qt.ControlModifier:
            self._toggleComment()
            return

        # Backspace 智能退格：光标前全是空格且数量为 tabReplaceSize 的倍数时，一次删除一个缩进单位
        if key == Qt.Key_Backspace and e.modifiers() == Qt.NoModifier and self._replaceTab:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                col = cursor.positionInBlock()
                lineText = cursor.block().text()
                textBeforeCursor = lineText[:col]
                tabSize = self.tabReplaceSize()
                # 光标前全是空格，且空格数是 tabSize 的倍数
                if (
                    col >= tabSize
                    and textBeforeCursor.strip() == ""
                    and col % tabSize == 0
                ):
                    # 删除前面 tabSize 个空格
                    for _ in range(tabSize):
                        cursor.deletePreviousChar()
                    return

        # 其他情况交给父类处理
        super().keyPressEvent(e, **kwargs)

    # ── 行级编辑通用框架 ──────────────────────────────────────

    def _applyLineEdits(self, edits, alwaysRestoreSelection=False):
        """对多行执行插入/删除编辑，自动维护选区

        edits: [(block, posInBlock, insertOrDelete, textOrLen), ...]
            - insertOrDelete = True  → 在 block.position()+posInBlock 处插入 textOrLen
            - insertOrDelete = False → 从 block.position()+posInBlock 处删除 textOrLen 个字符
        alwaysRestoreSelection: 无选区时是否也设置光标位置（反缩进需要）
        """
        cursor = self.textCursor()
        hasSelection = cursor.hasSelection() or alwaysRestoreSelection
        selStart = cursor.selectionStart()
        selEnd = cursor.selectionEnd()
        cursorAtTail = cursor.position() == selEnd

        # 第一遍：计算偏移量
        deltaStart = 0
        deltaEnd = 0
        for b, posInBlock, isInsert, textOrLen in edits:
            pos = b.position() + posInBlock
            delta = len(textOrLen) if isInsert else -textOrLen
            if delta > 0:
                # 插入：选区在插入点之后才受影响
                if selStart > pos:
                    deltaStart += delta
                if selEnd > pos:
                    deltaEnd += delta
            else:
                # 删除：选区在删除区域之后才受影响
                removeLen = -delta
                if selStart > pos + removeLen:
                    deltaStart += delta  # delta < 0
                elif selStart > pos:
                    deltaStart = pos - selStart
                if selEnd > pos + removeLen:
                    deltaEnd += delta
                elif selEnd > pos:
                    deltaEnd = pos - selEnd

        # 第二遍：执行编辑
        cursor.beginEditBlock()
        for b, posInBlock, isInsert, textOrLen in edits:
            pos = b.position() + posInBlock
            cursor.setPosition(pos)
            if isInsert:
                cursor.insertText(textOrLen)
            else:
                cursor.setPosition(pos + textOrLen, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
        cursor.endEditBlock()

        # 恢复选区
        self._restoreSelection(cursor, selStart, selEnd, deltaStart, deltaEnd,
                               cursorAtTail, hasSelection)

    def _restoreSelection(self, cursor, selStart, selEnd, deltaStart, deltaEnd,
                          cursorAtTail, hasSelection):
        """根据偏移量恢复选区位置，保持光标头/尾方向"""
        newStart = max(selStart + deltaStart, 0)
        newEnd = max(selEnd + deltaEnd, 0)
        if hasSelection and newStart != newEnd:
            if cursorAtTail:
                cursor.setPosition(newStart)
                cursor.setPosition(newEnd, QTextCursor.KeepAnchor)
            else:
                cursor.setPosition(newEnd)
                cursor.setPosition(newStart, QTextCursor.KeepAnchor)
        else:
            cursor.setPosition(newStart)
        self.setTextCursor(cursor)

    def _getSelectedBlocks(self, cursor, requireSelection=False):
        """返回选中行的 (startBlock, endBlock, hasSelection)

        requireSelection=True 时，无选区返回 (None, None, False)，
        让调用方自行决定是否交给父类处理。
        """
        hasSelection = cursor.hasSelection()
        if requireSelection and not hasSelection:
            return None, None, False
        doc = self.document()
        if hasSelection:
            startBlock = doc.findBlock(cursor.selectionStart())
            endBlock = doc.findBlock(cursor.selectionEnd())
        else:
            startBlock = cursor.block()
            endBlock = cursor.block()
        return startBlock, endBlock, hasSelection

    @staticmethod
    def _iterBlocks(startBlock, endBlock):
        """从 startBlock 迭代到 endBlock（含）"""
        block = startBlock
        while block.isValid():
            yield block
            if block == endBlock:
                break
            block = block.next()

    # ── 注释切换 ─────────────────────────────────────────────

    @staticmethod
    def _commentPrefixLen(stripped: str) -> int:
        """返回行首注释前缀的长度（0 表示非注释行）"""
        if stripped.startswith("# "):
            return 2
        if stripped.startswith("#"):
            return 1
        return 0

    def _toggleComment(self):
        """Ctrl+/ 切换注释：对当前行或选中行添加/移除 # 注释

        逻辑与 VSCode 一致：
        - 添加注释：在所有操作行的最小缩进处统一插入 "# "，使 # 对齐
        - 取消注释：在每行缩进后查找并移除 "# " 或 "#"
        - 空行不参与决策也不被修改，特例：全为空行时在行首添加 "# "
        - 无选区时切换当前行，有选区时统一切换所选行
        """
        COMMENT_PREFIX = "# "
        cursor = self.textCursor()
        startBlock, endBlock, hasSelection = self._getSelectedBlocks(cursor)

        # 收集行信息
        lineInfos = []
        for block in self._iterBlocks(startBlock, endBlock):
            text = block.text()
            stripped = text.lstrip()
            indentLen = len(text) - len(stripped) if stripped else 0
            commentLen = self._commentPrefixLen(stripped)
            lineInfos.append((block, indentLen, commentLen, stripped))

        # 判断添加/移除
        allEmpty = all(not s for _, _, _, s in lineInfos)
        hasUncommented = any(cl == 0 for _, _, cl, s in lineInfos if s)
        shouldComment = hasUncommented or allEmpty
        minIndent = min((i for _, i, _, s in lineInfos if s), default=0)

        # 构建编辑操作
        edits = []
        for b, indentLen, commentLen, stripped in lineInfos:
            if not stripped and not allEmpty:
                continue
            if shouldComment:
                edits.append((b, minIndent, True, COMMENT_PREFIX))
            elif commentLen > 0:
                edits.append((b, indentLen, False, commentLen))

        self._applyLineEdits(edits, alwaysRestoreSelection=hasSelection)

    # ── 缩进 / 反缩进 ────────────────────────────────────────

    def _indentSelection(self):
        """Tab 增加缩进：对选区所有非空行在行首插入一级缩进

        无选区时交给父类处理（插入 Tab 或空格）。
        空行（仅含空白字符）不添加缩进，与 VSCode 行为一致。
        """
        cursor = self.textCursor()
        startBlock, endBlock, _ = self._getSelectedBlocks(cursor, requireSelection=True)
        if startBlock is None:
            return

        indentStr = self._tabReplace if self._replaceTab else "\t"
        edits = [
            (b, 0, True, indentStr)
            for b in self._iterBlocks(startBlock, endBlock)
            if b.text().strip()  # 跳过空行
        ]
        self._applyLineEdits(edits)

    def _unindentSelection(self):
        """Shift+Tab 减少缩进：对当前行或选区所有行移除一级缩进

        每行最多移除一个缩进单位（tabSize 个空格或 1 个 Tab）。
        """
        tabSize = self.tabReplaceSize()
        cursor = self.textCursor()
        startBlock, endBlock, hasSelection = self._getSelectedBlocks(cursor)

        # 计算每行可移除的缩进长度
        edits = []
        for block in self._iterBlocks(startBlock, endBlock):
            text = block.text()
            if self._replaceTab:
                leadingSpaces = len(text) - len(text.lstrip(' '))
                if leadingSpaces > 0:
                    edits.append((block, 0, False, min(leadingSpaces, tabSize)))
            elif text.startswith('\t'):
                edits.append((block, 0, False, 1))

        if edits:
            self._applyLineEdits(edits, alwaysRestoreSelection=True)

    def _proceedCompleterEnd(self, e):
        """重写补全触发逻辑：输入 1 个字符即开始补全（父类需要 2 个）"""
        key = e.key()
        ctrlOrShift = qce_utils.has_modifier(e, Qt.ControlModifier, Qt.ShiftModifier)
        text = e.text()
        isEmpty = len(text) <= 0
        if not self._completer or (ctrlOrShift and isEmpty) or key == Qt.Key_Delete:
            return
        popup = self._completer.popup()
        eow = r""""(~!@#$%^&*()+{}|:"<>?,./;'[]\-=)"""
        isShortcut = qce_utils.is_shortcut(e, Qt.ControlModifier, Qt.Key_Space)
        completionPrefix = self._wordUnderCursor()
        isContainChar = len(text) > 0 and (text[-1] in eow)
        # 将阈值从 < 2 改为 < 1，输入 1 个字符即触发补全
        if (not isShortcut) and (isEmpty or len(completionPrefix) < 1 or isContainChar):
            popup.hide()
            return

        if completionPrefix != self._completer.completionPrefix():
            self._completer.setCompletionPrefix(completionPrefix)

        cursRect = self.cursorRect()
        cursRect.setWidth(
            max(
                popup.sizeHintForColumn(0),
                self._POPUP_MIN_WIDTH,
            )
        )
        self._completer.complete(cursRect)
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))

    def _wordUnderCursor(self) -> str:
        """重写补全前缀提取：从光标位置向左扫描 Python 标识符字符

        父类使用 QTextCursor.WordUnderCursor，当光标紧贴 ')' 等符号时
        会选中该符号而非前面的标识符，导致括号内无法触发补全。
        """
        tc = self.textCursor()
        blockText = tc.block().text()
        pos = min(tc.positionInBlock(), len(blockText))
        # 从光标位置向左扫描，收集连续的标识符字符（字母、数字、下划线）
        start = pos
        while start > 0 and (blockText[start - 1].isalnum() or blockText[start - 1] == '_'):
            start -= 1
        return blockText[start:pos]

    def _insertCompletion(self, s: str):
        """重写补全插入：选中光标前的标识符前缀后替换

        父类使用 QTextCursor.WordUnderCursor 选中待替换文本，
        与 _wordUnderCursor 存在相同的括号内误选问题。
        当使用 CompletionModel 时，s 已经是纯文本（通过 completionRole 获取）。
        """
        if self._completer.widget() != self:
            return
        tc = self.textCursor()
        prefixLen = len(self._completer.completionPrefix())
        # 向左选中与补全前缀等长的文本，然后替换为完整补全词
        tc.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, prefixLen)
        tc.insertText(s)
        self.setTextCursor(tc)

    # ---- 当前行高亮修复 ----

    def _highlightCurrentLine(self, extraSelection):
        """重写父类方法：修复缺少 FullWidthSelection 导致当前行背景不可见的问题

        QCodeEditor 库的原始实现未设置 QTextFormat.FullWidthSelection，
        导致背景色只覆盖有文字的区域，空行或行尾之后完全不可见。
        """
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            selection.format = self._syntaxStyle.getFormat("CurrentLine")
            selection.format.setForeground(QBrush())
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extraSelection.append(selection)

    # ---- 编辑器初始化 ----

    def showEvent(self, event):
        """首次显示时根据 QSS 生效后的字体设置 Tab 视觉宽度"""
        super().showEvent(event)
        if not hasattr(self, '_tabStopInitialized'):
            self._tabStopInitialized = True
            fm = QFontMetricsF(self.font())
            self.setTabStopDistance(fm.horizontalAdvance(' ') * self._TAB_SPACES)

    # ---- 语言文件路径（高亮 + 补全共享） ----

    _LANG_RESOURCE = ":/styles/python_lang.json"

    # ---- 语法高亮 ----

    def _setupHighlighter(self):
        """创建并配置扩充版 Python 语法高亮器

        直接使用轻量的 QStyleSyntaxHighlighter 基类，自行初始化规则容器，
        避免 QPythonHighlighter 构造函数中加载内置规则后又被清空的浪费。
        """
        self._highlighter = QStyleSyntaxHighlighter()
        # 初始化规则容器和正则模式（QPythonHighlighter 中定义的属性）
        self._highlighter.m_highlightRules = []
        self._highlighter.m_highlightBlockRules = []
        self._highlighter.m_functionPattern = QRegularExpression(
            r"(\b([A-Za-z0-9_]+(?:\.))*([A-Za-z0-9_]+)(?=\())"
        )
        self._loadExtendedPythonRules()
        self.setHighlighter(self._highlighter)

    def _getLanguage(self):
        """获取语言定义实例（带缓存，从 Qt 资源系统加载）"""
        if not hasattr(self, '_cachedLanguage'):
            self._cachedLanguage = self._loadLanguageFromResource(self._LANG_RESOURCE)
        return self._cachedLanguage

    @staticmethod
    def _loadLanguageFromResource(resourcePath: str):
        """从 Qt 资源路径加载语言定义

        QLanguage.load() 只接受文件系统路径，无法直接读取 Qt 资源。
        此方法通过 QFile 读取资源内容后，手动构建 QLanguage 实例。

        Args:
            resourcePath: Qt 资源路径，如 ":/styles/python_lang.json"

        Returns:
            加载成功返回 QLanguage 实例，失败返回 None
        """
        f = QFile(resourcePath)
        if not f.open(QIODevice.ReadOnly):
            logger.app.warning(f"Failed to open language resource: {resourcePath}")
            return None
        try:
            data = json.loads(bytes(f.readAll()))
            if not isinstance(data, dict) or not data:
                return None
            lang = QLanguage()
            for section_name, section in data.items():
                if isinstance(section, list):
                    lang._list[section_name] = section
                    lang._loaded = True
            return lang if lang.isLoaded() else None
        except Exception as e:
            logger.app.warning(f"Failed to load language resource: {resourcePath}, error: {e}")
            return None
        finally:
            f.close()

    def _loadExtendedPythonRules(self):
        """加载扩充版 Python 语言定义"""
        lang = self._getLanguage()
        if not lang:
            return

        # 加载语言规则
        for key in lang.keys():
            names = lang.names(key)
            if not names:
                continue
            for name in names:
                self._highlighter.m_highlightRules.append(
                    QHighlightRule(QRegularExpression(rf"\b{name}\b"), key)
                )

        # 添加数字规则
        self._highlighter.m_highlightRules.append(
            QHighlightRule(QRegularExpression(r"(\b(0b|0x|0o){0,1}[\d.']+\b)"), "Number")
        )

        # 添加字符串规则（使用负向前瞻/后顾排除三引号，避免与多行 block rules 冲突）
        self._highlighter.m_highlightRules.append(
            QHighlightRule(QRegularExpression(r'(?<!")"[^\n"]*"(?!")'), "String")
        )
        self._highlighter.m_highlightRules.append(
            QHighlightRule(QRegularExpression(r"(?<!')'[^\n']*'(?!')"), "String")
        )

        # 添加注释规则
        self._highlighter.m_highlightRules.append(
            QHighlightRule(QRegularExpression(r"#[^\n]*"), "Comment")
        )

        # 添加装饰器规则
        self._highlighter.m_highlightRules.append(
            QHighlightRule(QRegularExpression(r"@\w+"), "Keyword")
        )

        # 添加多行字符串规则
        self._highlighter.m_highlightBlockRules.append(
            QHighlightBlockRule(
                QRegularExpression("(''')"),
                QRegularExpression("(''')"),
                "String",
            )
        )
        self._highlighter.m_highlightBlockRules.append(
            QHighlightBlockRule(
                QRegularExpression(r'(""")'),
                QRegularExpression(r'(""")'),
                "String",
            )
        )

        # 重写 highlightBlock 修复库中 endPattern 搜索偏移 bug
        self._highlighter.highlightBlock = self._fixedHighlightBlock

    def _fixedHighlightBlock(self, text: str):
        """修复版 highlightBlock：解决库中多行块结束标记搜索偏移错误

        原始库在搜索 endPattern 时使用 startIndex + 1 作为偏移，
        导致当结束行恰好以三引号开头时无法正确匹配结束标记，
        使得多行块状态永远无法重置。
        """
        hl = self._highlighter

        # 1. 函数名高亮
        matchIterator = hl.m_functionPattern.globalMatch(text)
        while matchIterator.hasNext():
            match = matchIterator.next()
            hl.setFormat(
                match.capturedStart(),
                match.capturedLength(),
                hl.syntaxStyle().getFormat("Type"),
            )
            hl.setFormat(
                match.capturedStart(3),
                match.capturedLength(3),
                hl.syntaxStyle().getFormat("Function"),
            )

        # 2. 单行规则高亮
        for rule in hl.m_highlightRules:
            matchIterator = rule.pattern.globalMatch(text)
            while matchIterator.hasNext():
                match = matchIterator.next()
                hl.setFormat(
                    match.capturedStart(),
                    match.capturedLength(),
                    hl.syntaxStyle().getFormat(rule.formatName),
                )

        # 3. 多行块规则高亮（修复版）
        hl.setCurrentBlockState(0)
        startIndex = 0
        highlightRuleId = hl.previousBlockState()
        isContinuation = (1 <= highlightRuleId <= len(hl.m_highlightBlockRules))

        if not isContinuation:
            for i, rule in enumerate(hl.m_highlightBlockRules):
                startIndex = qce_utils.index_of(text, rule.startPattern, 0)
                if startIndex >= 0:
                    highlightRuleId = i + 1
                    break

        while startIndex >= 0:
            blockRule = hl.m_highlightBlockRules[highlightRuleId - 1]
            # 修复：延续行从 offset 0 搜索结束标记；
            #       起始行跳过 startPattern 的匹配长度
            if isContinuation:
                searchOffset = startIndex
            else:
                startMatch = blockRule.startPattern.match(text, startIndex)
                searchOffset = startIndex + startMatch.capturedLength()
            isContinuation = False  # 后续循环都是新块的起始

            match = blockRule.endPattern.match(text, searchOffset)
            endIndex = match.capturedStart()
            if endIndex == -1:
                hl.setCurrentBlockState(highlightRuleId)
                matchLength = len(text) - startIndex
            else:
                matchLength = endIndex - startIndex + match.capturedLength()

            hl.setFormat(
                startIndex,
                matchLength,
                hl.syntaxStyle().getFormat(blockRule.formatName),
            )
            startIndex = qce_utils.index_of(
                text, blockRule.startPattern, startIndex + matchLength
            )

    # ---- 补全弹窗样式 ----

    _POPUP_MIN_WIDTH = 220  # 补全弹窗最小宽度（像素）

    def setupCompleterPopupStyle(self):
        """配置补全弹窗的视觉样式，使其与项目设计语言一致

        应在 setCompleter() 之后调用。
        """
        completer = self.completer()
        if not completer:
            return

        # 使用支持循环导航 + 圆角绘制的自定义 popup 替换默认 popup
        popup = _WrapAroundListView()
        popup.setObjectName("completerPopup")
        popup.maxVisibleItems = completer.maxVisibleItems()
        completer.setPopup(popup)

        # 使用紧凑行高的 delegate 控制 item 高度
        popup.setItemDelegate(_CompactItemDelegate(popup))

        # 设置窗口属性以支持圆角（去除系统默认窗口框架，启用透明背景）
        target = popup.window()
        target.setWindowFlag(Qt.FramelessWindowHint, True)
        target.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        target.setAttribute(Qt.WA_TranslucentBackground, True)

    # ---- 空白字符可视化 ----

    def paintEvent(self, event, **kwargs):
        """在父类绘制完成后，用淡色叠加绘制可见区域内的空白字符

        空格绘制为居中小点 ·，Tab 绘制为右箭头 →。
        颜色取自语法主题的 VisualWhitespace 定义，不会像 Qt 原生
        ShowTabsAndSpaces 那样跟随文本前景色导致过于显眼。
        """
        super().paintEvent(event)

        # 获取 VisualWhitespace 颜色，未定义则不绘制
        fmt = self._syntaxStyle.getFormat("VisualWhitespace")
        color = fmt.foreground().color()
        if not color.isValid():
            return

        painter = QPainter(self.viewport())
        painter.setPen(color)
        painter.setFont(self.font())

        fm = QFontMetricsF(self.font())
        spaceWidth = fm.horizontalAdvance(' ')
        dot = '·'

        doc = self.document()
        docLayout = doc.documentLayout()
        scrollY = self.verticalScrollBar().value()
        viewportRect = self.viewport().rect()

        # 从第一个可见块开始遍历
        blockNumber = self._getFirstVisibleBlock()
        block = doc.findBlockByNumber(blockNumber)

        while block.isValid():
            blockRect = docLayout.blockBoundingRect(block)
            # 将文档坐标转换为 viewport 坐标（减去滚动偏移）
            blockTop = blockRect.top() - scrollY
            if blockTop > viewportRect.bottom():
                break
            if block.isVisible() and blockTop + blockRect.height() >= viewportRect.top():
                text = block.text()
                layout = block.layout()
                # 通过 QTextLayout 精确定位每个字符，天然支持折行
                for i, ch in enumerate(text):
                    if ch != ' ' and ch != '\t':
                        continue
                    line = layout.lineForTextPosition(i)
                    if not line.isValid():
                        continue
                    # line.y() 是相对于 block 顶部的偏移
                    lineY = blockTop + line.y()
                    lineH = line.height()
                    charX = line.cursorToX(i)[0] + blockRect.left()
                    if ch == ' ':
                        charRect = QRectF(charX, lineY, spaceWidth, lineH)
                        painter.drawText(charRect, Qt.AlignCenter, dot)
                    else:
                        # Tab：获取下一个位置的 x 来确定 Tab 宽度
                        nextX = line.cursorToX(i + 1)[0] + blockRect.left()
                        tabWidth = nextX - charX
                        # 绘制占满 Tab 宽度的箭头线：—————→
                        padding = spaceWidth * 0.3
                        midY = lineY + lineH / 2.0
                        lineX1 = charX + padding
                        lineX2 = charX + tabWidth - padding
                        arrowSize = min(spaceWidth * 0.35, tabWidth * 0.15)
                        painter.drawLine(QPointF(lineX1, midY), QPointF(lineX2, midY))
                        painter.drawLine(QPointF(lineX2, midY),
                                         QPointF(lineX2 - arrowSize, midY - arrowSize))
                        painter.drawLine(QPointF(lineX2, midY),
                                         QPointF(lineX2 - arrowSize, midY + arrowSize))
            block = block.next()

        painter.end()

    # ---- 工具方法 ----

    @staticmethod
    def _stripComment(line: str) -> str:
        """去掉行内 # 注释（忽略字符串内的 #）"""
        inSingle = False
        inDouble = False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '\\':
                i += 2
                continue
            if ch == "'" and not inDouble:
                inSingle = not inSingle
            elif ch == '"' and not inSingle:
                inDouble = not inDouble
            elif ch == '#' and not inSingle and not inDouble:
                return line[:i]
            i += 1
        return line


# ============================================================
# 修复 QLineNumberArea 行号颜色 bug
# ============================================================

class FixedLineNumberArea(_QLineNumberArea):
    """修复版行号区域：区分当前行号和普通行号的颜色"""

    def paintEvent(self, event, **kwargs):
        painter = QPainter(self)
        bgColor = self._syntaxStyle.getFormat("Text").background().color()
        painter.fillRect(event.rect(), bgColor)

        blockNumber = self._codeEditParent._getFirstVisibleBlock()
        block = self._codeEditParent.document().findBlockByNumber(blockNumber)
        docLayout = self._codeEditParent.document().documentLayout()
        blockRect = docLayout.blockBoundingRect(block)
        top = int(
            blockRect
            .translated(0, -self._codeEditParent.verticalScrollBar().value())
            .top()
        )
        bottom = top + int(blockRect.height())

        currentLineColor = self._syntaxStyle.getFormat("CurrentLineNumber").foreground().color()
        otherLinesColor = self._syntaxStyle.getFormat("LineNumber").foreground().color()
        currentBlockNumber = self._codeEditParent.textCursor().blockNumber()

        painter.setFont(self._codeEditParent.font())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                # 修复：根据是否为当前行选择不同颜色
                if blockNumber == currentBlockNumber:
                    painter.setPen(currentLineColor if currentLineColor else otherLinesColor)
                else:
                    painter.setPen(otherLinesColor if otherLinesColor else currentLineColor)
                painter.drawText(
                    -5,
                    top,
                    self.sizeHint().width(),
                    self._codeEditParent.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + int(
                docLayout.blockBoundingRect(block).height()
            )
            blockNumber += 1


# ============================================================
# 补全弹窗循环导航支持
# ============================================================

class _CompactItemDelegate(QStyledItemDelegate):
    """紧凑行高的 item delegate，手动绘制带圆角裁剪的 item 背景和类型图标

    QSS 中 item 背景设为 transparent，由此 delegate 在 paint() 中
    手动绘制选中/悬停背景，并设置与弹窗边框一致的圆角 clip path，
    确保 item 背景不会超出圆角边框范围。
    每个 item 左侧绘制一个彩色类型图标字符（类似 VSCode 风格）。
    """

    _VERTICAL_PADDING = 2   # 文字上下各留 2px
    _ICON_LEFT_MARGIN = 6   # 图标左侧边距
    _ICON_RIGHT_MARGIN = 6  # 图标与文字之间的间距
    _ICON_WIDTH = 14         # 图标字符占用的宽度

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        fm = option.fontMetrics
        size.setHeight(fm.height() + self._VERTICAL_PADDING * 2)
        # 宽度需包含图标区域
        size.setWidth(size.width() + self._ICON_LEFT_MARGIN + self._ICON_WIDTH + self._ICON_RIGHT_MARGIN)
        return size

    def paint(self, painter, option, index):
        """绘制选中/悬停高亮 → 类型图标 → 文本

        底色由 _WrapAroundListView.paintEvent 第一步的 AA 圆角 fillPath
        统一负责；delegate 只负责高亮叠加，带 AA + 圆角 clip 使首尾 item
        的高亮与弹窗圆角平滑贴合。选中态切换时残留由 currentChanged 中
        触发对 previous/current 两行的 update() 消除。
        """
        listView = option.widget
        isSelected = bool(option.state & QStyle.State_Selected)
        isHovered = bool(option.state & QStyle.State_MouseOver)

        # 预计算背景色与文字色（无 listView 时全部为 None，后续分别判空）
        bgColor = None
        fgColor = None
        if listView:
            bgColor = listView.selBg if isSelected else (listView.hoverBg if isHovered else None)
            fgColor = listView.selFg if isSelected else listView.fg

        # ---- 高亮叠加（圆角 clip 防止首尾 item 溢出弹窗圆角外）----
        if bgColor and bgColor.isValid():
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            clipPath = QPainterPath()
            clipPath.addRoundedRect(QRectF(listView.viewport().rect()),
                                    _WrapAroundListView._BORDER_RADIUS,
                                    _WrapAroundListView._BORDER_RADIUS)
            painter.setClipPath(clipPath)
            painter.fillRect(option.rect, bgColor)
            painter.restore()

        rect = option.rect
        kind = index.data(COMPLETION_KIND_ROLE)
        text = index.data(Qt.DisplayRole) or ""

        painter.save()

        # ---- 绘制类型图标 ----
        if kind is not None:
            iconChar = kind.icon
            iconColor = kind.color
            iconRect = QRectF(
                rect.left() + self._ICON_LEFT_MARGIN,
                rect.top(),
                self._ICON_WIDTH,
                rect.height(),
            )
            iconFont = QFont(option.font)
            iconFont.setPointSizeF(option.font.pointSizeF() * 0.85)
            iconFont.setBold(True)
            painter.setFont(iconFont)
            painter.setPen(iconColor)
            painter.drawText(iconRect, Qt.AlignCenter, iconChar)

        # ---- 绘制补全文本 ----
        textLeft = rect.left() + self._ICON_LEFT_MARGIN + self._ICON_WIDTH + self._ICON_RIGHT_MARGIN
        textRect = QRectF(textLeft, rect.top(), rect.right() - textLeft, rect.height())
        painter.setFont(option.font)
        if fgColor and fgColor.isValid():
            painter.setPen(fgColor)
        else:
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        painter.drawText(textRect, Qt.AlignVCenter | Qt.AlignLeft, text)

        # ---- 绘制 API 标签 ----
        is_api = index.data(COMPLETION_IS_API_ROLE)
        if is_api:
            self._paintApiTag(painter, option, rect, isSelected)

        painter.restore()

    def _paintApiTag(self, painter: QPainter, option: QStyleOptionViewItem,
                     rect: QRectF, isSelected: bool):
        """在补全项右侧绘制 'API' 小标签，标识该符号来自项目 API"""
        tagText = "API"
        tagFont = QFont(option.font)
        tagFont.setPointSizeF(option.font.pointSizeF() * 0.7)
        tagFont.setBold(True)
        tagFm = QFontMetricsF(tagFont)
        tagWidth = tagFm.horizontalAdvance(tagText) + 8  # 左右各 4px 内边距
        tagHeight = tagFm.height() + 2
        tagRight = rect.right() - 8  # 距右边缘 8px
        tagRect = QRectF(
            tagRight - tagWidth,
            rect.top() + (rect.height() - tagHeight) / 2,
            tagWidth,
            tagHeight,
        )
        # 标签颜色：复用 FUNCTION 图标色，半透明处理
        baseColor = _KIND_COLORS[CompletionKind.FUNCTION]
        tagBgColor = QColor(baseColor)
        tagBgColor.setAlpha(30 if isSelected else 20)
        tagFgColor = QColor(baseColor)
        tagFgColor.setAlpha(200 if isSelected else 160)

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(tagBgColor)
        painter.drawRoundedRect(tagRect, 3, 3)
        painter.setFont(tagFont)
        painter.setPen(tagFgColor)
        painter.drawText(tagRect, Qt.AlignCenter, tagText)


def _completer_color_property(attr: str) -> Property:
    """为补全弹窗颜色生成 Qt Property（供 QSS qproperty- 使用）"""
    return Property(
        QColor,
        lambda self: getattr(self, attr),
        lambda self, c: (setattr(self, attr, QColor(c)), self.update()),
    )


class _WrapAroundListView(QListView):
    """支持循环导航和圆角绘制的 QListView，用作 QCompleter 的 popup

    功能：
    1. 循环导航：QCompleter 在边界处会将 currentIndex 设为无效（row=-1），
       此子类拦截并跳转到对端，实现无缝循环。
    2. 圆角边框：在 FramelessWindowHint + TranslucentBackground 下，
       QSS 的 border/border-radius 会被裁剪。此子类通过 paintEvent
       手绘圆角矩形背景和边框来实现视觉效果。

    颜色属性通过 QSS qproperty- 设置，例如：
        _WrapAroundListView#completerPopup { qproperty-bgColor: #282C34; }
    """

    _BORDER_RADIUS = 6  # 圆角半径（像素）
    _SCROLLBAR_WIDTH = 6  # overlay 滚动条宽度（像素）
    _SCROLLBAR_MIN_HEIGHT = 16  # 滚动条最小高度（像素）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.maxVisibleItems = 10  # 最大可见行数，由 setupCompleterPopupStyle 设置
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 隐藏原生滚动条，改用手绘 overlay

        # 颜色属性默认值（可通过 QSS qproperty 覆盖）
        self._bgColor = QColor("#282C34")
        self._borderColor = QColor("#3E4451")
        self._selBg = QColor("#3E4451")
        self._hoverBg = QColor("#2C313A")
        self._fg = QColor("#ABB2BF")
        self._selFg = QColor("#D7DAE0")
        self._scrollColor = QColor("#4B5263")

    # ---- Qt Property: 颜色（供 QSS qproperty- 使用） ----

    bgColor = _completer_color_property('_bgColor')
    borderColor = _completer_color_property('_borderColor')
    selBg = _completer_color_property('_selBg')
    hoverBg = _completer_color_property('_hoverBg')
    fg = _completer_color_property('_fg')
    selFg = _completer_color_property('_selFg')
    scrollColor = _completer_color_property('_scrollColor')

    def showEvent(self, event):
        """每次弹窗显示时校正高度

        弹窗隐藏后再次显示相同内容时，complete() 设置的窗口尺寸
        可能与上次相同，不触发有效的 resizeEvent，导致高度校正被跳过。
        在 showEvent 中补做一次，确保弹窗高度始终精确。
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._adjustHeightToContent)

    def resizeEvent(self, event):
        """拦截外部对弹窗的 resize，延迟修正为精确高度

        QCompleter.complete() 每次都会设置弹窗大小，但其计算在
        FramelessWindowHint 下不够精确。延迟到当前事件处理完毕后
        修正，_adjustHeightToContent 自身的幂等守卫保证不会无限循环。
        """
        super().resizeEvent(event)
        QTimer.singleShot(0, self._adjustHeightToContent)

    def _adjustHeightToContent(self):
        """根据实际 item 总高度精确调整弹窗窗口大小（不超过 maxVisibleItems）"""
        model = self.model()
        if not model:
            return
        rowCount = model.rowCount()
        if rowCount <= 0:
            return
        visibleCount = min(rowCount, self.maxVisibleItems)
        totalHeight = sum(self.sizeHintForRow(i) for i in range(visibleCount))
        window = self.window()
        extraHeight = window.height() - self.viewport().height()
        newHeight = totalHeight + extraHeight
        if newHeight != window.height():
            window.resize(window.width(), newHeight)

    def paintEvent(self, event):
        """圆角背景 → 父类绘制 → overlay 滚动条 → 圆角边框"""
        vp = self.viewport()
        rect = QRectF(vp.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        roundedPath = QPainterPath()
        roundedPath.addRoundedRect(rect, self._BORDER_RADIUS, self._BORDER_RADIUS)

        # 第一步：绘制圆角背景
        if self._bgColor.isValid():
            painter = QPainter(vp)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.fillPath(roundedPath, QBrush(self._bgColor))
            painter.end()

        # 第二步：父类绘制列表内容（delegate 仅叠加高亮 + 图标 + 文本）
        super().paintEvent(event)

        # 第三步：绘制 overlay 滚动条（仅在内容可滚动时显示）
        self._paintOverlayScrollbar(vp, rect)

        # 第四步：绘制圆角边框（带 AA，复用同一条圆角路径）
        if self._borderColor.isValid():
            painter = QPainter(vp)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(self._borderColor, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(roundedPath)
            painter.end()

    def _paintOverlayScrollbar(self, vp, clipRect):
        """在 viewport 右侧绘制 overlay 风格的滚动条

        滚动条紧贴弹窗右边缘、从顶到底铺满，由圆角裁剪路径自然裁切，
        与弹窗融为一体。仅当内容溢出时才绘制。
        """
        scrollBar = self.verticalScrollBar()
        if not scrollBar or scrollBar.maximum() <= 0:
            return  # 内容未溢出，无需滚动条

        scrollColor = self._scrollColor
        if not scrollColor or not scrollColor.isValid():
            return

        w = self._SCROLLBAR_WIDTH
        # 轨道从顶到底铺满整个弹窗高度
        trackTop = clipRect.top()
        trackHeight = clipRect.height()
        if trackHeight <= 0:
            return

        # 通过 scrollBar 的 value / maximum 计算精确进度
        sbMax = scrollBar.maximum()
        sbVal = scrollBar.value()
        sbPage = scrollBar.pageStep()
        sbTotal = sbMax + sbPage  # 逻辑总范围

        # 手柄高度按 pageStep / total 比例计算
        handleHeight = max(trackHeight * (sbPage / sbTotal), self._SCROLLBAR_MIN_HEIGHT)
        # 手柄位置按 value / maximum 比例计算
        handleTop = trackTop + (trackHeight - handleHeight) * (sbVal / sbMax) if sbMax > 0 else trackTop

        # 绘制：紧贴右边缘，无间距
        handleRect = QRectF(
            clipRect.right() - w,
            handleTop,
            w,
            handleHeight,
        )
        painter = QPainter(vp)
        # 裁剪到圆角区域内，滚动条顶部/底部被自然裁切
        clip = QPainterPath()
        clip.addRoundedRect(clipRect, self._BORDER_RADIUS, self._BORDER_RADIUS)
        painter.setClipPath(clip)
        painter.setPen(Qt.NoPen)
        painter.setBrush(scrollColor)
        painter.drawRect(handleRect)
        painter.end()

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        # 强制整个 viewport 重绘，让 paintEvent 第一步的 AA 圆角底统一
        # 刷新脏区域，消除旧高亮的 AA α 边缘残留。
        # 曾尝试只 update previous/current 两行的 visualRect 以减少重绘范围，
        # 但实测仍会出现细线残留（item rect 内的 α 边缘未被新一帧的 bg
        # fillPath 覆盖），因此保持全量刷新。
        self.viewport().update()
        if current.isValid() or not previous.isValid():
            return
        # currentIndex 从有效变为无效（-1），说明到达边界
        model = self.model()
        if model is None or model.rowCount() == 0:
            return
        rowCount = model.rowCount()
        prevRow = previous.row()
        if prevRow == 0:
            # 从第一项越界 → 跳到最后一项
            self.setCurrentIndex(model.index(rowCount - 1, 0))
        elif prevRow == rowCount - 1:
            # 从最后一项越界 → 跳到第一项
            self.setCurrentIndex(model.index(0, 0))
