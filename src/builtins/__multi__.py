# ============================================================
# 内置脚本：连点（Multi）
#
# 循环执行点击，每次间隔 interval 毫秒。
# sleep() 内部会检查 stop 信号，热键再次触发可终止循环。
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

    # INT 类型：默认值为 int 时自动推断
    interval = params("interval", 100,
        label={"en_US": "Interval (ms)", "zh_CN": "间隔(ms)"})

    # description 在控件上显示为 tooltip
    count = params("count", -1,
        label={"en_US": "Count", "zh_CN": "次数"},
        description={"en_US": "(-1 = infinite)", "zh_CN": "(-1 = 无限)"})

# ---- 循环执行 ----
# count < 0 表示无限循环，可由热键再次触发终止
i = 0
while count < 0 or i < count:
    click(button, *pos)
    i += 1
    sleep(interval)
