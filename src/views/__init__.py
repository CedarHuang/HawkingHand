import ctypes
import sys

from PySide6.QtCore import QObject, QEvent, Qt, QLocale, QPropertyAnimation, QEasingCurve, QTimer, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QWidget, QSpinBox, QDoubleSpinBox, QComboBox, QGraphicsOpacityEffect, QApplication

from core.config import settings as configSettings
from core.models import ParamDef, ParamType


def _polishWidget(widget: QWidget):
    """刷新控件样式，使 setProperty 设置的动态属性在 QSS 中生效"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _NoScrollFilter(QObject):
    """滚轮事件过滤器：控件未聚焦时忽略滚轮，避免意外修改数值。

    安装后会将控件的 focusPolicy 设为 StrongFocus（仅点击/Tab 聚焦），
    未聚焦时滚轮事件被忽略并自动传递给父级滚动区域。
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


class _ComboPopupFadeFilter(QObject):
    """QComboBox 弹出容器事件过滤器：淡入淡出 + 阴影移除。

    安装在弹出容器（combo.view().window()）上，监听 Show/Hide 事件：
    - Show：移除 Qt 自动添加的阴影，启动淡入动画。
    - Hide：重置透明度状态，确保下次弹出正常。

    淡出由 combo.hidePopup 的猴子补丁驱动，在动画结束后才真正关闭弹层。
    """

    _DURATION = 120  # ms

    def installOn(self, combo: QComboBox):
        """为 combo 安装弹层淡入淡出。仅首次调用生效。"""
        if getattr(combo, "_fadeInstalled", False):
            return
        combo._fadeInstalled = True
        combo._fadeClosing = False
        combo._fadeAnim = None

        # 绑定容器
        container = combo.view().window()
        container._fadeCombo = combo
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.installEventFilter(self)

        # 保存原始方法
        origShow = combo.showPopup
        origHide = combo.hidePopup
        ctrl = self

        def _showPopup():
            ctrl._stopAnim(combo)
            combo._fadeClosing = False
            ctrl._opacityEffect(combo.view()).setOpacity(0.0)
            origShow()

        def _hidePopup():
            if combo._fadeClosing:
                return
            if not container.isVisible():
                ctrl._opacityEffect(combo.view()).setOpacity(1.0)
                origHide()
                return
            combo._fadeClosing = True
            ctrl._animate(combo, 0.0, QEasingCurve.Type.InCubic,
                          lambda: ctrl._finishHide(combo, origHide))

        combo.showPopup = _showPopup
        combo.hidePopup = _hidePopup

    # ---- 事件过滤 ----

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            # 移除 Qt 每次弹出时自动添加的 QGraphicsDropShadowEffect
            if obj.graphicsEffect():
                obj.setGraphicsEffect(None)
            # 延迟一帧启动淡入，确保弹层已完成布局
            QTimer.singleShot(0, lambda c=obj: self._fadeIn(c))
        elif event.type() == QEvent.Type.Hide:
            # 安全网：弹层被外部关闭时重置状态
            combo = getattr(obj, "_fadeCombo", None)
            if combo is not None:
                combo._fadeClosing = False
                self._stopAnim(combo)
                self._opacityEffect(combo.view()).setOpacity(1.0)
        return False

    # ---- 内部方法 ----

    def _opacityEffect(self, view) -> QGraphicsOpacityEffect:
        effect = view.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(view)
            effect.setOpacity(1.0)
            view.setGraphicsEffect(effect)
        return effect

    def _stopAnim(self, combo: QComboBox):
        anim = combo._fadeAnim
        if anim is not None:
            combo._fadeAnim = None
            anim.stop()

    def _animate(self, combo: QComboBox, endVal: float,
                 curve: QEasingCurve.Type, onDone=None):
        self._stopAnim(combo)
        effect = self._opacityEffect(combo.view())
        anim = QPropertyAnimation(effect, b"opacity", combo)
        anim.setDuration(self._DURATION)
        anim.setStartValue(effect.opacity())
        anim.setEndValue(endVal)
        anim.setEasingCurve(curve)
        combo._fadeAnim = anim

        def finished():
            if combo._fadeAnim is not anim:
                return
            combo._fadeAnim = None
            if onDone:
                onDone()

        anim.finished.connect(finished)
        anim.start()

    def _fadeIn(self, container):
        combo = getattr(container, "_fadeCombo", None)
        if combo is None or combo._fadeClosing or not container.isVisible():
            return
        # 移除容器自身的阴影（Qt 可能在 show 后再次添加）
        if container.graphicsEffect():
            container.setGraphicsEffect(None)
        self._animate(combo, 1.0, QEasingCurve.Type.OutCubic)

    def _finishHide(self, combo: QComboBox, origHide):
        combo._fadeClosing = False
        self._opacityEffect(combo.view()).setOpacity(1.0)
        origHide()


