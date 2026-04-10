"""
事件编辑页
==========
提供事件配置的创建/编辑表单，包含类型分段切换联动、
热键录入、字段动态显示/隐藏、表单验证等 UI 交互。
"""

import os

import keyboard
from PySide6.QtCore import Signal, QEvent, QObject, QCoreApplication
from PySide6.QtWidgets import (
    QWidget, QButtonGroup, QMessageBox,
)

from core import common
from core import event_listener
from core.input_backend import MOUSE_LEFT, MOUSE_RIGHT
from ui.generated.ui_event_edit_page import Ui_EventEditPage
from views import _polishWidget, polishInputWidgets


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
        self._typeGroup.addButton(self.ui.typeBtnClick)
        self._typeGroup.addButton(self.ui.typeBtnPress)
        self._typeGroup.addButton(self.ui.typeBtnMulti)
        self._typeGroup.addButton(self.ui.typeBtnScript)
        self._typeGroup.buttonClicked.connect(self._onTypeChanged)

        # 按钮 → 类型名称映射
        self._btnTypeMap = {
            self.ui.typeBtnClick: "Click",
            self.ui.typeBtnPress: "Press",
            self.ui.typeBtnMulti: "Multi",
            self.ui.typeBtnScript: "Script",
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
        self.ui.triggerOnReleaseCheck.toggled.connect(self._markDirty)
        self.ui.buttonCombo.currentTextChanged.connect(self._markDirty)
        self.ui.scriptCombo.currentTextChanged.connect(self._markDirty)
        self.ui.positionX.valueChanged.connect(self._markDirty)
        self.ui.positionY.valueChanged.connect(self._markDirty)
        self.ui.intervalInput.valueChanged.connect(self._markDirty)
        self.ui.clicksInput.valueChanged.connect(self._markDirty)

        # ---- 为预设鼠标按钮选项设置内部值（不受翻译影响） ----
        self._initButtonComboData()

        # ---- 修饰输入控件（滚轮过滤 + 弹出圆角修复）----
        polishInputWidgets(self)

        # ---- 初始状态：Click 类型 ----
        self._applyType("Click")

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
        self.ui.typeBtnClick.setChecked(True)
        self.ui.hotkeyInput.clear()
        self.ui.buttonCombo.setCurrentIndex(0)
        self.ui.scopeInput.clear()
        self.ui.triggerOnReleaseCheck.setChecked(False)
        self.ui.positionX.setValue(-1)
        self.ui.positionY.setValue(-1)
        self.ui.intervalInput.setValue(100)
        self.ui.clicksInput.setValue(-1)

        # 清除错误提示样式
        self._clearErrors()

        # 应用类型联动
        self._applyType("Click")

        self._isDirty = False
        self._suspendDirtyTracking = False

    def setFormData(self, data: dict):
        """用数据填充表单

        Args:
            data: 包含 type, hotkey, target, scope, posX, posY, interval, clicks 的字典
        """
        self._suspendDirtyTracking = True
        self._isEditing = True
        self.ui.pageTitle.setText(self.tr("Edit Event"))

        eventType = data.get("type", "Click")

        # 选中对应的类型按钮
        typeButtons = {
            "Click": self.ui.typeBtnClick,
            "Press": self.ui.typeBtnPress,
            "Multi": self.ui.typeBtnMulti,
            "Script": self.ui.typeBtnScript,
        }
        btn = typeButtons.get(eventType)
        if btn:
            btn.setChecked(True)
        self._applyType(eventType)

        # 填充字段
        self.ui.hotkeyInput.setText(data.get("hotkey", ""))
        self._setButtonComboValue(data.get("target", MOUSE_LEFT))

        # scope 为 "*" 或空时显示为空，让 placeholder 提示用户格式
        scopeVal = data.get("scope", "")
        self.ui.scopeInput.setText("" if scopeVal in ("", "*") else scopeVal)

        self.ui.triggerOnReleaseCheck.setChecked(data.get("trigger_on_release", False))

        self.ui.positionX.setValue(data.get("posX", -1))
        self.ui.positionY.setValue(data.get("posY", -1))
        self.ui.intervalInput.setValue(data.get("interval", 100))
        self.ui.clicksInput.setValue(data.get("clicks", -1))

        # 如果是脚本类型，设置脚本选择
        if eventType == "Script":
            scriptName = data.get("script", "")
            idx = self.ui.scriptCombo.findText(scriptName)
            if idx >= 0:
                self.ui.scriptCombo.setCurrentIndex(idx)

        self._isDirty = False
        self._suspendDirtyTracking = False

    def setScriptList(self, scripts: list[str]):
        """设置脚本下拉列表

        Args:
            scripts: 脚本名称列表（不含 .py 后缀）
        """
        self._suspendDirtyTracking = True
        self.ui.scriptCombo.clear()
        self.ui.scriptCombo.addItems(scripts)
        self._suspendDirtyTracking = False

    # ---- 内部方法 ----

    def _onTypeChanged(self, button):
        """类型分段按钮切换回调"""
        typeName = self._btnTypeMap.get(button, "Click")
        self._applyType(typeName)
        self._markDirty()

    def _applyType(self, typeName: str):
        """根据类型名称显示/隐藏对应字段"""
        isScript = typeName == "Script"
        isMulti = typeName == "Multi"

        # Script 类型显示脚本选择，隐藏按键选择
        self.ui.scriptRow.setVisible(isScript)
        self.ui.buttonFieldLabel.setVisible(not isScript)
        self.ui.buttonCombo.setVisible(not isScript)

        # paramsGroup：Script 无参数，整组隐藏；其他类型显示
        self.ui.paramsGroup.setVisible(not isScript)
        # intervalRow / clicksRow 仅 Multi 类型可见
        self.ui.intervalRow.setVisible(isMulti)
        self.ui.clicksRow.setVisible(isMulti)

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
        typeName = self._btnTypeMap.get(checkedBtn, "Click")

        data = {
            "type": typeName,
            "hotkey": self.ui.hotkeyInput.text().strip(),
            "target": self._getButtonComboValue(),
            "scope": self.ui.scopeInput.text().strip(),
            "trigger_on_release": self.ui.triggerOnReleaseCheck.isChecked(),
            "posX": self.ui.positionX.value(),
            "posY": self.ui.positionY.value(),
            "interval": self.ui.intervalInput.value(),
            "clicks": self.ui.clicksInput.value(),
        }

        if typeName == "Script":
            data["script"] = self.ui.scriptCombo.currentText()

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

        # 按键/脚本不能为空
        checkedBtn = self._typeGroup.checkedButton()
        typeName = self._btnTypeMap.get(checkedBtn, "Click")

        if typeName == "Script":
            if not self.ui.scriptCombo.currentText().strip():
                valid = False
        else:
            if not self.ui.buttonCombo.currentText().strip():
                self.ui.buttonCombo.setProperty("hasError", True)
                _polishWidget(self.ui.buttonCombo)
                valid = False

        return valid

    def _clearErrors(self):
        """清除所有字段的错误提示样式"""
        self.ui.hotkeyInput.setProperty("hasError", False)
        _polishWidget(self.ui.hotkeyInput)
        self.ui.hotkeyInput.setPlaceholderText(self.tr("Click here, then press a key combination..."))

        self.ui.buttonCombo.setProperty("hasError", False)
        _polishWidget(self.ui.buttonCombo)

    def _markDirty(self, *_args):
        """标记表单已修改（批量设置期间自动跳过）"""
        if not self._suspendDirtyTracking:
            self._isDirty = True

    def _initButtonComboData(self):
        """为 buttonCombo 的预设选项设置 itemData（内部值），使其不受翻译影响

        .ui 文件中定义的顺序：index 0 = Left, index 1 = Right
        显示文本会被 Qt 翻译系统翻译（如 "左键"/"右键"），
        但 itemData 始终保持英文内部值，供数据层使用。
        """
        _BUTTON_DATA = [MOUSE_LEFT, MOUSE_RIGHT]
        for i, value in enumerate(_BUTTON_DATA):
            if i < self.ui.buttonCombo.count():
                self.ui.buttonCombo.setItemData(i, value)

    def _getButtonComboValue(self) -> str:
        """获取 buttonCombo 的内部值（优先取 itemData，回退到 currentText）

        对于预设的鼠标按钮选项，返回不受翻译影响的内部值（如 "mouse_left"）；
        对于用户手动输入的键盘按键名，返回输入的文本。

        注意：可编辑 QComboBox 中，用户修改输入框文本但未按回车时，
        currentIndex 仍指向旧的预设项，currentData() 会返回旧预设值。
        因此需要额外比较编辑框文本与当前选中项的显示文本是否一致。
        """
        idx = self.ui.buttonCombo.currentIndex()
        data = self.ui.buttonCombo.currentData()
        editText = self.ui.buttonCombo.currentText().strip()

        if data is not None and idx >= 0:
            # 当前 index 指向预设项，但编辑框文本可能已被用户修改
            itemText = self.ui.buttonCombo.itemText(idx)
            if editText == itemText:
                # 文本与预设项一致，返回内部值
                return str(data)
            # 文本已被用户修改（自定义输入但未按回车），返回实际输入
            return editText

        # 用户手动输入的键盘按键，无 itemData，直接返回文本
        return editText

    def _setButtonComboValue(self, value: str):
        """根据内部值设置 buttonCombo 的选中项

        优先通过 itemData 匹配预设选项；若未找到，则作为自定义文本设置。
        """
        idx = self.ui.buttonCombo.findData(value)
        if idx >= 0:
            self.ui.buttonCombo.setCurrentIndex(idx)
        else:
            # 自定义键盘按键名，直接设置文本
            self.ui.buttonCombo.setCurrentText(value)

    @staticmethod
    def _onOpenScriptsDir():
        """打开脚本目录"""
        os.startfile(common.scripts_path())
