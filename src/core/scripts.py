import builtins
import copy
import importlib
import importlib.util
import inspect
import json
import os
import threading
import watchdog.events
import watchdog.observers

from core import api
from core import common
from core import event_listener
from core import logger
from core import vision_backend
from core.models import ParamDef, ParamRef, ParamType


def _scan_builtin_names() -> frozenset[str]:
    src_dir = common.builtins_src_dir()
    if not os.path.isdir(src_dir):
        return frozenset()
    return frozenset(
        os.path.splitext(f.name)[0]
        for f in os.scandir(src_dir)
        if f.is_file() and f.name.endswith('.py')
    )


BUILTIN_SCRIPT_NAMES = _scan_builtin_names()


def is_builtin(name: str) -> bool:
    """判断脚本名是否为内置脚本。"""
    return name in BUILTIN_SCRIPT_NAMES


_script_info_cache: dict[str, dict] = {}


def _load_info_cache() -> dict[str, dict]:
    """从磁盘加载 info 缓存。自动清理已删除脚本的条目、mtime 不匹配的条目。"""
    cache_path = common.info_cache_path()
    scripts_dir = common.scripts_path()
    cache: dict[str, dict] = {}
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return cache
    for name, entry in raw.items():
        filepath = os.path.join(scripts_dir, f'{name}.py')
        if not os.path.isfile(filepath):
            continue  # 脚本已删除，自然清理
        try:
            if os.path.getmtime(filepath) != entry.get('mtime', 0):
                continue  # mtime 不匹配，需要重新提取
        except OSError:
            continue
        cache[name] = entry
    return cache


def _save_info_cache():
    """将当前缓存持久化到磁盘。"""
    cache_path = common.info_cache_path()
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(_script_info_cache, f, indent=2, ensure_ascii=False)
    except OSError:
        logger.script.error('Failed to save info cache:', exc_info=True)


def _cache_info(script_name: str):
    """提取脚本 info 并写入缓存（内存 + 磁盘）。"""
    if script_name == '__builtins__':
        _script_info_cache[script_name] = {'name': script_name, 'description': None}
        return
    try:
        info = ScriptCode.get_by_name(script_name).get_info()
    except Exception:
        info = {'name': script_name, 'description': None}
    # 记录 mtime 用于下次启动比对
    filepath = os.path.join(common.scripts_path(), f'{script_name}.py')
    try:
        info['mtime'] = os.path.getmtime(filepath)
    except OSError:
        pass
    _script_info_cache[script_name] = info
    _save_info_cache()


def get_script_info(script_name: str) -> dict:
    """获取脚本的 info 字典（含 name 和 description）。磁盘缓存 + mtime 校验。"""
    if script_name not in _script_info_cache:
        _cache_info(script_name)
    return _script_info_cache[script_name]


def get_display_name(script_name: str) -> str | dict:
    """获取脚本展示名。与 get_script_info 共享缓存。"""
    info = get_script_info(script_name)
    name = info.get('name')
    if name is not None and name != script_name:
        return name
    return script_name


def ensure_builtin_scripts():
    """将内置脚本源文件复制到用户 scripts 目录。仅内容不同时覆写，避免 mtime 刷新触发 info 提取。"""
    src_dir = common.builtins_src_dir()
    if not os.path.isdir(src_dir):
        return
    scripts_dir = common.scripts_path()
    for name in BUILTIN_SCRIPT_NAMES:
        src = os.path.join(src_dir, f'{name}.py')
        dst = os.path.join(scripts_dir, f'{name}.py')
        try:
            with open(src, 'r', encoding='utf-8') as f:
                code = f.read()
            try:
                with open(dst, 'r', encoding='utf-8') as f:
                    if f.read() == code:
                        continue
            except FileNotFoundError:
                pass
            with open(dst, 'w', encoding='utf-8') as f:
                f.write(code)
        except OSError:
            logger.script.error(f'Failed to sync built-in script {name}.py:', exc_info=True)


