"""
事件列表页
==========
展示所有已配置的事件卡片列表，支持添加、空状态显示、
卡片启用/禁用切换、右键菜单操作、删除确认、拖拽排序等 UI 交互。
"""

from PySide6.QtCore import Signal, Qt, QPoint, QTimer
from PySide6.QtWidgets import (
    QWidget, QSpacerItem, QSizePolicy, QMessageBox, QFrame, QLabel,
)

from ui.generated.ui_event_list_page import Ui_EventListPage
from views.event_card import EventCard


# 拖拽触发的最小移动距离（像素）
_DRAG_THRESHOLD = 8
# 拖拽自动滚动：鼠标距滚动区域边缘多少像素内触发自动滚动
_AUTO_SCROLL_MARGIN = 50
# 拖拽自动滚动：定时器间隔（毫秒）
_AUTO_SCROLL_INTERVAL = 20
# 拖拽自动滚动：每次滚动的基础步长（像素）
_AUTO_SCROLL_STEP = 6


class _DropIndicator(QFrame):
    """拖拽时显示的插入位置指示线"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("dropIndicator")
        self.setFixedHeight(3)
        self.hide()


class EventListPage(QWidget):
    """事件列表页面"""

    # 信号定义
    addEventRequested = Signal()          # 请求添加新事件
    editEventRequested = Signal(int)      # 请求编辑事件（传递索引）
    deleteEventRequested = Signal(int)    # 请求删除事件（传递索引）
    copyEventRequested = Signal(int)      # 请求复制事件（传递索引）
    moveEventRequested = Signal(int, int) # 请求移动事件（原索引, 目标索引）
    enabledToggled = Signal(int, bool)     # 事件启用/禁用切换（索引, 状态）

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_EventListPage()
        self.ui.setupUi(self)

        # 卡片列表引用
        self._cards: list[EventCard] = []
        # 底部弹性空间
        self._bottomSpacer: QSpacerItem | None = None

        # ---- 拖拽排序状态 ----
        self._dragCard: EventCard | None = None        # 正在拖拽的卡片
        self._dragIndex: int = -1                      # 拖拽卡片的原始索引
        self._dragStartPos: QPoint = QPoint()          # 鼠标按下时的全局坐标
        self._dragGhost: QWidget | None = None         # 拖拽时的半透明幽灵
        self._dropIndicator = _DropIndicator(self.ui.scrollContent)  # 插入指示线
        self._isDragging = False                       # 是否正在拖拽中
        self._dragPending = False                      # 是否等待拖拽触发（鼠标已按下但未达到阈值）
        self._dragGhostX = 0                           # 幽灵卡片的固定水平位置
        self._lastDragGlobalPos: QPoint = QPoint()     # 最近一次拖拽的鼠标全局坐标

        # 拖拽自动滚动定时器
        self._autoScrollTimer = QTimer(self)
        self._autoScrollTimer.setInterval(_AUTO_SCROLL_INTERVAL)
        self._autoScrollTimer.timeout.connect(self._onAutoScroll)
        self._autoScrollDir = 0                        # 滚动方向：-1 向上, 1 向下, 0 不滚动
        self._autoScrollSpeed = 0                      # 当前滚动速度（像素/tick）

        # 连接按钮信号
        self.ui.btnAddEvent.clicked.connect(self.addEventRequested.emit)
        self.ui.btnAddFirstEvent.clicked.connect(self.addEventRequested.emit)

        # 初始化为空状态
        self._updateEmptyState()

    # ---- 公共方法 ----

    def clearCards(self):
        """清空所有事件卡片"""
        # 取消正在进行的拖拽
        self._cancelDrag()

        for card in self._cards:
            self.ui.eventListLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # 移除底部弹性空间
        if self._bottomSpacer is not None:
            self.ui.eventListLayout.removeItem(self._bottomSpacer)
            self._bottomSpacer = None

        self._updateEmptyState()

    def addCard(self, eventType: str, hotkey: str, display_name: str,
                script_name: str = "", scope: str = "*",
                extra: str = "", enabled: bool = True) -> EventCard:
        """添加一张事件卡片

        Returns:
            创建的 EventCard 实例
        """
        card = EventCard(self)
        card.setEventData(eventType, hotkey, display_name, script_name, scope, extra, enabled)

        index = len(self._cards)
        self._cards.append(card)

        # 移除底部弹性空间（稍后重新添加）
        if self._bottomSpacer is not None:
            self.ui.eventListLayout.removeItem(self._bottomSpacer)

        # 添加卡片到布局
        self.ui.eventListLayout.addWidget(card)

        # 重新添加底部弹性空间
        self._bottomSpacer = QSpacerItem(
            0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding
        )
        self.ui.eventListLayout.addItem(self._bottomSpacer)

        # 连接卡片信号
        card.clicked.connect(lambda idx=index: self.editEventRequested.emit(idx))
        card.editRequested.connect(lambda idx=index: self.editEventRequested.emit(idx))
        card.copyRequested.connect(lambda idx=index: self.copyEventRequested.emit(idx))
        card.deleteRequested.connect(lambda idx=index: self._confirmDelete(idx))
        card.enabledToggled.connect(lambda checked, idx=index: self.enabledToggled.emit(idx, checked))

        # 为卡片安装事件过滤器以支持拖拽
        card.installEventFilter(self)

        self._updateEmptyState()
        return card

    def rebuildCards(self, cardDataList: list[tuple]):
        """批量重建所有卡片，保持滚动位置不变

        Args:
            cardDataList: 每个元素为 (eventType, hotkey, target, scope, extra, enabled) 元组
        """
        # 记住当前滚动位置
        scrollBar = self.ui.scrollArea.verticalScrollBar()
        savedScrollPos = scrollBar.value()

        # 清空卡片但不切换空状态（避免 scrollArea 被隐藏导致滚动位置重置）
        self._cancelDrag()
        for card in self._cards:
            self.ui.eventListLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        if self._bottomSpacer is not None:
            self.ui.eventListLayout.removeItem(self._bottomSpacer)
            self._bottomSpacer = None

        # 重新添加所有卡片
        for data in cardDataList:
            self.addCard(*data)

        # 最终统一更新空状态
        self._updateEmptyState()

        # 恢复滚动位置
        scrollBar.setValue(savedScrollPos)

    def cardCount(self) -> int:
        """返回当前卡片数量"""
        return len(self._cards)

    # ---- 拖拽排序 ----

    def eventFilter(self, obj, event):
        """事件过滤器：拦截卡片的鼠标事件以实现拖拽排序"""
        if not isinstance(obj, EventCard) or obj not in self._cards:
            return super().eventFilter(obj, event)

        eventType = event.type()

        if eventType == event.Type.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                # 检查是否点击在按钮上，如果是则不启动拖拽
                child = obj.childAt(event.position().toPoint())
                if child in (obj.ui.btnToggleEnabled, obj.ui.btnMore):
                    return False
                # 记录拖拽起始信息
                self._dragCard = obj
                self._dragIndex = self._cards.index(obj)
                self._dragStartPos = event.globalPosition().toPoint()
                self._dragPending = True
                # 不拦截事件，让卡片也能处理点击
                return False

        elif eventType == event.Type.MouseMove:
            if self._dragPending and not self._isDragging:
                # 检查是否超过拖拽阈值
                delta = event.globalPosition().toPoint() - self._dragStartPos
                if delta.manhattanLength() >= _DRAG_THRESHOLD:
                    self._startDrag()
                    return True
            if self._isDragging:
                self._updateDrag(event.globalPosition().toPoint())
                return True

        elif eventType == event.Type.MouseButtonRelease:
            if self._isDragging:
                self._finishDrag()
                return True
            # 重置待拖拽状态
            self._dragPending = False
            self._dragCard = None
            return False

        return super().eventFilter(obj, event)

    def _startDrag(self):
        """开始拖拽：创建幽灵卡片并隐藏原卡片"""
        self._isDragging = True
        self._dragPending = False

        card = self._dragCard
        if card is None:
            return

        # 取消卡片的点击待发射状态，避免拖拽结束后误触发 clicked 信号
        card._clickPending = False

        # 创建卡片的半透明截图作为幽灵
        pixmap = card.grab()
        self._dragGhost = QWidget(self.window())
        self._dragGhost.setFixedSize(pixmap.size())
        self._dragGhost.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self._dragGhost.setAttribute(Qt.WA_TranslucentBackground)
        self._dragGhost.setWindowOpacity(0.7)

        # 将截图绘制到幽灵控件上
        ghostLabel = QLabel(self._dragGhost)
        ghostLabel.setPixmap(pixmap)
        ghostLabel.setFixedSize(pixmap.size())

        # 记录幽灵的固定水平位置（与原卡片对齐），拖拽时只允许垂直移动
        cardGlobalPos = card.mapToGlobal(QPoint(0, 0))
        self._dragGhostX = cardGlobalPos.x()

        # 显示幽灵
        globalPos = self._dragStartPos
        self._dragGhost.move(
            self._dragGhostX,
            globalPos.y() - 10
        )
        self._dragGhost.show()

        # 降低原卡片的透明度
        card.setWindowOpacity(0.3)
        card.setProperty("dragging", True)
        card.style().unpolish(card)
        card.style().polish(card)

        # 抓取鼠标
        card.grabMouse()

    def _clampGlobalPosToScrollArea(self, globalPos: QPoint) -> QPoint:
        """将全局坐标的 Y 分量钳制在滚动区域可视范围内

        确保拖拽逻辑坐标不会超出滚动区域边界，
        同时保留原始 X 坐标不变。
        """
        scrollArea = self.ui.scrollArea
        viewport = scrollArea.viewport()
        # 获取 viewport 的全局上下边界
        topLeft = viewport.mapToGlobal(QPoint(0, 0))
        bottomRight = viewport.mapToGlobal(QPoint(0, viewport.height()))
        clampedY = max(topLeft.y(), min(globalPos.y(), bottomRight.y()))
        return QPoint(globalPos.x(), clampedY)

    def _updateDrag(self, globalPos: QPoint):
        """更新拖拽：移动幽灵并更新插入指示线位置，检测边缘自动滚动"""
        # 将鼠标坐标钳制在滚动区域可视范围内
        clampedPos = self._clampGlobalPosToScrollArea(globalPos)
        self._lastDragGlobalPos = clampedPos

        # 移动幽灵（水平位置锁定，垂直方向限制在滚动区域内）
        if self._dragGhost:
            self._dragGhost.move(
                self._dragGhostX,
                clampedPos.y() - 10
            )

        # 计算目标插入位置（使用钳制后的坐标）
        targetIndex = self._calcDropIndex(clampedPos)
        self._showDropIndicator(targetIndex)

        # 检测是否需要自动滚动（使用原始坐标，以便鼠标超出边界时仍能触发滚动）
        self._checkAutoScroll(globalPos)

    def _finishDrag(self):
        """完成拖拽：执行排序并清理"""
        if self._dragCard:
            self._dragCard.releaseMouse()

        globalPos = self._dragCard.mapToGlobal(QPoint(0, 0)) if self._dragCard else QPoint()
        # 使用最后的鼠标位置计算目标索引
        # 由于 releaseMouse 后无法获取鼠标位置，使用 dropIndicator 的当前位置推算
        targetIndex = self._calcDropIndex(
            self._dragGhost.pos() + QPoint(self._dragGhost.width() // 2, 10)
            if self._dragGhost else globalPos
        )

        fromIndex = self._dragIndex

        # 清理拖拽状态
        self._cleanupDrag()

        # 如果位置发生了变化，发射移动信号
        if fromIndex != -1 and targetIndex != -1 and fromIndex != targetIndex:
            self.moveEventRequested.emit(fromIndex, targetIndex)

    def _cancelDrag(self):
        """取消拖拽"""
        if self._isDragging and self._dragCard:
            self._dragCard.releaseMouse()
        self._cleanupDrag()

    def _cleanupDrag(self):
        """清理所有拖拽状态"""
        # 恢复原卡片透明度
        if self._dragCard:
            self._dragCard.setWindowOpacity(1.0)
            self._dragCard.setProperty("dragging", False)
            self._dragCard.style().unpolish(self._dragCard)
            self._dragCard.style().polish(self._dragCard)

        # 销毁幽灵
        if self._dragGhost:
            self._dragGhost.hide()
            self._dragGhost.deleteLater()
            self._dragGhost = None

        # 隐藏指示线
        self._dropIndicator.hide()

        # 停止自动滚动
        self._autoScrollTimer.stop()
        self._autoScrollDir = 0
        self._autoScrollSpeed = 0

        # 重置状态
        self._dragCard = None
        self._dragIndex = -1
        self._isDragging = False
        self._dragPending = False
        self._dragGhostX = 0
        self._lastDragGlobalPos = QPoint()

    def _checkAutoScroll(self, globalPos: QPoint):
        """检测鼠标是否在滚动区域边缘，启动或停止自动滚动"""
        scrollArea = self.ui.scrollArea
        # 将鼠标全局坐标转换为 scrollArea 的局部坐标
        localPos = scrollArea.mapFromGlobal(globalPos)
        viewHeight = scrollArea.viewport().height()
        mouseY = localPos.y()

        if mouseY < _AUTO_SCROLL_MARGIN:
            # 鼠标在顶部边缘区域（或已超出顶部）→ 向上滚动
            # 越靠近/超出边缘滚动越快
            proximity = max(1, _AUTO_SCROLL_MARGIN - mouseY)
            self._autoScrollDir = -1
            self._autoScrollSpeed = max(1, int(_AUTO_SCROLL_STEP * proximity / _AUTO_SCROLL_MARGIN))
            if not self._autoScrollTimer.isActive():
                self._autoScrollTimer.start()
        elif mouseY > viewHeight - _AUTO_SCROLL_MARGIN:
            # 鼠标在底部边缘区域（或已超出底部）→ 向下滚动
            proximity = max(1, mouseY - (viewHeight - _AUTO_SCROLL_MARGIN))
            self._autoScrollDir = 1
            self._autoScrollSpeed = max(1, int(_AUTO_SCROLL_STEP * proximity / _AUTO_SCROLL_MARGIN))
            if not self._autoScrollTimer.isActive():
                self._autoScrollTimer.start()
        else:
            # 鼠标不在边缘区域，停止自动滚动
            self._autoScrollTimer.stop()
            self._autoScrollDir = 0
            self._autoScrollSpeed = 0

    def _onAutoScroll(self):
        """自动滚动定时器回调：滚动列表并更新指示线位置"""
        if not self._isDragging or self._autoScrollDir == 0:
            self._autoScrollTimer.stop()
            return

        scrollBar = self.ui.scrollArea.verticalScrollBar()
        newValue = scrollBar.value() + self._autoScrollDir * self._autoScrollSpeed
        newValue = max(scrollBar.minimum(), min(newValue, scrollBar.maximum()))
        scrollBar.setValue(newValue)

        # 滚动后重新计算指示线位置（鼠标全局坐标不变，但 scrollContent 的局部坐标变了）
        if not self._lastDragGlobalPos.isNull():
            targetIndex = self._calcDropIndex(self._lastDragGlobalPos)
            self._showDropIndicator(targetIndex)

    def _calcDropIndex(self, globalPos: QPoint) -> int:
        """根据鼠标全局坐标计算目标插入索引

        返回值语义：将拖拽卡片移动到返回索引的位置（swap 语义）。
        """
        if not self._cards:
            return 0

        scrollContent = self.ui.scrollContent
        localPos = scrollContent.mapFromGlobal(globalPos)
        mouseY = localPos.y()

        # 遍历所有卡片，找到鼠标所在的位置
        for i, card in enumerate(self._cards):
            cardTop = card.y()
            cardMid = cardTop + card.height() // 2
            if mouseY < cardMid:
                return i

        # 鼠标在所有卡片下方
        return len(self._cards) - 1

    def _showDropIndicator(self, targetIndex: int):
        """在目标位置显示插入指示线"""
        if not self._cards or targetIndex < 0:
            self._dropIndicator.hide()
            return

        scrollContent = self.ui.scrollContent
        layout = self.ui.eventListLayout

        # 计算指示线的 Y 坐标
        if targetIndex <= self._dragIndex:
            # 在目标卡片的上方
            if targetIndex < len(self._cards):
                refCard = self._cards[targetIndex]
                y = refCard.y() - 2
            else:
                y = 0
        else:
            # 在目标卡片的下方
            if targetIndex < len(self._cards):
                refCard = self._cards[targetIndex]
                y = refCard.y() + refCard.height() + 2
            else:
                lastCard = self._cards[-1]
                y = lastCard.y() + lastCard.height() + 2

        # 设置指示线位置和宽度
        margins = layout.contentsMargins()
        self._dropIndicator.setGeometry(
            margins.left(), y,
            scrollContent.width() - margins.left() - margins.right(), 3
        )
        self._dropIndicator.show()
        self._dropIndicator.raise_()

    # ---- 内部方法 ----

    def _updateEmptyState(self):
        """根据卡片数量切换空状态/列表视图"""
        hasCards = len(self._cards) > 0
        self.ui.scrollArea.setVisible(hasCards)
        self.ui.emptyState.setVisible(not hasCards)

    def _confirmDelete(self, index: int):
        """弹出删除确认对话框"""
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr("Are you sure you want to delete this event?\nThis action cannot be undone."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.deleteEventRequested.emit(index)

