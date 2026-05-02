# ============================================================
# 内置脚本：单击（Click）
#
# 这是最简单的脚本范例，展示了脚本的基本结构：
#   1. info()   — 声明脚本的展示名和描述（在脚本列表中显示）
#   2. params() — 声明可配置参数，系统自动在事件编辑页渲染控件
#   3. init()   — 首次执行时运行一次，用于声明参数
#   4. click()  — 执行鼠标单击
# ============================================================

if init():
    # ---- info: 脚本展示信息（在脚本列表和下拉框中显示）----
    info(
        name={"en_US": "Click", "zh_CN": "单击"},
        description={
            "en_US": "Performs a single mouse click at the target position.",
            "zh_CN": "在目标位置执行一次鼠标单击。",
        })

    # ---- 参数声明 ----
    # CHOICE 类型：options 为 dict 时自动推断
    # 支持三种 options 格式：
    #   list:          ["a", "b"]           — 值即显示文本
    #   dict[T, str]:  {"a": "选项A", ...}   — 值→显示文本
    #   dict[T, dict]: {"a": {"en_US": ...}, ...}  — 值→多语言显示文本
    button = params("button", "mouse_left",
        label={"en_US": "Button", "zh_CN": "按键"},
        options={
            "mouse_left":  {"en_US": "Left",  "zh_CN": "左键"},
            "mouse_right": {"en_US": "Right", "zh_CN": "右键"},
        })

    # COORD 类型：默认值为 list/tuple 时自动推断
    # 在事件编辑页渲染 X/Y 双数值框，(-1, -1) = 当前鼠标位置
    pos = params("position", [-1, -1],
        label={"en_US": "Position", "zh_CN": "坐标"})

# *pos 解包坐标传入 click()
click(button, *pos)
