"""ElidedLabel — 显示时自动 elide 超长文本，不参与宽度布局计算。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """QLabel 子类：文本超宽时自动显示省略号，不撑开父布局。"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._fullText = ""
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def setFullText(self, text: str):
        """设置完整文本，实际渲染时会按宽度自动 elide。"""
        self._fullText = text
        # 布局用空文本，不参与宽度计算
        self.setText("")
        self.update()

    def paintEvent(self, event):
        if not self._fullText:
            super().paintEvent(event)
            return
        fm = self.fontMetrics()
        elided = fm.elidedText(self._fullText, Qt.TextElideMode.ElideRight, self.width())
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.drawText(self.rect(), int(self.alignment()) | int(Qt.TextSingleLine), elided)
