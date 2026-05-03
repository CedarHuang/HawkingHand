# ============================================================
# 内置脚本：单击（Click）
#
# 这是最简单的脚本范例，展示了脚本的基本结构：
#   1. info()   — 声明脚本的展示名和描述（在脚本列表中显示）
#   2. params() — 声明可配置参数，系统自动在事件编辑页渲染控件
#   3. switch() — 声明参数可见性依赖
#   4. init()   — 首次执行时运行一次，用于声明参数
#   5. click()  — 执行单击
# ============================================================

if init():
    # ---- info: 脚本展示信息（在脚本列表和下拉框中显示）----
    info(
        name={"en_US": "Click", "zh_CN": "单击"},
        description={
            "en_US": "Performs a single click with mouse or keyboard.",
            "zh_CN": "执行一次鼠标或键盘单击。",
        })

    # ---- 参数声明 ----
    # CHOICE 类型：options 为 dict 时自动推断
    # 支持三种 options 格式：
    #   list:          ["a", "b"]           — 值即显示文本
    #   dict[T, str]:  {"a": "选项A", ...}   — 值→显示文本
    #   dict[T, dict]: {"a": {"en_US": ...}, ...}  — 值→多语言显示文本
    device = params("device", "mouse",
        label={"en_US": "Device", "zh_CN": "设备"},
        options={
            "mouse":    {"en_US": "Mouse",    "zh_CN": "鼠标"},
            "keyboard": {"en_US": "Keyboard", "zh_CN": "键盘"},
        })

    mouse_button = params("mouse_button", "mouse_left",
        label={"en_US": "Mouse Button", "zh_CN": "鼠标按键"},
        options={
            "mouse_left":  {"en_US": "Left",  "zh_CN": "左键"},
            "mouse_right": {"en_US": "Right", "zh_CN": "右键"},
        })

    # HOTKEY 类型：显式指定 type='hotkey'，在事件编辑页渲染热键录制器
    keyboard_key = params("keyboard_key", "", type="hotkey",
        label={"en_US": "Keyboard Key", "zh_CN": "键盘按键"})

    # COORD 类型：默认值为 list/tuple 时自动推断
    # 在事件编辑页渲染 X/Y 双数值框，(-1, -1) = 当前鼠标位置
    pos = params("position", [-1, -1],
        label={"en_US": "Position", "zh_CN": "坐标"})

    # switch() 声明参数可见性依赖：device 为 mouse 时显示 mouse_button 和 pos，
    # 为 keyboard 时显示 keyboard_key。未参与 switch 的参数始终可见。
    switch(device, {
        "mouse":    [mouse_button, pos],
        "keyboard": [keyboard_key],
    })

# *pos 解包坐标传入 click()；键盘按键时 click() 自动忽略坐标
target = mouse_button if device == "mouse" else keyboard_key
click(target, *pos)
