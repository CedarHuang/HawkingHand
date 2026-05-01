import fnmatch
import keyboard
import threading

from core import config
from core import foreground_listener
from core import logger
from core.scripts import scripts


def _ensure_keyboard_listening():
    # 检查 keyboard 库的 listening_thread 是否存活，若已死亡则强制恢复。
    listener = keyboard._listener

    # 获取锁后再确认状态，避免竞态
    with listener.lock:
        if not listener.listening:
            return
        # 线程对象不存在，不应发生，做防御性检查
        if not hasattr(listener, 'listening_thread'):
            listener.listening = False
            logger.app.warning('keyboard listening_thread does not exist, listening flag has been reset')
            return
        # 线程已死亡 -> 需要恢复
        if not listener.listening_thread.is_alive():
            logger.app.warning('keyboard listening_thread is dead, forcing recovery')
            listener.listening = False


def _hook_combo(on_press, on_release, hotkey):
    """通用组合键处理：所有键按下时调 on_press，任意键释放时调 on_release。

    Toggle 和 Hold 共用此函数，仅传入的回调不同：
    - Toggle: on_press=toggle_cb, on_release=None
    - Hold:   on_press=start_cb, on_release=stop_cb
    """
    keys = [k.strip() for k in hotkey.split('+')]
    started = False

    def on_key_event(event):
        nonlocal started
        if event.event_type == keyboard.KEY_DOWN:
            if not started and all(keyboard.is_pressed(k) for k in keys):
                started = True
                if on_press:
                    on_press()
        elif event.event_type == keyboard.KEY_UP:
            if started:
                started = False
                if on_release:
                    on_release()

    for key in keys:
        keyboard.hook_key(key, on_key_event)


def start():
    _ensure_keyboard_listening()

    for event in config.events:
        if not event.enabled:
            continue
        if not event.hotkey:
            continue

        match event.type:
            case 'Toggle':
                on_press, on_release = toggle_factory(event), None
            case 'Hold':
                on_press, on_release = hold_factory(event)
            case _:
                continue
        _hook_combo(on_press, on_release, event.hotkey)


def stop():
    keyboard.unhook_all()
    foreground_listener.clear_event_callback_list()


def restart():
    stop()
    start()


def toggle_factory(event):
    """Toggle 类型：首次热键启动脚本线程，再次热键 set_stop 终止。"""
    parse_scope(event)
    script, context = scripts.load_as_function(event)
    thread = None

    def if_ing_then_stop():
        if thread and thread.is_alive():
            context.set_stop()
            return True
        return False

    def callback():
        nonlocal thread
        if not check_scope(event):
            return
        if if_ing_then_stop():
            return
        thread = threading.Thread(target=script)
        thread.start()

    foreground_listener.add_event_callback_list(if_ing_then_stop)

    return callback


def hold_factory(event):
    """Hold 类型：返回 (start_cb, stop_cb)，分别注册到热键按下和释放。"""
    parse_scope(event)
    script, context = scripts.load_as_function(event)
    thread = None

    def start_script():
        nonlocal thread
        if not check_scope(event):
            return
        if thread and thread.is_alive():
            return
        context.clear_stop()
        thread = threading.Thread(target=script)
        thread.start()

    def stop_script():
        nonlocal thread
        if thread and thread.is_alive():
            context.set_stop()

    return start_script, stop_script


def parse_scope(event):
    _scope = []
    for i in event.scope.split(':', 1):
        i = i.strip()
        if i == '':
            i = '*'
        _scope.append(i)
    _scope.extend(['*'] * (2 - len(_scope)))
    event._scope = _scope
    event._version = 0
    event._passed = False


def check_scope(event):
    process_name, window_title, data_version = foreground_listener.active_window_info()
    if event._version == data_version:
        return event._passed

    e_p_name, e_w_title = event._scope
    process_name_passed = fnmatch.fnmatchcase(process_name, e_p_name)
    window_title_passed = fnmatch.fnmatchcase(window_title, e_w_title)
    passed = process_name_passed and window_title_passed

    event._version = data_version
    event._passed = passed

    return passed
