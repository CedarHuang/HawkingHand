"""
Python 代码编辑器组件
====================
基于 QCodeEditor 的 Python 编辑器定制组件，包括：
- PythonCodeEditor：支持冒号自动增加缩进、单字符触发补全、
  扩充版 Python 语法高亮（含多行字符串修复）、补全弹窗样式定制
- FixedLineNumberArea：修复行号区域当前行/普通行颜色区分
"""

import json

from PySide6.QtCore import Qt, QRegularExpression, QFile, QIODevice
from PySide6.QtGui import QFontMetricsF, QPainter, QTextCursor
from PySide6.QtWidgets import QTextEdit

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
        # 其他情况交给父类处理
        super().keyPressEvent(e, **kwargs)

    def _proceedCompleterEnd(self, e):
        """重写补全触发逻辑：输入 1 个字符即开始补全（父类需要 2 个）"""
        key = e.key()
        ctrlOrShift = qce_utils.has_modifier(e, Qt.ControlModifier, Qt.ShiftModifier)
        text = e.text()
        isEmpty = len(text) <= 0
        if not self._completer or (ctrlOrShift and isEmpty) or key == Qt.Key_Delete:
            return
        eow = r""""(~!@#$%^&*()+{}|:"<>?,./;'[]\-=)"""
        isShortcut = qce_utils.is_shortcut(e, Qt.ControlModifier, Qt.Key_Space)
        completionPrefix = self._wordUnderCursor()
        isContainChar = len(text) > 0 and (text[-1] in eow)
        # 将阈值从 < 2 改为 < 1，输入 1 个字符即触发补全
        if (not isShortcut) and (isEmpty or len(completionPrefix) < 1 or isContainChar):
            self._completer.popup().hide()
            return

        if completionPrefix != self._completer.completionPrefix():
            self._completer.setCompletionPrefix(completionPrefix)
            self._completer.popup().setCurrentIndex(
                self._completer.completionModel().index(0, 0)
            )

        cursRect = self.cursorRect()
        cursRect.setWidth(
            self._completer.popup().sizeHintForColumn(0)
            + self._completer.popup().verticalScrollBar().sizeHint().width()
        )
        self._completer.complete(cursRect)

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
        """
        if self._completer.widget() != self:
            return
        tc = self.textCursor()
        prefixLen = len(self._completer.completionPrefix())
        # 向左选中与补全前缀等长的文本，然后替换为完整补全词
        tc.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, prefixLen)
        tc.insertText(s)
        self.setTextCursor(tc)

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

    def loadLanguageWords(self):
        """从语言定义文件加载所有词汇，供补全器复用

        Returns:
            包含所有 Python 关键字、内置函数、类型、异常等词汇的列表
        """
        lang = self._getLanguage()
        if not lang:
            return []
        words = []
        for key in lang.keys():
            names = lang.names(key)
            if names:
                words.extend(names)
        return words

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

    def setupCompleterPopupStyle(self):
        """配置补全弹窗的视觉样式，使其与项目设计语言一致

        应在 setCompleter() 之后调用。
        """
        popup = self.completer().popup()
        if not popup:
            return

        # 设置窗口属性以支持圆角和纤细描边（去除系统默认窗口框架）
        popupWindow = popup.window()
        if popupWindow and popupWindow is not popup:
            popupWindow.setWindowFlag(Qt.FramelessWindowHint, True)
            popupWindow.setWindowFlag(Qt.NoDropShadowWindowHint, True)
            popupWindow.setAttribute(Qt.WA_TranslucentBackground, True)

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