class _ToolTipShadowFilter(QObject):
    """QToolTip 全局事件过滤器：移除 Windows 原生阴影 + 修复内边距。

    Windows 下 QToolTip 的投影阴影由窗口类样式 CS_DROPSHADOW 产生，
    QSS 无法控制，必须通过 Win32 API SetClassLongPtrW 移除。

    Qt 对 QTipLabel 的 QSS padding 映射存在 bug：padding 被映射到
    QLabel.margin（单一值，四个方向相同），垂直 padding 基本丢失，
    且 frameWidth 膨胀到 padding+border 之和。因此 QSS 中不应设置
    padding，改由代码精确控制。

    QLabel.sizeHint 公式（margin=0, indent=0）：
      width  = textWidth + cm.left + cm.right
      height = textHeight + cm.top + cm.bottom + 1  (1px leading)
    border 画在 contentsMargins 区域内（不额外占空间）。
    但 leading 会使 CR 高度比 textHeight 多 1px，导致文字垂直偏移
    （AlignVCenter 居中时上方多出 0.5px 空白），上下视觉不对称。
    解决方案：用 setFixedSize 强制 CR 高度 = textHeight，消除偏移；
    此时 cm=(7, 3, 7, 3) 即可实现视觉上 L=7 R=7 T=3+1(border)=4
    B=3+1(border)=4 的对称效果（含 border 视觉间距 4:8）。

    必须同时监听 Show 和 StyleChange：
    - Show：首次展示 / hide后重新展示
    - StyleChange：直接切换 tooltip 文本时，Qt 会重新应用样式，
      重置 contentsMargins，但不触发 Show 事件
    """

    _CS_DROPSHADOW = 0x00020000
    _GCLP_STYLE = -26  # GetClassLongPtrW index
    _shadowRemoved = False  # 类级别只需移除一次
    _filterRegistered = False  # 事件过滤器全局注册标记

    # 目标 contentsMargins：L=7 T=3 R=7 B=3
    # CR 高度 = textHeight（消除垂直偏移），视觉上 L=7 R=7 T=3+1(border)=4
    # B=3+1(border)=4，即含 border 视觉间距 4:8
    _CM = (7, 3, 7, 3)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if not isinstance(obj, QWidget):
            return False

        et = event.type()
        if et not in (QEvent.Type.Show, QEvent.Type.StyleChange):
            return False

        className = obj.metaObject().className()
        if className not in ("QTipLabel", "QToolTip"):
            return False

        # ---- 修复内边距 ----
        cl, ct, cr, cb = self._CM

        # 仅在值实际需要改变时才设置（避免触发 StyleChange 无限递归）
        if obj.margin() != 0:
            obj.setMargin(0)
        if obj.indent() != 0:
            obj.setIndent(0)

        cm = obj.contentsMargins()
        if (cm.left(), cm.top(), cm.right(), cm.bottom()) != (cl, ct, cr, cb):
            obj.setContentsMargins(cl, ct, cr, cb)

        # 精确设置窗口尺寸：用 setFixedSize 锁定，防止 Qt 内部布局自动调整
        #   width  = textWidth + cm.left + cm.right
        #   height = textHeight + cm.top + cm.bottom  (不加 leading，消除垂直偏移)
        fm = QFontMetrics(obj.font())
        text = obj.text()
        if "\n" in text:
            # 多行文本：使用 boundingRect 计算换行后的实际尺寸
            br = fm.boundingRect(0, 0, 0, 0, Qt.TextFlag.TextExpandTabs, text)
            target_w = br.width() + cl + cr
            target_h = br.height() + ct + cb
        else:
            target_w = fm.horizontalAdvance(text) + cl + cr
            target_h = fm.height() + ct + cb
        target_size = QSize(target_w, target_h)
        if obj.size() != target_size:
            obj.setFixedSize(target_size)

        # 移除 Windows 原生阴影（类级别，只需一次）
        if et == QEvent.Type.Show and not self._shadowRemoved and sys.platform == "win32":
            try:
                hwnd = int(obj.winId())
                gcl = ctypes.windll.user32.GetClassLongPtrW
                scl = ctypes.windll.user32.SetClassLongPtrW
                classStyle = gcl(hwnd, self._GCLP_STYLE)
                if classStyle & self._CS_DROPSHADOW:
                    scl(hwnd, self._GCLP_STYLE,
                        classStyle & ~self._CS_DROPSHADOW)
                    _ToolTipShadowFilter._shadowRemoved = True
            except Exception:
                pass

        return False


