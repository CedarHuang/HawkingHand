# ============================================================
# 内置脚本：按住（Press）
#
# 每次执行在 按下/释放 之间切换。
# 变量在 ScriptContext 中跨执行保持状态，
# 因此不需要 global 或外部存储。
# ============================================================

if init():
    button = params("button", "mouse_left",
        label={"en_US": "Button", "zh_CN": "按键"},
        options={
            "mouse_left":  {"en_US": "Left",  "zh_CN": "左键"},
            "mouse_right": {"en_US": "Right", "zh_CN": "右键"},
        })

    pos = params("position", [-1, -1],
        label={"en_US": "Position", "zh_CN": "坐标"})

    # is_down 在 init() 内首次初始化为 False，
    # 后续执行时复用上下文中的值，实现 按下/释放 交替
    is_down = False

# ---- 交替 down / up ----
if is_down:
    up(button, *pos)
else:
    down(button, *pos)
is_down = not is_down
