import builtins
import copy
import importlib
import importlib.util
import inspect
import os
import watchdog.events
import watchdog.observers

from core import api
from core import common
from core import event_listener
from core import logger
from core import vision_backend

class ScriptCode:
    instances = {
        # key: script path
        # value: ScriptCode instance
    }

    @classmethod
    def get_by_name(cls, script_name):
        script_path = os.path.join(common.scripts_path(), f'{script_name}.py')
        script_path = os.path.realpath(script_path)
        instance = cls.instances.get(script_path)
        if not instance:
            instance = cls(script_name, script_path)
            cls.instances[script_path] = instance
        return instance

    def __init__(self, script_name, script_path):
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
        logger.script.info(f'File "{path}" has been modified, reload!')

        event_listener.restart()

    def start(self):
        self.observer.start()
        logger.script.debug(f'Started observing scripts directory: {common.scripts_path()}')

    def stop(self):
        self.observer.stop()
        self.observer.join()
        logger.script.debug(f'Stopped observing scripts directory: {common.scripts_path()}')

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