# 模块级单例
_noScrollFilter = _NoScrollFilter()
_comboFadeFilter = _ComboPopupFadeFilter()
_toolTipShadowFilter = _ToolTipShadowFilter()


def polishInputWidgets(parent: QWidget):
    """统一修饰 parent 下的输入控件（QSpinBox / QDoubleSpinBox / QComboBox）

    1. 安装滚轮过滤器：控件未聚焦时忽略滚轮，避免意外修改数值。
    2. 为 ComboBox 弹层安装淡入淡出动画 + 阴影移除。
    3. 为 QToolTip 移除 Qt 自动添加的阴影效果（仅注册一次）。
    """

    for child in (parent.findChildren(QSpinBox)
                  + parent.findChildren(QDoubleSpinBox)
                  + parent.findChildren(QComboBox)):
        child.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        child.installEventFilter(_noScrollFilter)

    for combo in parent.findChildren(QComboBox):
        _comboFadeFilter.installOn(combo)

    # 注册 QToolTip 阴影移除过滤器（仅注册一次）
    app = QApplication.instance()
    if app is not None and not _ToolTipShadowFilter._filterRegistered:
        _ToolTipShadowFilter._filterRegistered = True
        app.installEventFilter(_toolTipShadowFilter)


# ---- 多语言 & 参数展示工具 ----

def getLocalizedText(text: str | dict[str, str] | None, fallback: str = "") -> str:
    """从多语言文本中获取当前语言的文本。

    语言回退链：用户配置 → 系统语言 → 英文 → 首个条目 → fallback
    每层内部先精确匹配 langCode，再前缀匹配 langPrefix。

    Args:
        text: 单语言文本(str)或多语言映射(dict[str, str])或 None
        fallback: 当 text 为 None 或空 dict 时的回退文本

    Returns:
        当前语言的文本
    """
    match text:
        case None:
            return fallback
        case str():
            return text
        case dict():
            if not text:
                return fallback
            candidates = []
            userLang = configSettings.language
            if userLang and userLang != "system":
                candidates.append(userLang)
            candidates.append(QLocale().name())
            candidates.append("en_US")

            for langCode in candidates:
                if langCode in text:
                    return text[langCode]
                langPrefix = langCode.split("_")[0]
                for key, value in text.items():
                    if key.split("_")[0] == langPrefix:
                        return value

            return next(iter(text.values()))
        case _:
            return fallback


def formatParamValue(pd: ParamDef, value: object) -> str:
    """将参数值格式化为用于显示的字符串。

    Args:
        pd: ParamDef 参数声明
        value: 参数值
    """
    if pd.type == ParamType.BOOL:
        return "On" if value else "Off"
    if pd.type == ParamType.COORD:
        try:
            return f"({value[0]}, {value[1]})"
        except (TypeError, IndexError):
            return str(value)
    if pd.type == ParamType.CHOICE and pd.options:
        return _formatChoiceDisplay(pd.options, value)
    return str(value)


def _formatChoiceDisplay(options: list | dict, value: object) -> str:
    """从 options 中查找 value 对应的显示文本。"""
    if isinstance(options, dict):
        option = options.get(value)
        if option is not None:
            if isinstance(option, dict):
                return getLocalizedText(option, fallback=str(value))
            return str(option)
    return str(value)