class ScriptCode:
    instances: dict[str, 'ScriptCode'] = {
        # key: script path
        # value: ScriptCode instance
    }

    @classmethod
    def get_by_name(cls, script_name):
        script_path = os.path.join(common.scripts_path(), f'{script_name}.py')
        # 内置脚本：文件被删后恢复
        if is_builtin(script_name) and not os.path.exists(script_path):
            ensure_builtin_scripts()
        script_path = os.path.realpath(script_path)
        instance = cls.instances.get(script_path)
        if not instance:
            instance = cls(script_name, script_path)
            cls.instances[script_path] = instance
        return instance

    def __init__(self, script_name, script_path):
        self.name = script_name
        self.path = script_path
        self.code = ''
        self.reload()

        try:
            # 仅初次加载尝试编译
            self.code = compile(self.code, f'<{script_name}>', 'exec')
        except SyntaxError:
            logger.script.error(f'Syntax error in script <{script_name}> at "{script_path}":', exc_info=True)
        except:
            logger.script.error(f'Unexpected error compiling script <{script_name}> at "{script_path}":', exc_info=True)

    def reload(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                self.code = f.read()
        except:
            logger.script.error(f'Error reading script file: "{self.path}":', exc_info=True)

    def get_info(self) -> dict:
        return ExtractContext().run_info_with_timeout(self)

    def get_param_defs(self) -> list[ParamDef]:
        return ExtractContext().run_params_with_timeout(self)


class ScriptObserver(watchdog.events.FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.observer = watchdog.observers.Observer()
        self.observer.schedule(self, common.scripts_path(), recursive=True)
        self._known_mtimes = {
            # key: file path
            # value: 上次处理时的 mtime
        }

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith('.py'):
            return

        path = os.path.realpath(event.src_path)

        instance = ScriptCode.instances.get(path)
        if not instance:
            return

        # 仅当文件实际修改时间变化时才 reload（过滤读取触发的伪事件）
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return

        if self._known_mtimes.get(path) == mtime:
            return
        self._known_mtimes[path] = mtime

        instance.reload()
        if instance.name in _script_info_cache:
            del _script_info_cache[instance.name]
            _save_info_cache()
        logger.script.info(f'File "{path}" has been modified, reload!')

        event_listener.restart()

    def start(self):
        ensure_builtin_scripts()
        self.observer.start()
        logger.script.debug(f'Started observing scripts directory: {common.scripts_path()}')

    def stop(self):
        self.observer.stop()
        self.observer.join()
        logger.script.debug(f'Stopped observing scripts directory: {common.scripts_path()}')

# 模块加载时初始化缓存
_script_info_cache.update(_load_info_cache())
script_observer = ScriptObserver()


class ScriptContext(dict):
    # 用户脚本中import的查找路径
    allowed_import_paths = [
        os.path.abspath(common.scripts_path()),
    ]

    def __init__(self, event):
        super().__init__()
        self.event = event
        self['__builtins__'] = self.create_restricted_builtins()

    def create_restricted_builtins(self):
        restricted_builtins = builtins.__dict__.copy()

        if 'open' in restricted_builtins:
            del restricted_builtins['open']
        if 'exit' in restricted_builtins:
            del restricted_builtins['exit']
        if 'quit' in restricted_builtins:
            del restricted_builtins['quit']

        restricted_builtins['__import__'] = self.custom_import
        restricted_builtins.update(api._create_context(self.event))

        self.import_module_to_target(restricted_builtins, 'core.api', import_root=False, import_all=True, exclude_module=True)

        return restricted_builtins

    def set_stop(self):
        self['__builtins__']['_set_stop']()

    def clear_stop(self):
        self['__builtins__']['_clear_stop']()

    def clear_delay_flag(self):
        self['__builtins__']['_clear_delay_flag']()

    def custom_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        # 允许导入的内置模块
        if name in ['math', 'time']:
            return importlib.import_module(name)

        # 禁止相对导入
        if level != 0:
            raise ImportError(f"Relative imports are not allowed in sandboxed scripts: '{name}' (level={level})")

        # 尝试在允许的路径中查找模块
        for allowed_path_root in self.allowed_import_paths:
            # 构造可能的模块文件路径
            # 对于 'module' -> 'module.py'
            # 对于 'package.sub_module' -> 'package/__init__.py' then 'package/sub_module.py'
            module_path_parts = name.split('.')
            # 尝试作为文件导入 (e.g., module.py)
            potential_file_path = os.path.join(allowed_path_root, *module_path_parts) + '.py'
            # 尝试作为包导入 (e.g., package/__init__.py)
            potential_package_init_path = os.path.join(allowed_path_root, *module_path_parts, '__init__.py')

            found_path = None
            if os.path.exists(potential_file_path) and os.path.isfile(potential_file_path):
                found_path = potential_file_path
            elif os.path.exists(potential_package_init_path) and os.path.isfile(potential_package_init_path):
                found_path = potential_package_init_path
            if not found_path:
                continue

            # 确保找到的路径在搜索路径下
            resolved_found_path = os.path.realpath(found_path)
            resolved_allowed_path_root = os.path.realpath(allowed_path_root)
            if not resolved_found_path.startswith(resolved_allowed_path_root + os.sep) and resolved_found_path != resolved_allowed_path_root:
                continue

            # 加载模块
            spec = importlib.util.spec_from_file_location(name, resolved_found_path)
            if not spec:
                continue

            module = importlib.util.module_from_spec(spec)
            module.__builtins__ = self['__builtins__'].copy()
            module.__builtins__['init'] = api._create_init()
            spec.loader.exec_module(module)
            return module

        raise ImportError(f"Module '{name}' not found or not allowed to be imported from restricted paths.")

    @staticmethod
    def import_module_to_target(target, module_name, import_root=True, import_all=False, exclude_module=False):
        module = importlib.import_module(module_name)

        if import_root:
            target[module_name] = module

        if not import_all:
            return

        for name in dir(module):
            if name.startswith('_'):
                continue

            member = getattr(module, name)
            if exclude_module and inspect.ismodule(member):
                continue

            target[name] = member


class ExtractContext(ScriptContext):
    MODE_INFO = 'info'
    MODE_PARAMS = 'params'

    def __init__(self):
        self._script_name = ''
        self._mode = ''
        super().__init__(None)

    def create_restricted_builtins(self):
        restricted_builtins = super().create_restricted_builtins()

        # 非活跃模式的 API 替换为 noop，活跃模式的保留（由 run_*_with_timeout 注入）
        if self._mode == self.MODE_INFO:
            restricted_builtins['params'] = lambda name, default, /, **_: default
        else:
            restricted_builtins['info'] = lambda name=None, description=None: None

        def _make_disabled_handler(name):
            def handler(*args, **kwargs):
                logger.script.debug(
                    f'Script <{self._script_name}> called disabled builtin <{name}> '
                    f'in <{self._mode}> extraction sandbox, script execution terminated'
                )
                return restricted_builtins['exit']()
            return handler

        for key in restricted_builtins:
            if key in ('__import__', 'exit', 'quit', 'params', 'info', 'switch'):
                continue
            restricted_builtins[key] = _make_disabled_handler(key)

        return restricted_builtins

    def run_params_with_timeout(self, script_code: ScriptCode) -> list[ParamDef]:
        """PARAMS 模式：只录制 params() 调用，info() 为 noop。"""
        self._mode = self.MODE_PARAMS
        self._script_name = script_code.name
        param_defs: list[ParamDef] = []
        self.update(self._create_extract_params(param_defs))
        self['init'] = api._create_init()

        def _run_extract():
            try:
                exec(script_code.code, self)
            except api.ScriptExit:
                pass
            except Exception as e:
                logger.script.warning(f'Script <{script_code.name}> extract params failed: {e}')

        extract_thread = threading.Thread(target=_run_extract, daemon=True)
        extract_thread.start()
        extract_thread.join(timeout=0.5)

        return list(param_defs)

    def run_info_with_timeout(self, script_code: ScriptCode) -> dict:
        """INFO 模式：只录制 info() 调用，params() 为 noop。"""
        self._mode = self.MODE_INFO
        self._script_name = script_code.name
        info: dict = {}
        self['info'] = self._create_extract_info(info)
        self['init'] = api._create_init()

        def _run_extract():
            try:
                exec(script_code.code, self)
            except api.ScriptExit:
                pass
            except Exception as e:
                logger.script.warning(f'Script <{script_code.name}> extract info failed: {e}')

        extract_thread = threading.Thread(target=_run_extract, daemon=True)
        extract_thread.start()
        extract_thread.join(timeout=0.5)

        return dict(info)

    @staticmethod
    def _create_extract_params(param_defs: list[ParamDef]):
        def _extract_params(name, default, /, *, label=None, description=None, options=None, type=None):
            # 参数不足或 default=None 时忽略该调用
            if name is None or default is None:
                return None

            param_type = ParamType(type) if type else ParamType.infer_from(default, options)

            # 处理 default 不在 options 中的情况
            effective_default = api._effective_default(default, options)

            # 同名参数已存在直接返回（与 params() 中 name ∈ script_args 的路径对齐，
            # 返回 ParamRef 供 switch() 读取 ._name 建立可见性映射）
            for pd in param_defs:
                if pd.name == name:
                    return ParamRef(name, effective_default)

            param_defs.append(ParamDef(
                name=name, type=param_type, default=effective_default,
                label=label, description=description, options=options,
            ))
            return ParamRef(name, effective_default)

        def _extract_switch(source, cases):
            source_name = getattr(source, '_name', None)
            if source_name is None:
                return
            targets: dict[str, list[str]] = {}
            for case_value, param_list in cases.items():
                names = []
                for p in param_list:
                    p_name = getattr(p, '_name', None)
                    if p_name is not None:
                        names.append(p_name)
                targets[case_value] = names
            for pd in param_defs:
                if pd.name == source_name:
                    pd.switch_cases = targets
                    break

        return {'params': _extract_params, 'switch': _extract_switch}

    @staticmethod
    def _create_extract_info(info: dict):
        recorded = False

        def _extract_info(name=None, description=None):
            nonlocal recorded
            if not recorded:
                info['name'] = name
                info['description'] = description
                recorded = True
            return None

        return _extract_info


class Scripts:
    def __init__(self):
        pass

    def load_as_function(self, event):
        script_name = event.target
        script_code = ScriptCode.get_by_name(script_name)
        script_context = ScriptContext(copy.deepcopy(event))

        def wrapped_function():
            try:
                exec(script_code.code, script_context)
            except api.ScriptExit as e:
                if e.code != 0:
                    logger.script.info(f'Script <{script_name}> terminated: {e}')
            except Exception:
                logger.script.error(f'Runtime error in script <{script_name}>:', exc_info=True)
            finally:
                script_context.clear_stop()
                script_context.clear_delay_flag()
                vision_backend.close()

        return wrapped_function, script_context

scripts = Scripts()
