import fnmatch
import keyboard
import sys
import threading

from core import config
from core import foreground_listener
from core import input_backend
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

def _wrap_hotkey(callback, hotkey, trigger_on_release):
    # 统一管理热键触发时机，解决两个问题：
    # 1. keyboard 库长按热键时会持续触发回调，需要限制为只触发一次
    # 2. keyboard 库的 trigger_on_release 参数存在 bug
    #    nonblocking hotkeys 在 KEY_UP 时 _pressed_events 已被清空导致匹配失败
    #
    # 为每个键注册持久 hook 追踪释放状态：
    # - trigger_on_release=False：按下时立即触发回调
    # - trigger_on_release=True：任意键释放时触发回调
    keys = [k.strip() for k in hotkey.split('+')]
    fired = False

    def on_key_release(event):
        nonlocal fired
        if event.event_type == keyboard.KEY_UP:
            # 任意键释放后重置标志位
            if fired and not all(keyboard.is_pressed(k) for k in keys):
                if trigger_on_release:
                    callback()
                fired = False

    for key in keys:
        keyboard.hook_key(key, on_key_release)

    def wrapped_callback():
        nonlocal fired
        if fired:
            return
        fired = True
        if not trigger_on_release:
            callback()

    return wrapped_callback

def start():
    # keyboard 库的 listening_thread 因 GetMessage 异常退出后 listening 标志仍为 True，
    # 导致后续 add_hotkey 不会重建线程，热键失效。因此需要一个 workaround 检测并修正此状态不一致。
    _ensure_keyboard_listening()

    for event in config.events:
        if not event.enabled:
            continue
        if event.hotkey == None or event.hotkey == '':
            continue

        callback = callback_factory(event)
        callback = _wrap_hotkey(callback, event.hotkey, event.trigger_on_release)

        keyboard.add_hotkey(event.hotkey, callback)

def stop():
    keyboard.unhook_all()
    foreground_listener.clear_event_callback_list()

def restart():
    stop()
    start()

def callback_factory(event):
    parse_scope(event)
    match event.type:
        case 'Click':
            return click_factory(event)
        case 'Press':
            return press_factory(event)
        case 'Multi':
            return multi_factory(event)
        case 'Script':
            return script_factory(event)
        case _:
            return lambda: None

def click_factory(event):
    def callback():
        if not check_scope(event):
            return
        input_backend.click(event.target, *event.position)

    return callback

def press_factory(event):
    already_down = False
    def callback():
        if not check_scope(event):
            return
        nonlocal already_down
        if not already_down:
            input_backend.down(event.target, *event.position)
            already_down = True
        else:
            input_backend.up(event.target, *event.position)
            already_down = False

    return callback

def multi_factory(event):
    ing = False
    stop = threading.Event()
    def callback_impl():
        nonlocal ing, stop
        ing = True
        interval = event.interval / 1000
        clicks = event.clicks if event.clicks >= 0 else sys.maxsize
        count = 0
        while not stop.is_set():
            input_backend.click(event.target, *event.position)
            count += 1
            if count >= clicks:
                break
            stop.wait(interval)
        ing = False
        stop.clear()

    def if_ing_then_stop():
        nonlocal ing, stop
        if ing:
            stop.set()
        return ing

    def callback():
        if not check_scope(event):
            return
        if if_ing_then_stop():
            return
        threading.Thread(target=callback_impl).start()

    foreground_listener.add_event_callback_list(if_ing_then_stop)

    return callback

def script_factory(event):
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

