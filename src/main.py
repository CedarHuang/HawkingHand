# nuitka-project: --product-name=HawkingHand
# nuitka-project: --file-description=HawkingHand
# nuitka-project: --company-name=CedarHuang
# nuitka-project: --copyright=Copyright (C) 2024-2026 CedarHuang. Licensed under the Apache License 2.0.

# nuitka-project: --enable-plugin=pyside6
# nuitka-project: --output-dir=dist
# nuitka-project: --output-filename=HawkingHand
# nuitka-project: --standalone
# nuitka-project: --windows-console-mode=disable
# nuitka-project: --windows-icon-from-ico=src/resources/icons/icon.ico

# 排除未使用的 Qt 模块
# nuitka-project: --nofollow-import-to=PySide6.QtDataVisualization
# nuitka-project: --nofollow-import-to=PySide6.QtNetwork
# nuitka-project: --nofollow-import-to=PySide6.QtOpenGL
# nuitka-project: --nofollow-import-to=PySide6.QtOpenGLWidgets
# nuitka-project: --nofollow-import-to=PySide6.QtPdf

# 排除未使用的 win32ui（及其依赖 mfc140u.dll）
# nuitka-project: --nofollow-import-to=win32ui

# 排除未使用的 Qt 插件（PDF、TLS、数据库驱动）
# nuitka-project: --noinclude-qt-plugins=qpdf
# nuitka-project: --noinclude-qt-plugins=sqldrivers
# nuitka-project: --noinclude-qt-plugins=tls

# 排除未使用的大体积 DLL
# nuitka-project: --noinclude-dlls=libcrypto*
# nuitka-project: --noinclude-dlls=libssl*
# nuitka-project: --noinclude-dlls=mfc140u*
# nuitka-project: --noinclude-dlls=qt6datavisualization*
# nuitka-project: --noinclude-dlls=qt6network*
# nuitka-project: --noinclude-dlls=qt6opengl*
# nuitka-project: --noinclude-dlls=qt6pdf*

# 包含 Windows 运行时 DLL，确保用户无需额外安装 VC++ 运行时
# nuitka-project: --include-windows-runtime-dlls=yes

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from __version__ import __version__
from core import event_listener
from core import foreground_listener
from core import single_instance
from core.config import settings as configSettings
from core.scripts import script_observer
from resources import resources_rc  # noqa: F401  注册 Qt 资源
from views.appearance import applyTheme, resolveTheme, installTranslator
from views.main_window import MainWindow


def main():
    # 单实例检查：若已有实例运行则唤醒并退出
    if not single_instance.check():
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)

    # 加载翻译
    _translator = installTranslator(app, configSettings.language)

    # 根据配置应用主题
    applyTheme(app, resolveTheme(configSettings.theme))

    # 创建主窗口
    window = MainWindow()
    window.setVersion(f"v{__version__}")

    # 注册 core 层回调
    window.registerCallbacks()

    # 加载数据到 UI
    window.refreshEventList()
    window.initSettings()

    # 初始化系统托盘
    window.initTray()

    # 非静默启动则显示窗口
    silent = 'silent' in sys.argv
    if not (silent and configSettings.enable_tray):
        window.show()

    # 启动后台监听
    foreground_listener.start()
    event_listener.start()
    script_observer.start()

    # 进入事件循环
    exitCode = app.exec()

    # 退出时清理
    script_observer.stop()
    event_listener.stop()
    foreground_listener.stop()
    window.cleanupTray()

    sys.exit(exitCode)


if __name__ == "__main__":
    main()
