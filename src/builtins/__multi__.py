# ============================================================
# 内置脚本：连点（Multi-Click）
#
# 展示了循环执行与终止控制的机制：
#   - sleep() 内部会检查 stop 信号
#   - 热键再次触发时，系统发送 stop 信号
#   - 下一次 sleep() 检查到 stop 信号，抛出 ScriptExit 终止脚本
#   - 用户无需手动编写"如何停止"的逻辑
#
# 参数说明：
#   - count < 0 表示无限循环，由用户再次触发热键终止
#   - count ≥ 0 表示点击指定次数后自动结束
# ============================================================

if init():
    info(
        name={"en_US": "Multi-Click", "zh_CN": "连点"},
        description={
            "en_US": "Repeatedly clicks with mouse or keyboard at a configurable interval.",
            "zh_CN": "以可配置间隔重复点击鼠标或键盘。",
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

    # INT 类型：默认值为 int 时自动推断
    interval = params("interval", 100,
        label={"en_US": "Interval (ms)", "zh_CN": "间隔(ms)"})

    # description 在事件编辑页显示为控件的 tooltip
    count = params("count", -1,
        label={"en_US": "Count", "zh_CN": "次数"},
        description={"en_US": "(-1 = infinite)", "zh_CN": "(-1 = 无限)"})

    # switch() 声明参数可见性依赖：未参与 switch 的参数（interval、count）始终可见
    switch(device, {
        "mouse":    [mouse_button, pos],
        "keyboard": [keyboard_key],
    })

# count < 0 无限循环，sleep() 内置 stop 检查，热键再次触发可终止
# click() 为键盘按键时自动忽略坐标
target = mouse_button if device == "mouse" else keyboard_key
i = 0
while count < 0 or i < count:
    click(target, *pos)
    i += 1
    sleep(interval)
