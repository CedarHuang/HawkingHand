import logging
import os
import sys

from core import common

# 应用日志文件路径
app_log_file_path = os.path.join(common.root_path(), 'app.log')
# 脚本日志文件路径
script_log_file_path = os.path.join(common.config_path(), 'scripts.log')

# 统一日志格式
_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class IndentedFormatter(logging.Formatter):
    def formatException(self, ei):
        return f'{super().formatException(ei)}\n'


def log_level():
    if common.is_frozen():
        return logging.INFO
    return logging.DEBUG


def _install_handler(logger: logging.Logger, handler: logging.Handler):
    handler.setLevel(log_level())
    handler.setFormatter(IndentedFormatter(_LOG_FORMAT))
    logger.addHandler(handler)


def _create_logger(name: str, path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(log_level())

    _install_handler(logger, logging.FileHandler(path, encoding='utf-8'))
    _install_handler(logger, logging.StreamHandler(sys.stdout))

    return logger


app = _create_logger('app', app_log_file_path)
script = _create_logger('script', script_log_file_path)


def install_ui_handler(handler: logging.Handler):
    _install_handler(app, handler)
    _install_handler(script, handler)
