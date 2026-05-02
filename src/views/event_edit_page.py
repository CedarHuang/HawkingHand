"""
事件编辑页
==========
提供事件配置的创建/编辑表单，包含类型分段切换联动、
热键录入、字段动态显示/隐藏、表单验证等 UI 交互。
"""

import os

import keyboard
from PySide6.QtCore import Signal, QEvent, QObject, QCoreApplication, QLocale, Qt
from PySide6.QtGui import QTextDocument, QPalette
from PySide6.QtWidgets import (
    QWidget, QButtonGroup, QMessageBox,
    QSizePolicy, QSpacerItem,
    QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox,
    QLabel, QHBoxLayout, QFrame, QStyledItemDelegate, QStyle,
)
from core import common
from core import event_listener
from core.config import settings as configSettings
from core.models import ParamDef, ParamType
from core.scripts import ScriptCode, is_builtin
from ui.generated.ui_event_edit_page import Ui_EventEditPage
from views import _polishWidget, polishInputWidgets
from views.toggle_switch import ToggleSwitch


def prettifyHotkey(hotkey: str) -> str:
    """将热键字符串规范化并美化为用户友好的展示格式。

    内部调用 keyboard.get_hotkey_name() 完成排序与去重（去除 left/right 前缀、
    修饰键按 Ctrl→Alt→Shift→Win 排列），再将每个键名首字母大写，
    转为常见展示风格（如 'Ctrl+Shift+A'）。
    美化后的字符串仍可直接传给 keyboard.add_hotkey()，因为该库大小写不敏感。
    """
    if not hotkey:
        return hotkey
    normalized = keyboard.get_hotkey_name(hotkey.split("+"))
    return "+".join(part.strip().capitalize() for part in normalized.split("+"))


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
            # 构建候选语言列表
            candidates = []
            userLang = configSettings.language
            if userLang and userLang != "system":
                candidates.append(userLang)
            candidates.append(QLocale().name())  # 系统语言
            candidates.append("en_US")           # 英文兜底

            for langCode in candidates:
                # 精确匹配
                if langCode in text:
                    return text[langCode]
                # 前缀匹配（如 "zh" 匹配 "zh_CN"）
                langPrefix = langCode.split("_")[0]
                for key, value in text.items():
                    if key.split("_")[0] == langPrefix:
                        return value

            # 回退到第一个条目
            return next(iter(text.values()))
        case _:
            return fallback


