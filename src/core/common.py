import ctypes
import os
import sys

app_name = 'HawkingHand'
app_author = 'CedarHuang'

scripts_name = 'scripts'
builtins_name = '__builtins__.py'
event_config_name = 'event.json'
settings_config_name = 'settings.json'

def is_frozen():
    return '__compiled__' in globals()

def is_running_as_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def mkdir_if_not_exists(path):
    if not os.path.exists(path):
        os.makedirs(path)

def exe_path():
    return os.path.abspath(sys.argv[0])

def root_path():
    if is_frozen():
        return os.path.dirname(exe_path())
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

def config_path():
    return os.path.join(os.environ['APPDATA'], app_author, app_name)

def scripts_path():
    return os.path.join(config_path(), scripts_name)

def builtins_path():
    return os.path.join(scripts_path(), builtins_name)

def builtins_src_dir():
    if is_frozen():
        return os.path.join(os.path.dirname(exe_path()), 'builtins')
    return os.path.join(root_path(), 'src', 'builtins')

def event_config_path():
    return os.path.join(config_path(), event_config_name)

def settings_config_path():
    return os.path.join(config_path(), settings_config_name)

mkdir_if_not_exists(config_path())
mkdir_if_not_exists(scripts_path())
