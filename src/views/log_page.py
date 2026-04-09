"""
日志页
======
显示本次运行期间的所有日志输出，方便用户实时查看。
UI 布局由 log_page.ui 定义，通过 build.py 编译为 ui_log_page.py。
"""

import logging
from collections import deque
from html import escape

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Property, QEvent
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QWidget

from ui.generated.ui_log_page import Ui_LogPage

# ---- 日志记录缓存上限 ----
_MAX_LOG_RECORDS = 5000


class LogSignalEmitter(QObject):
    """跨线程日志信号发射器"""
    logReceived = Signal(str, int)  # (格式化消息, 日志级别)


class QtLogHandler(logging.Handler):
    """自定义日志 Handler，将日志记录通过 Qt 信号发送到 UI"""

    def __init__(self, emitter: LogSignalEmitter):
        super().__init__()
        self._emitter = emitter

    def emit(self, record):
        try:
            msg = self.format(record)
            self._emitter.logReceived.emit(msg, record.levelno)
        except Exception:
            self.handleError(record)


# ---- Qt Property 工厂函数 ----

def _color_property(attr: str) -> Property:
    """为日志级别颜色生成 Qt Property（供 QSS qproperty- 使用）"""
    return Property(
        QColor,
        lambda self: getattr(self, attr),
        lambda self, c: setattr(self, attr, QColor(c)),
    )


class LogPage(QWidget):
    """日志页面"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        # ---- 加载 UI ----
        self.ui = Ui_LogPage()
        self.ui.setupUi(self)

        # ---- 日志级别颜色（默认值，可通过 QSS qproperty 覆盖） ----
        self._logColorDebug = QColor("#5C6370")
        self._logColorInfo = QColor("#ABB2BF")
        self._logColorWarning = QColor("#E5C07B")
        self._logColorError = QColor("#E06C75")
        self._logColorCritical = QColor("#E06C75")

        # ---- 日志记录缓存（用于主题切换时重新渲染） ----
        self._logRecords: deque[tuple[str, int]] = deque(maxlen=_MAX_LOG_RECORDS)

        # ---- 信号发射器 & Handler ----
        self._emitter = LogSignalEmitter(self)
        self._handler = QtLogHandler(self._emitter)

        # ---- 设置日志记录缓存上限 ----
        self.ui.logTextArea.setMaximumBlockCount(_MAX_LOG_RECORDS)

        # ---- 连接信号 ----
        self.ui.btnClearLog.clicked.connect(self._onClear)
        self._emitter.logReceived.connect(self._appendLog)

    @property
    def handler(self) -> QtLogHandler:
        """返回日志 Handler，供外部注册到 logger"""
        return self._handler

    # ---- Qt Property: 日志级别颜色（供 QSS qproperty- 使用） ----

    logColorDebug = _color_property('_logColorDebug')
    logColorInfo = _color_property('_logColorInfo')
    logColorWarning = _color_property('_logColorWarning')
    logColorError = _color_property('_logColorError')
    logColorCritical = _color_property('_logColorCritical')

    # 日志级别 → 颜色属性名的静态映射
    _LEVEL_COLOR_ATTR = {
        logging.DEBUG: '_logColorDebug',
        logging.INFO: '_logColorInfo',
        logging.WARNING: '_logColorWarning',
        logging.ERROR: '_logColorError',
        logging.CRITICAL: '_logColorCritical',
    }

    def _getColorForLevel(self, level: int) -> str:
        """根据日志级别返回当前主题下的颜色 hex 值"""
        attr = self._LEVEL_COLOR_ATTR.get(level, '_logColorInfo')
        return getattr(self, attr).name()

    def _buildLogHtml(self, message: str, level: int) -> str:
        """根据日志级别生成带颜色的 HTML 片段"""
        color = self._getColorForLevel(level)
        return (
            f'<pre style="margin:0; white-space:pre-wrap; color:{color};'
            f' font-family:Consolas,\'Courier New\',monospace;">'
            f'{escape(message)}</pre>'
        )

    def _scrollToBottom(self):
        """将日志文本区域滚动到底部"""
        self.ui.logTextArea.moveCursor(QTextCursor.MoveOperation.End)
        self.ui.logTextArea.ensureCursorVisible()

    @Slot(str, int)
    def _appendLog(self, message: str, level: int):
        """追加带颜色的日志到文本区域并自动滚动到底部"""
        self._logRecords.append((message, level))
        html = self._buildLogHtml(message, level)
        self.ui.logTextArea.appendHtml(html)
        self._scrollToBottom()

    def _onClear(self):
        """清空日志"""
        self._logRecords.clear()
        self.ui.logTextArea.clear()

    def _refreshTheme(self):
        """主题切换后重新渲染所有已缓存的日志，使颜色与新主题匹配"""
        area = self.ui.logTextArea
        area.setUpdatesEnabled(False)
        area.clear()
        # 批量拼接 HTML 后一次性写入，避免逐条 appendHtml 导致的重复文档重排
        html = ''.join(self._buildLogHtml(msg, lvl) for msg, lvl in self._logRecords)
        if html:
            area.appendHtml(html)
        area.setUpdatesEnabled(True)
        # 延迟到下一个事件循环执行，确保布局更新完成后再滚动
        QTimer.singleShot(0, self._scrollToBottom)

    def changeEvent(self, event: QEvent):
        """监听样式变化事件，主题切换时自动重新渲染日志颜色"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._refreshTheme()