class HotkeyRecorder(QObject):
    """热键录入辅助类，使用 keyboard 库捕获组合键并显示文本

    使用 keyboard 库进行录制，确保录制出的键名与 keyboard.add_hotkey() 一致。
    录制结果经过 prettifyHotkey() 规范化并美化：
    - 去除 left/right 前缀
    - 修饰键按固定顺序排列（Ctrl → Alt → Shift → Windows）
    - 每个键名首字母大写
    """

    def __init__(self, lineEdit):
        super().__init__(lineEdit)
        self._lineEdit = lineEdit
        self._recording = False
        self._currentKeys = set()

        # 设为只读，防止用户直接输入
        lineEdit.setReadOnly(True)
        # 安装事件过滤器，拦截鼠标点击和焦点事件
        lineEdit.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截输入框事件"""
        if obj is not self._lineEdit:
            return False

        # 点击输入框时开始录制
        if event.type() == QEvent.Type.MouseButtonPress and not self._recording:
            self._startRecording()
            return True

        # 录制中拦截所有键盘事件，交由 keyboard 库处理
        if self._recording and event.type() in (
            QEvent.Type.KeyPress, QEvent.Type.KeyRelease
        ):
            return True

        return False

    def _startRecording(self):
        """开始录入状态"""
        self._recording = True
        self._currentKeys = set()
        self._lineEdit.setText("...")
        self._lineEdit.setPlaceholderText(
            QCoreApplication.translate("EventEditPage", "Press a key combination...")
        )
        self._lineEdit.setProperty("recording", True)
        _polishWidget(self._lineEdit)

        # 停止事件监听器，避免录制时触发已有热键
        event_listener.stop()

        # 使用 keyboard 库进行录制
        keyboard.hook(self._onKeyboardEvent)

    def _stopRecording(self):
        """停止录入状态"""
        self._recording = False
        keyboard.unhook_all()

        # 美化最终结果
        currentText = self._lineEdit.text()
        if currentText and currentText != "...":
            self._lineEdit.setText(prettifyHotkey(currentText))

        self._lineEdit.setPlaceholderText(
            QCoreApplication.translate("EventEditPage", "Click here, then press a key combination...")
        )
        self._lineEdit.setProperty("recording", False)
        _polishWidget(self._lineEdit)

        # 恢复事件监听器
        event_listener.start()

    def _onKeyboardEvent(self, event):
        """keyboard 库的按键回调"""
        if event.event_type == keyboard.KEY_DOWN:
            self._currentKeys.add(event.name)
            # 实时显示时也进行美化排序
            self._lineEdit.setText(
                prettifyHotkey("+".join(self._currentKeys))
            )
        elif event.event_type == keyboard.KEY_UP:
            self._currentKeys.discard(event.name)
            if len(self._currentKeys) == 0:
                self._stopRecording()


class HtmlDelegate(QStyledItemDelegate):
    """使用 HTML 渲染 Combo 下拉列表项，同时保留原生的悬停/选中样式。"""

    _DISPLAY_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = QTextDocument()

    def _html(self, option, index) -> str:
        fg = option.palette.color(QPalette.ColorRole.Text).name()
        display = index.data(self._DISPLAY_ROLE)
        script = index.data(Qt.ItemDataRole.UserRole)
        dim = option.palette.color(QPalette.ColorRole.AlternateBase).name()
        badge = ''
        if is_builtin(script or ''):
            label = QCoreApplication.translate("EventEditPage", "Built-in")
            badge = f' <span style="color:{dim};"> [{label}]</span>'
        if display and script and display != script:
            return f'<span style="color:{fg};">{display}</span> <span style="color:{dim};">: {script}</span>{badge}'
        return f'<span style="color:{fg};">{index.data(Qt.ItemDataRole.DisplayRole)}</span>{badge}'

    def paint(self, painter, option, index):
        # 绘制选中/悬停背景（复用原生 QStyle 绘制）
        painter.save()
        style = option.widget.style() if option.widget else None
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.restore()

        self._doc.setDefaultFont(option.font)
        self._doc.setHtml(self._html(option, index))
        self._doc.setTextWidth(option.rect.width())
        # 文本区域与原生 item 对齐
        textRect = option.rect.adjusted(4, 2, -4, -2)
        painter.save()
        painter.translate(textRect.topLeft())
        painter.setClipRect(textRect.translated(-textRect.topLeft()))
        self._doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):
        self._doc.setDefaultFont(option.font)
        self._doc.setHtml(self._html(option, index))
        size = self._doc.size().toSize()
        size.setHeight(max(size.height() + 4, 28))
        return size


class EventEditPage(QWidget):
    """事件编辑页面"""

    # 信号定义
    backRequested = Signal()              # 请求返回列表页
    saveRequested = Signal(dict)          # 请求保存（传递表单数据字典）

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.ui = Ui_EventEditPage()
        self.ui.setupUi(self)

        # 当前编辑模式：True=编辑已有事件, False=新建事件
        self._isEditing = False
        # 表单是否有修改
        self._isDirty = False
        # 批量设置表单时暂停脏追踪，避免程序化赋值误触发
        self._suspendDirtyTracking = False

        # ---- 类型分段按钮互斥组 ----
        self._typeGroup = QButtonGroup(self)
        self._typeGroup.setExclusive(True)
        self._typeGroup.addButton(self.ui.typeBtnToggle)
        self._typeGroup.addButton(self.ui.typeBtnHold)
        self._typeGroup.buttonClicked.connect(self._onTypeChanged)

        # 按钮 → 类型名称映射
        self._btnTypeMap = {
            self.ui.typeBtnToggle: "Toggle",
            self.ui.typeBtnHold: "Hold",
        }

        # ---- 热键录入 ----
        self._hotkeyRecorder = HotkeyRecorder(self.ui.hotkeyInput)

        # ---- 按钮信号 ----
        self.ui.btnBack.clicked.connect(self._onBackClicked)
        self.ui.btnCancel.clicked.connect(self._onBackClicked)
        self.ui.btnSave.clicked.connect(self._onSaveClicked)
        self.ui.btnOpenScriptsDir.clicked.connect(self._onOpenScriptsDir)

        # ---- 表单变更追踪 ----
        self.ui.hotkeyInput.textChanged.connect(self._markDirty)
        self.ui.scopeInput.textChanged.connect(self._markDirty)

        # ---- 脚本参数动态渲染 ----
        self._currentParamDefs: list[ParamDef] = []  # 当前脚本的参数声明列表
        self._paramWidgets: dict[str, QWidget] = {}  # 参数名 → 控件的映射
        self._scriptArgsToRestore: dict | None = None  # 待还原的脚本参数值

        # ---- 修饰输入控件（滚轮过滤 + 弹出圆角修复）----
        polishInputWidgets(self)

        # ---- 脚本选择变更时动态渲染参数 ----
        self.ui.scriptCombo.currentIndexChanged.connect(self._onScriptChanged)


    # ---- 公共方法 ----

    def resetForm(self, isEditing: bool = False):
        """重置表单为初始状态

        Args:
            isEditing: True=编辑模式, False=新建模式
        """
        self._suspendDirtyTracking = True
        self._isEditing = isEditing

        # 更新标题
        self.ui.pageTitle.setText(
            self.tr("Edit Event") if isEditing else self.tr("New Event")
        )

        # 重置所有字段
        self.ui.typeBtnToggle.setChecked(True)
        self.ui.hotkeyInput.clear()
        self.ui.scopeInput.clear()

        # 清除错误提示样式
        self._clearErrors()

        # 清除脚本参数状态
        self._clearScriptParams()
        self._scriptArgsToRestore = None

        self._isDirty = False
        self._suspendDirtyTracking = False

    def setFormData(self, data: dict):
        """用数据填充表单

        Args:
            data: 包含 type, hotkey, scope, script, script_args 的字典
        """
        self._suspendDirtyTracking = True
        self._isEditing = True
        self.ui.pageTitle.setText(self.tr("Edit Event"))

        eventType = data.get("type", "Toggle")

        # 选中对应的类型按钮
        typeButtons = {
            "Toggle": self.ui.typeBtnToggle,
            "Hold": self.ui.typeBtnHold,
        }
        btn = typeButtons.get(eventType)
        if btn:
            btn.setChecked(True)

        # 填充字段
        self.ui.hotkeyInput.setText(data.get("hotkey", ""))

        # scope 为 "*" 或空时显示为空，让 placeholder 提示用户格式
        scopeVal = data.get("scope", "")
        self.ui.scopeInput.setText("" if scopeVal in ("", "*") else scopeVal)

        # 设置脚本选择
        scriptName = data.get("script", "__click__")
        self._scriptArgsToRestore = data.get("script_args")
        idx = self.ui.scriptCombo.findData(scriptName)
        if idx >= 0:
            self.ui.scriptCombo.blockSignals(True)
            self.ui.scriptCombo.setCurrentIndex(idx)
            self.ui.scriptCombo.blockSignals(False)
        else:
            self._scriptArgsToRestore = None
        self.refreshScriptParams()

        self._isDirty = False
        self._suspendDirtyTracking = False

    def setScriptList(self, scripts: list[tuple[str, str]]):
        """设置脚本下拉列表

        Args:
            scripts: (script_name, display_name) 元组列表
        """
        self.ui.scriptCombo.blockSignals(True)
        self.ui.scriptCombo.clear()
        if not hasattr(self, '_htmlDelegate'):
            self._htmlDelegate = HtmlDelegate(self.ui.scriptCombo)
            self.ui.scriptCombo.setItemDelegate(self._htmlDelegate)
        for script_name, display_name in scripts:
            if isinstance(display_name, dict):
                display_name = getLocalizedText(display_name, fallback=script_name)
            if display_name != script_name:
                plain = f'{display_name} : {script_name}'
            else:
                plain = display_name
            idx = self.ui.scriptCombo.count()
            self.ui.scriptCombo.addItem(plain, script_name)
            self.ui.scriptCombo.setItemData(idx, display_name, HtmlDelegate._DISPLAY_ROLE)
        self.ui.scriptCombo.blockSignals(False)

    # ---- 内部方法 ----

    def _onTypeChanged(self, button):
        """类型分段按钮切换回调"""
        self._markDirty()

    def _onBackClicked(self):
        """返回/取消按钮点击"""
        if self._isDirty:
            reply = QMessageBox.question(
                self,
                self.tr("Discard Changes"),
                self.tr("Discard unsaved changes?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.backRequested.emit()

    def _onSaveClicked(self):
        """保存按钮点击"""
        # 验证必填字段
        if not self._validate():
            return

        # 收集表单数据
        checkedBtn = self._typeGroup.checkedButton()
        typeName = self._btnTypeMap.get(checkedBtn, "Toggle")

        data = {
            "type": typeName,
            "hotkey": self.ui.hotkeyInput.text().strip(),
            "scope": self.ui.scopeInput.text().strip(),
            "script": self.ui.scriptCombo.currentData(),
            "script_args": self._collectScriptArgs(),
        }

        self._isDirty = False
        self.saveRequested.emit(data)

    def _validate(self) -> bool:
        """验证必填字段，返回是否通过"""
        valid = True
        self._clearErrors()

        # 热键不能为空
        if not self.ui.hotkeyInput.text().strip():
            self.ui.hotkeyInput.setProperty("hasError", True)
            _polishWidget(self.ui.hotkeyInput)
            self.ui.hotkeyInput.setPlaceholderText(self.tr("⚠ Hotkey is required"))
            valid = False

        # 脚本不能为空
        if not self.ui.scriptCombo.currentData():
            valid = False

        return valid

    def _clearErrors(self):
        """清除所有字段的错误提示样式"""
        self.ui.hotkeyInput.setProperty("hasError", False)
        _polishWidget(self.ui.hotkeyInput)
        self.ui.hotkeyInput.setPlaceholderText(self.tr("Click here, then press a key combination..."))

    def _markDirty(self, *_args):
        """标记表单已修改（批量设置期间自动跳过）"""
        if not self._suspendDirtyTracking:
            self._isDirty = True

    # ---- 脚本参数动态渲染 ----

    def _onScriptChanged(self, index: int):
        """脚本选择变更回调：重新渲染参数控件"""
        self.refreshScriptParams()
        self._markDirty()

    def refreshScriptParams(self):
        """根据当前选中的脚本，提取参数声明并动态渲染参数控件"""
        scriptName = self.ui.scriptCombo.currentData() or ""
        if not scriptName:
            self._clearScriptParams()
            self.ui.scriptParamsContainer.setVisible(False)
            self.ui.paramsGroup.setVisible(False)
            return

        # 通过提取沙箱获取参数声明
        scriptCode = ScriptCode.get_by_name(scriptName)
        paramDefs = scriptCode.get_param_defs()

        self._clearScriptParams()
        # 规范化：CHOICE 空 options 降级为 STR，确保声明与控件类型一致
        self._currentParamDefs = [
            ParamDef(name=pd.name, type=ParamType.STR, default=pd.default,
                     label=pd.label, description=pd.description, options=None)
            if pd.type == ParamType.CHOICE and not pd.options else pd
            for pd in paramDefs
        ]

        if not self._currentParamDefs:
            # 脚本无参数，隐藏整组
            self.ui.scriptParamsContainer.setVisible(False)
            self.ui.paramsGroup.setVisible(False)
            return

        # 有参数：显示 paramsGroup 和脚本参数容器
        self.ui.paramsGroup.setVisible(True)
        self.ui.scriptParamsContainer.setVisible(True)

        # 按参数声明顺序创建控件
        scriptLayout = self.ui.scriptParamsLayout
        for paramDef in self._currentParamDefs:
            row = self._createParamRow(paramDef)
            scriptLayout.addWidget(row)

        # 为动态创建的输入控件安装修饰（滚轮过滤 + ComboBox 弹出淡入）
        polishInputWidgets(self.ui.scriptParamsContainer)

        # 如果有待还原的参数值，进行还原
        if self._scriptArgsToRestore is not None:
            self._restoreScriptArgs(self._scriptArgsToRestore)
            self._scriptArgsToRestore = None

    def _clearScriptParams(self):
        """清除所有动态创建的脚本参数控件"""
        layout = self.ui.scriptParamsLayout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._currentParamDefs = []
        self._paramWidgets = {}

    def _createParamRow(self, paramDef: ParamDef) -> QFrame:
        """根据 ParamDef 创建一个参数行（标签 + 控件）

        行布局与 .ui 中定义的 hotkeyRow / scopeRow / scriptRow 等保持一致：
        QFrame(零边距) + QHBoxLayout(spacing=12) + QLabel(role=fieldLabel, minWidth=60) + 控件

        Args:
            paramDef: 参数声明元数据

        Returns:
            包含标签和输入控件的 QFrame
        """
        row = QFrame()
        row.setObjectName("paramRow")
        hLayout = QHBoxLayout(row)
        hLayout.setContentsMargins(0, 0, 0, 0)
        hLayout.setSpacing(12)

        # 标签：与 .ui 中的 fieldLabel 一致（role + minWidth）
        labelText = getLocalizedText(paramDef.label, fallback=paramDef.name)
        label = QLabel(labelText)
        label.setMinimumWidth(60)
        label.setProperty("role", "fieldLabel")
        hLayout.addWidget(label)

        # 根据类型创建控件
        widget = self._createParamWidget(paramDef)
        self._paramWidgets[paramDef.name] = widget

        if paramDef.type == ParamType.BOOL:
            # bool 类型：开关在左侧（不加 stretch），右侧加 spacer 推到左边
            hLayout.addWidget(widget)
            spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            hLayout.addSpacerItem(spacer)
        else:
            # 其他类型：控件占据剩余空间
            hLayout.addWidget(widget, 1)

        # 描述文本作为 tooltip，而非独立标签，保持行布局一致性
        descText = getLocalizedText(paramDef.description)
        if descText:
            label.setToolTip(descText)
            widget.setToolTip(descText)

        return row

    def _createParamWidget(self, paramDef: ParamDef) -> QWidget:
        """根据参数类型创建对应的输入控件

        Args:
            paramDef: 参数声明元数据

        Returns:
            输入控件实例
        """
        match paramDef.type:
            case ParamType.CHOICE: return self._createChoiceWidget(paramDef)
            case ParamType.COORD:  return self._createCoordWidget(paramDef)
            case ParamType.HOTKEY: return self._createHotkeyWidget(paramDef)
            case ParamType.BOOL:   return self._createBoolWidget(paramDef)
            case ParamType.INT:    return self._createIntWidget(paramDef)
            case ParamType.FLOAT:  return self._createFloatWidget(paramDef)
            case _:                return self._createStrWidget(paramDef)

    def _createChoiceWidget(self, paramDef: ParamDef) -> QComboBox:
        """创建 choice 类型下拉框

        options 支持三种形式：
        - list[T]: 值即显示文本
        - dict[T, str]: 值 → 显示文本
        - dict[T, dict[str, str]]: 值 → 语言 → 显示文本
        """
        combo = QComboBox()
        combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )
        combo.setMinimumHeight(32)

        options = paramDef.options

        if isinstance(options, list):
            for value in options:
                displayText = str(value) if not isinstance(value, str) else value
                combo.addItem(displayText, value)
        elif isinstance(options, dict):
            for key, val in options.items():
                if isinstance(val, dict):
                    # 多语言：val 是 {lang: text}
                    displayText = getLocalizedText(val, fallback=str(key))
                else:
                    # 单语言映射
                    displayText = str(val)
                combo.addItem(displayText, key)

        # 设置默认选中项
        default = paramDef.default
        defaultIdx = combo.findData(default)
        if defaultIdx >= 0:
            combo.setCurrentIndex(defaultIdx)
        else:
            # 防御性处理：理论上 _effective_default 已将 default 修正为 options 中的有效值，
            # 此分支不应触发；若触发则选中第一项兜底
            if combo.count() > 0:
                combo.setCurrentIndex(0)

        combo.currentIndexChanged.connect(self._markDirty)
        return combo

    def _createCoordWidget(self, paramDef: ParamDef) -> QFrame:
        """创建 coord 类型坐标输入控件（X/Y 双数值框）"""
        container = QFrame()
        container.setObjectName("coordContainer")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        default = paramDef.default
        try:
            defaultX, defaultY = int(default[0]), int(default[1])
        except (ValueError, TypeError, IndexError):
            defaultX, defaultY = -1, -1

        xLabel = QLabel("X")
        xLabel.setObjectName("xLabel")
        layout.addWidget(xLabel)

        spinX = QSpinBox()
        spinX.setMinimumSize(80, 32)
        spinX.setRange(-1, 99999)
        spinX.setValue(defaultX)
        spinX.valueChanged.connect(self._markDirty)
        layout.addWidget(spinX)

        yLabel = QLabel("Y")
        yLabel.setObjectName("yLabel")
        layout.addWidget(yLabel)

        spinY = QSpinBox()
        spinY.setMinimumSize(80, 32)
        spinY.setRange(-1, 99999)
        spinY.setValue(defaultY)
        spinY.valueChanged.connect(self._markDirty)
        layout.addWidget(spinY)

        hint = QLabel(QCoreApplication.translate(
            "EventEditPage", "(-1 = current position)"))
        hint.setObjectName("positionHint")
        layout.addWidget(hint)

        layout.addStretch()

        container._spinX = spinX
        container._spinY = spinY
        return container

    def _createHotkeyWidget(self, paramDef: ParamDef) -> QLineEdit:
        """创建 hotkey 类型热键录入控件"""
        lineEdit = QLineEdit()
        lineEdit.setMinimumHeight(32)
        lineEdit.setText(str(paramDef.default))
        lineEdit.textChanged.connect(self._markDirty)
        # HotkeyRecorder 由 lineEdit 持有（parent），负责拦截点击/按键、录制组合键
        HotkeyRecorder(lineEdit)
        return lineEdit

    def _createBoolWidget(self, paramDef: ParamDef) -> ToggleSwitch:
        """创建 bool 类型开关"""
        switch = ToggleSwitch()
        switch.setFixedSize(40, 22)
        switch.setChecked(bool(paramDef.default))
        switch.toggled.connect(self._markDirty)
        return switch

    _INT32_MIN, _INT32_MAX = -(1 << 31), (1 << 31) - 1

    def _createIntWidget(self, paramDef: ParamDef) -> QSpinBox:
        """创建 int 类型数值框"""
        spinBox = QSpinBox()
        spinBox.setMinimumHeight(32)
        spinBox.setRange(self._INT32_MIN, self._INT32_MAX)
        spinBox.setValue(int(paramDef.default))
        spinBox.valueChanged.connect(self._markDirty)
        return spinBox

    def _createFloatWidget(self, paramDef: ParamDef) -> QDoubleSpinBox:
        """创建 float 类型数值框"""
        spinBox = QDoubleSpinBox()
        spinBox.setMinimumHeight(32)
        spinBox.setRange(-1e9, 1e9)  # 整数9位 + 小数6位 = 15位有效数字，匹配double精度
        spinBox.setDecimals(6)
        spinBox.setValue(float(paramDef.default))
        spinBox.valueChanged.connect(self._markDirty)
        return spinBox

    def _createStrWidget(self, paramDef: ParamDef) -> QLineEdit:
        """创建 str 类型输入框"""
        lineEdit = QLineEdit()
        lineEdit.setMinimumHeight(32)
        lineEdit.setText(str(paramDef.default))
        lineEdit.textChanged.connect(self._markDirty)
        return lineEdit

    def _collectScriptArgs(self) -> dict[str, int | float | str | bool | list]:
        """从参数控件中收集所有参数值

        Returns:
            参数名 → 参数值的映射字典
        """
        result = {}
        for paramDef in self._currentParamDefs:
            widget = self._paramWidgets.get(paramDef.name)
            if widget is None:
                continue

            value = self._getParamValue(widget, paramDef)
            if value is None:
                continue
            result[paramDef.name] = value
        return result

    def _getParamValue(self, widget: QWidget, paramDef: ParamDef) -> int | float | str | bool | list | None:
        """从单个控件中获取参数值

        Args:
            widget: 输入控件
            paramDef: 参数声明

        Returns:
            参数值，空 QComboBox 时返回 None 表示跳过
        """
        if isinstance(widget, QComboBox):
            if widget.count() == 0:
                return None
            return widget.currentData()
        elif paramDef.type == ParamType.COORD:
            return [widget._spinX.value(), widget._spinY.value()]
        elif isinstance(widget, ToggleSwitch):
            return widget.isChecked()
        elif isinstance(widget, QSpinBox):
            return widget.value()
        elif isinstance(widget, QDoubleSpinBox):
            return widget.value()
        elif isinstance(widget, QLineEdit):
            return widget.text()
        return paramDef.default

    def _restoreScriptArgs(self, scriptArgs: dict):
        """将保存的参数值还原到参数控件

        忽略已移除的参数，新增参数使用默认值填充。
        类型转换失败时使用默认值。

        Args:
            scriptArgs: 参数名 → 参数值的映射字典
        """
        for paramDef in self._currentParamDefs:
            if paramDef.name not in scriptArgs:
                continue

            widget = self._paramWidgets.get(paramDef.name)
            if widget is None:
                continue

            savedValue = scriptArgs[paramDef.name]

            try:
                if isinstance(widget, QComboBox):
                    idx = widget.findData(savedValue)
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                    # 值不在 options 中时保持默认
                elif paramDef.type == ParamType.COORD:
                    if isinstance(savedValue, (list, tuple)) and len(savedValue) >= 2:
                        widget._spinX.setValue(int(savedValue[0]))
                        widget._spinY.setValue(int(savedValue[1]))
                elif isinstance(widget, ToggleSwitch):
                    widget.setChecked(bool(savedValue))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(savedValue))
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(savedValue))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(savedValue))
            except (ValueError, TypeError):
                # 类型转换失败，保持默认值
                pass

    @staticmethod
    def _onOpenScriptsDir():
        """打开脚本目录"""
        os.startfile(common.scripts_path())
