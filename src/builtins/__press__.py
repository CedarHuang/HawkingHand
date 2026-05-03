# ============================================================
# 内置脚本：按住（Press）
#
# 展示了变量跨执行持久化的机制：
#   - 每个事件拥有独立的上下文，变量在其中跨次保持
#   - 同一事件多次触发热键，在同一个上下文中执行脚本
#   - 因此 is_down 可以跨热键触发保持状态，实现 按下/释放 交替
#
# 执行流程：
#   第 1 次热键 → init()=True → 声明参数，is_down=False → down()
#   第 2 次热键 → init()=False → is_down=True → up()
#   第 3 次热键 → init()=False → is_down=False → down()
#   ...
# ============================================================

if init():
    info(
        name={"en_US": "Press", "zh_CN": "按住"},
        description={
            "en_US": "Presses and holds a mouse or keyboard button. Press again to release.",
            "zh_CN": "按下并保持鼠标或键盘按键，再次按下释放。",
        })

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

    pos = params("position", [-1, -1],
        label={"en_US": "Position", "zh_CN": "坐标"})

    # switch() 声明参数可见性依赖：根据 device 值切换显示鼠标或键盘参数
    switch(device, {
        "mouse":    [mouse_button, pos],
        "keyboard": [keyboard_key],
    })

    # is_down 在上下文中持久化，多次触发间保持状态
    is_down = False

# 交替执行 down() / up()；键盘按键时 down() / up() 自动忽略坐标
target = mouse_button if device == "mouse" else keyboard_key
if is_down:
    up(target, *pos)
else:
    down(target, *pos)
is_down = not is_down
