# ============================================================
# 内置脚本：单击（Click）
# ============================================================

# ---- 参数声明 ----
# init() 首次调用返回 True，后续调用返回 False。
# 将 params() 放在 init() 内可以避免重复解析参数值，提升性能。

if init():
    # CHOICE 类型：options 支持三种格式
    #   list:             ["a", "b"]           — 值即显示文本
    #   dict[T, str]:     {"a": "选项A", ...}   — 值→显示文本
    #   dict[T, dict]:    {"a": {"en_US": ...}, ...}  — 值→多语言显示文本
    button = params("button", "mouse_left",
        label={"en_US": "Button", "zh_CN": "按键"},
        options={
            "mouse_left":  {"en_US": "Left",  "zh_CN": "左键"},
            "mouse_right": {"en_US": "Right", "zh_CN": "右键"},
        })

    # COORD 类型：默认值为 list 或 tuple 时自动推断，
    # 在事件编辑页显示 X/Y 双数值框。(-1, -1) = 当前鼠标位置
    pos = params("position", [-1, -1],
        label={"en_US": "Position", "zh_CN": "坐标"})

# ---- 执行逻辑 ----
click(button, *pos)
