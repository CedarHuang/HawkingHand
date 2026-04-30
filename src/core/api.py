import functools
import inspect
import sys
import threading

from core import common
from core import foreground_listener
from core import input_backend
from core import logger
from core import models
from core import vision_backend

class ScriptExit(Exception):
    """自定义异常类，用于表示脚本的有意终止。

    当脚本通过 'exit()' 或 'quit()' 函数终止时，会抛出此异常。

    Attributes:
        message (str): 异常消息，默认为 "Script terminated."。
        code (int): 退出代码，默认为 0。
    """

    def __init__(self, message="Script terminated.", code=0):
        super().__init__(message)
        self.code = code


def _effective_default(default, options):
    """当 default 不在 options 中时，返回 options 的第一个元素作为有效默认值。

    注意：对 dict 类型 options，``in`` 检查的是 key 而非 value，
    ``next(iter(options))`` 返回的也是第一个 key（即 CHOICE 的实际值）。

    :meta private: 内部使用。
    """
    if options is not None and default not in options:
        try:
            return next(iter(options))
        except StopIteration:
            pass
    return default


def _create_init(_=None):
    """为 Script 脚本环境生成init。

    :meta private: 内部使用。
    """
    init_flag = False

    def init():
        """检查是否为首次调用。

        这个函数设计为只在首次调用时返回 True，之后的所有调用都返回 False。
        适用于需要确保某个操作只执行一次的场景。

        Returns:
            bool: 首次调用返回 True，后续调用返回 False。
        """
        nonlocal init_flag
        if not init_flag:
            init_flag = True
            return True
        return False

    return init

_context_id_inc = 0
_global_cache = {}
_script_cache = {}
_global_cache_lock = threading.Lock()
_script_cache_lock = threading.Lock()

