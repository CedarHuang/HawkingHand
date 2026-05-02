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
            "en_US": "Repeatedly clicks at the target position with a configurable interval.",
            "zh_CN": "在目标位置以可配置间隔重复点击。",
        })

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

    # description 在事件编辑页显示为控件的 tooltip
    count = params("count", -1,
        label={"en_US": "Count", "zh_CN": "次数"},
        description={"en_US": "(-1 = infinite)", "zh_CN": "(-1 = 无限)"})

# count < 0 无限循环，sleep() 内置 stop 检查，热键再次触发可终止
i = 0
while count < 0 or i < count:
    click(button, *pos)
    i += 1
    sleep(interval)