def _create_context(event: models.Event):
    """为 Script 脚本环境生成API。

    :meta private: 内部使用。
    """

    ############################################################################
    functions = {}

    def register(name=None):
        """注册函数到上下文。

        :meta private: 内部使用。
        """
        def decorator(func):
            functions[name or func.__name__] = func
            return func
        return decorator

    def replace_with(func):
        """装饰器，将被装饰函数替换为 func。

        :meta private: 内部使用。
        """
        def decorator(_):
            return func
        return decorator

    ############################################################################
    # 注册后端模块中的公开常量
    for backend in (input_backend, vision_backend):
        for name, member in vars(backend).items():
            if name.startswith('_'):
                continue
            if inspect.ismodule(member) or inspect.isfunction(member) or inspect.isclass(member):
                continue
            if name not in functions:
                functions[name] = member

    ############################################################################
    global _context_id_inc
    _context_id_inc += 1
    _context_id = _context_id_inc

    @register()
    def context_id():
        """获取当前上下文的唯一标识符。

        Returns:
            int: 当前上下文的唯一标识符。
        """
        return _context_id

    ############################################################################
    stop_event = threading.Event()

    @register()
    def sleep(ms):
        """暂停脚本执行指定的毫秒数。

        Args:
            ms (float | int): 暂停的毫秒数。
        """
        stop_event.wait(ms / 1000)
        if stop_event.is_set():
            exit()

    @register()
    def _set_stop():
        """设置停止事件标志。

        :meta private: 内部使用。
        """
        stop_event.set()

    @register()
    def _clear_stop():
        """清除停止事件标志。

        :meta private: 内部使用。
        """
        stop_event.clear()

    ############################################################################
    delay_time = 100
    delay_flag = False

    def delay(func):
        """延迟装饰器，在函数调用后添加延迟。

        :meta private: 内部使用。
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal delay_flag
            if delay_flag:
                sleep(delay_time)
            delay_flag = True
            result = func(*args, **kwargs)
            return result
        return wrapper

    @register()
    def _clear_delay_flag():
        """清除延迟标记。

        :meta private: 内部使用。
        """
        nonlocal delay_flag
        delay_flag = False

    @register('get_pause')
    @register()
    def get_delay():
        """获取鼠标操作调用之后的延迟时间。

        Returns:
            float | int: 当前的延迟时间（毫秒）。
        """
        return delay_time

    @register('set_pause')
    @register()
    def set_delay(ms):
        """设置鼠标操作调用之后的延迟时间。

        Args:
            ms (float | int): 延迟的毫秒数。

        Returns:
            float | int: 设置后的延迟时间（毫秒）。
        """
        nonlocal delay_time
        delay_time = ms
        return delay_time

    class DelayContext:
        def __init__(self, ms):
            self.temp_delay = ms
            self.orig_delay = None

        def __enter__(self):
            self.orig_delay = get_delay()
            set_delay(self.temp_delay)

        def __exit__(self, exc_type, exc_val, exc_tb):
            set_delay(self.orig_delay)

    @register('tmp_pause')
    @register()
    def tmp_delay(ms):
        """创建一个延迟上下文管理器实例，用于在代码块内临时设置延迟时间。

        Examples:
            with tmp_delay(200):
                click('Left')  # 在这个代码块内，点击操作将使用200毫秒的延迟
            click('Left')  # 在这个代码块外，点击操作将恢复之前的延迟设置

        Args:
            ms (float | int): 在上下文内使用的延迟时间（毫秒）。

        Returns:
            DelayContext: 延迟上下文管理器实例。
        """
        return DelayContext(ms)

    ############################################################################

    @register()
    def get_global_cache(key, default=None):
        """获取全局缓存中的值。

        这个字典在所有脚本的不同上下文中共享，可以用于存储跨脚本的数据。

        Args:
            key: 要获取的值的键。
            default: 如果键不存在时返回的默认值。默认为 None。

        Returns:
            any: 全局缓存字典中的值，如果键不存在则返回默认值。
        """
        with _global_cache_lock:
            return _global_cache.get(key, default)

    @register()
    def set_global_cache(key, value):
        """设置全局缓存中的值。

        这个字典在所有脚本的不同上下文中共享，可以用于存储跨脚本的数据。

        Args:
            key: 要设置的值的键。
            value: 要设置的值。

        Returns:
            any: 设置的值。
        """
        with _global_cache_lock:
            _global_cache[key] = value
            return value

    def _cur_script_cache():
        """获取当前脚本的缓存字典。
        
        这个字典在同一脚本的不同上下文中共享，但在不同脚本之间不共享。
        可以用于存储同一脚本的不同运行实例之间的数据。
        
        :meta private: 内部使用。
        """
        script_name = event.target
        if script_name not in _script_cache:
            _script_cache[script_name] = {}
        return _script_cache[script_name]

    @register()
    def get_script_cache(key, default=None):
        """获取脚本缓存中的值。

        这个字典在同一脚本的不同上下文中共享，但在不同脚本之间不共享。
        可以用于存储同一脚本的不同运行实例之间的数据。

        Args:
            key: 要获取的值的键。
            default: 如果键不存在时返回的默认值。默认为 None。

        Returns:
            any: 脚本缓存字典中的值，如果键不存在则返回默认值。
        """
        with _script_cache_lock:
            return _cur_script_cache().get(key, default)

    @register()
    def set_script_cache(key, value):
        """设置脚本缓存中的值。

        这个字典在同一脚本的不同上下文中共享，但在不同脚本之间不共享。
        可以用于存储同一脚本的不同运行实例之间的数据。

        Args:
            key: 要设置的值的键。
            value: 要设置的值。

        Returns:
            any: 设置的值。
        """
        with _script_cache_lock:
            _cur_script_cache()[key] = value
            return value

    ############################################################################

    @register('init')
    @_create_init
    def _(): ...

    @register('print')
    def _(*args, **kwargs):
        """将消息记录到脚本日志中。

        这个函数接受任意数量的位置参数和可选的 'sep' 和 'end' 关键字参数，类似于内置的 'print' 函数。
        但它将输出重定向到脚本日志，而不是标准输出。

        Args:
            *args: 要打印的任意数量的位置参数。
            sep (str, optional): 分隔符，用于分隔位置参数。默认为一个空格。
            end (str, optional): 结尾字符串，添加到输出的末尾。默认为一个换行符。
        """
        sep = kwargs.get('sep', ' ')
        end = kwargs.get('end', '\n')
        message = sep.join(map(str, args)) + end.rstrip('\n')
        logger.script.info(message)

    @register('quit')
    @register()
    def exit(code=0):
        """终止当前脚本的执行。

        此函数通过抛出 'ScriptExit' 异常来实现终止。

        Args:
            code (int, optional): 脚本的退出代码。默认为 0。

        Raises:
            ScriptExit: 始终抛出此异常以终止脚本。
        """
        raise ScriptExit(f"Script exited with code {code}", code)

    @register()
    def params(name, default, /, *, label=None, description=None, options=None):
        """声明脚本可配置参数并同时获取参数值。

        在脚本中调用此函数声明一个可配置参数，系统会根据参数声明在 UI 中渲染对应的输入控件。
        脚本执行时，返回该参数的用户配置值（若未配置则返回默认值）。

        Args:
            name (str): 参数标识符，用于在 UI 中显示和运行时查找配置值。
            default (int | float | str | bool): 参数默认值，同时用于推断参数类型（必填）。
            label (str | dict[str, str] | None): 显示标签，支持单语言字符串或多语言映射。默认为 None（使用 name）。
            description (str | dict[str, str] | None): 描述文本，支持单语言字符串或多语言映射。默认为 None。
            options (list | dict | None): 选项列表，用于 choice 类型参数。默认为 None。

        Returns:
            int | float | str | bool: 参数的用户配置值，若未配置则返回 default 值。
        """
        script_args = event.params.script_args if event else {}
        if name in script_args:
            value = script_args[name]
            # 类型转换：确保返回值类型与 default 一致
            param_type = models.ParamType.infer_from(default, options)
            if param_type == models.ParamType.CHOICE:
                # choice 类型：返回 options 中的键/元素值
                if value in options:
                    return value
                # 值不在 options 中，回退到有效默认值
                return _effective_default(default, options)
            elif param_type == models.ParamType.BOOL:
                if isinstance(value, bool):
                    return value
                if isinstance(value, int):
                    return bool(value)
                if isinstance(value, str):
                    return value.lower() in ('true', 'yes', '1')
                return default
            elif param_type == models.ParamType.INT:
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return default
            elif param_type == models.ParamType.FLOAT:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default
            elif param_type == models.ParamType.STR:
                try:
                    return str(value)
                except (ValueError, TypeError):
                    return default
            return value
        # default 不在 options 中时返回 options 第一个元素
        return _effective_default(default, options)

    @register()
    def event_hotkey():
        """获取当前事件的热键。

        Returns:
            str: 当前事件的热键。
        """
        return event.hotkey

    @register()
    def foreground():
        """获取当前处于前台（活动）窗口的进程名称和窗口标题。

        Returns:
            tuple[str, str]: 包含两个字符串的元组。
                - process_name (str): 当前前台应用程序的进程名称。
                - window_title (str): 当前前台窗口的标题。
        """
        process_name, window_title, _ = foreground_listener.active_window_info()
        return process_name, window_title

    ############################################################################

    @register()
    @replace_with(input_backend.position)
    def _(): ...

    @register()
    @delay
    @replace_with(input_backend.click)
    def _(): ...

    @register('press')
    @register()
    @delay
    @replace_with(input_backend.down)
    def _(): ...

    @register('release')
    @register()
    @delay
    @replace_with(input_backend.up)
    def _(): ...

    @register()
    @delay
    @replace_with(input_backend.move)
    def _(): ...

    @register()
    @delay
    @replace_with(input_backend.move_to)
    def _(): ...

    @register()
    @replace_with(input_backend.is_caps_lock_on)
    def _(): ...

    ############################################################################

    @register()
    @replace_with(vision_backend.get_pixel)
    def _(): ...

    @register()
    @replace_with(vision_backend.get_pixels)
    def _(): ...

    ############################################################################
    return functions

def _generate_builtins():
    lines = [
    '"""',
    '自动生成的 __builtins__.py 文件',
    '为 IDE 提供代码补全和类型提示',
    '"""',
    '',
    'from builtins import *',
    '',
    ]

    def _append_doc(doc, indent=''):
        if doc:
            doc = doc.replace('\n', '\n' + indent)
            lines.append(f'{indent}"""{doc}')
            lines.append(f'{indent}"""')

    # 收集所有可用符号
    this_module = sys.modules[__name__]
    env = {}
    for name in dir(this_module):
        if name.startswith('_'):
            continue

        member = getattr(this_module, name)
        if inspect.ismodule(member) or inspect.isfunction(member):
            continue
        env[name] = member
    env.update(_create_context(None))

    # 处理符号
    for name, member in env.items():
        if name.startswith('_'):
            continue

        if inspect.isclass(member):
            bases = ', '.join(b.__name__ for b in member.__bases__)
            lines.append(f'class {name}({bases}):')
            _append_doc(inspect.getdoc(member), '    ')
            lines.append('    ...\n')
        elif callable(member):
            sig = inspect.signature(member)
            lines.append(f'def {name}{sig}:')
            _append_doc(inspect.getdoc(member), '    ')
            lines.append('    ...\n')
        else:
            annotated_type = getattr(type(member), '__annotated_type__', type(member))
            lines.append(f'{name}: {annotated_type.__name__} = {member!r}')
            _append_doc(inspect.getdoc(member))
            lines.append('')

    with open(common.builtins_path(), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

_generate_builtins()