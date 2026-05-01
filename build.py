"""
项目构建脚本
============
提供以下构建功能：
  1. UI 编译：将 src/ui/*.ui 通过 pyside6-uic 编译为 Python 模块，输出到 src/ui/generated/
  2. 翻译提取：从 .py 和 .ui 源文件中提取可翻译字符串，生成 .ts 文件到 src/translations/
  3. 翻译编译：将 .ts 文件通过 pyside6-lrelease 编译为 .qm 文件，输出到 src/translations/generated/
  4. 资源编译：将 src/resources/resources.qrc 通过 pyside6-rcc 编译为 Python 模块
     qrc 中统一管理图标(icons)、样式(qss)和翻译(qm)等所有资源
  5. 项目打包：通过 Nuitka 将项目编译为独立可执行文件，输出到 dist/

用法:
    python build.py                 # 构建全部（UI + 翻译 + 资源编译）
    python build.py rcc             # 仅编译 .qrc 资源文件
    python build.py ui              # 仅编译 .ui 文件
    python build.py tr              # 提取翻译字符串并编译 .qm（自动触发 rcc）
    python build.py tr --extract    # 仅提取/更新 .ts 文件
    python build.py tr --compile    # 仅编译 .ts → .qm（自动触发 rcc）
    python build.py tr --locations  # 提取时保留源码位置信息（默认不保留）
    python build.py dist            # 打包项目（自动先执行全量构建）
    python build.py clean           # 清空所有构建产物
    python build.py check           # 仅检查哪些文件需要重新构建
"""
import io
import re
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# ---- 修复非 UTF-8 终端（如 CI 环境的 cp1252）下中文输出崩溃 ----
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---- 路径常量 ----
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"

# UI 相关路径
_UI_DIR = _SRC_DIR / "ui"
_UI_OUTPUT_DIR = _UI_DIR / "generated"
_UI_OUTPUT_PREFIX = "ui_"

# 资源相关路径
_RES_DIR = _SRC_DIR / "resources"
_RES_QRC_FILE = _RES_DIR / "resources.qrc"
_RES_OUTPUT_FILE = _RES_DIR / "resources_rc.py"

# 翻译相关路径
_TR_DIR = _SRC_DIR / "translations"
_TR_OUTPUT_DIR = _TR_DIR / "generated"
_TR_PREFIX = "hawkinghand_"

# 打包相关路径
_DIST_DIR = _PROJECT_ROOT / "dist"
_MAIN_SCRIPT = _SRC_DIR / "main.py"
_VERSION_FILE = _SRC_DIR / "__version__.py"

# 需要提取翻译的语言列表
_TR_LANGUAGES = ["zh_CN", "en_US"]

# 需要扫描翻译字符串的源文件目录/文件模式
_TR_SOURCE_PATTERNS = [
    (_SRC_DIR / "views", "*.py"),
    (_SRC_DIR / "core", "*.py"),
    (_UI_DIR, "*.ui"),
    (_SRC_DIR, "main.py"),
]


# ============================================================
# 工具查找
# ============================================================

def _find_pyside6_tool(tool_name: str) -> str:
    """查找 PySide6 工具的可执行文件路径

    Args:
        tool_name: 工具名称，如 "uic", "lupdate", "lrelease"

    Returns:
        工具的完整路径，未找到则返回空字符串
    """
    # 优先使用 PySide6 包目录下的工具
    try:
        import PySide6
        suffix = ".exe" if sys.platform == "win32" else ""
        tool_path = Path(PySide6.__file__).parent / f"{tool_name}{suffix}"
        if tool_path.exists():
            return str(tool_path)
    except ImportError:
        pass

    # 回退：尝试 PATH 中的 pyside6-<tool_name>
    result = shutil.which(f"pyside6-{tool_name}")
    if result:
        return result

    return ""


# ============================================================
# 资源编译
# ============================================================

def cmd_rcc(force: bool = False) -> bool:
    """编译 .qrc 资源文件为 Python 模块"""
    if not _RES_QRC_FILE.exists():
        print("未找到 .qrc 资源文件。")
        return True

    rcc_path = _find_pyside6_tool("rcc")
    if not rcc_path:
        print("✗ 未找到 pyside6-rcc，请确认已安装 PySide6:")
        print("  pip install PySide6")
        return False

    print(f"[资源] 扫描到 {_RES_QRC_FILE.name}\n")

    if not force and not _needs_compile(_RES_QRC_FILE, _RES_OUTPUT_FILE):
        # 还需要检查 qrc 引用的所有资源文件是否有变更
        needs_recompile = False
        if _RES_OUTPUT_FILE.exists():
            out_mtime = _RES_OUTPUT_FILE.stat().st_mtime
            # 扫描 qrc 引用的所有资源目录
            _res_scan_dirs = [
                _RES_DIR,                   # icons/ 等直属资源
                _SRC_DIR / "ui" / "styles", # QSS 样式文件
                _TR_OUTPUT_DIR,             # QM 翻译编译产物
            ]
            for scan_dir in _res_scan_dirs:
                if not scan_dir.exists():
                    continue
                for res_file in scan_dir.rglob("*"):
                    if res_file.is_file() and res_file.suffix not in (".py", ".qrc", ".pyc"):
                        if res_file.stat().st_mtime > out_mtime:
                            needs_recompile = True
                            break
                if needs_recompile:
                    break
        if not needs_recompile:
            print(f"  ⊘ 跳过（未变更）: {_RES_QRC_FILE.name}")
            print(f"\n[资源] 完成: 0 个编译, 1 个跳过, 0 个失败")
            return True

    print(f"  ▸ 编译: {_RES_QRC_FILE.name} → {_RES_OUTPUT_FILE.name}")
    cmd = [rcc_path, str(_RES_QRC_FILE), "-o", str(_RES_OUTPUT_FILE), "-g", "python"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"\n[资源] 完成: 1 个编译, 0 个跳过, 0 个失败")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ✗ 编译失败: {_RES_QRC_FILE.name}")
        if e.stderr:
            print(f"    错误信息: {e.stderr.strip()}")
        print(f"\n[资源] 完成: 0 个编译, 0 个跳过, 1 个失败")
        return False


# ============================================================
# UI 编译
# ============================================================

def _find_ui_files() -> list[Path]:
    """扫描 src/ui/ 下所有 .ui 文件（不递归子目录）"""
    return sorted(_UI_DIR.glob("*.ui"))


def _ui_output_path(ui_file: Path) -> Path:
    """根据 .ui 文件路径计算对应的输出 .py 路径

    例如: main_window.ui → generated/ui_main_window.py
    """
    return _UI_OUTPUT_DIR / f"{_UI_OUTPUT_PREFIX}{ui_file.stem}.py"


def _needs_compile(src_file: Path, out_file: Path) -> bool:
    """判断是否需要重新编译（源文件比产物更新，或产物不存在）"""
    if not out_file.exists():
        return True
    return src_file.stat().st_mtime > out_file.stat().st_mtime


def _ensure_dir(directory: Path, with_init: bool = False):
    """确保目录存在，可选创建 __init__.py"""
    directory.mkdir(parents=True, exist_ok=True)
    if with_init:
        init_file = directory / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")


def _compile_single_ui(ui_file: Path, py_file: Path, uic_path: str) -> bool:
    """调用 pyside6-uic 编译单个 .ui 文件，返回是否成功"""
    cmd = [uic_path, str(ui_file), "-o", str(py_file), "-g", "python"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ✗ 编译失败: {ui_file.name}")
        if e.stderr:
            print(f"    错误信息: {e.stderr.strip()}")
        return False


def cmd_ui(force: bool = False):
    """编译所有 .ui 文件"""
    ui_files = _find_ui_files()
    if not ui_files:
        print("未找到任何 .ui 文件。")
        return True

    uic_path = _find_pyside6_tool("uic")
    if not uic_path:
        print("✗ 未找到 pyside6-uic，请确认已安装 PySide6:")
        print("  pip install PySide6")
        return False

    _ensure_dir(_UI_OUTPUT_DIR, with_init=True)

    compiled = 0
    skipped = 0
    failed = 0

    print(f"[UI] 扫描到 {len(ui_files)} 个 .ui 文件\n")

    for ui_file in ui_files:
        py_file = _ui_output_path(ui_file)

        if not force and not _needs_compile(ui_file, py_file):
            skipped += 1
            print(f"  ⊘ 跳过（未变更）: {ui_file.name}")
            continue

        print(f"  ▸ 编译: {ui_file.name} → generated/{py_file.name}")
        if _compile_single_ui(ui_file, py_file, uic_path):
            compiled += 1
        else:
            failed += 1

    print(f"\n[UI] 完成: {compiled} 个编译, {skipped} 个跳过, {failed} 个失败")
    return failed == 0


# ============================================================
# 翻译提取与编译
# ============================================================

def _collect_source_files() -> list[Path]:
    """收集所有需要提取翻译字符串的源文件"""
    sources = []
    for base_path, pattern in _TR_SOURCE_PATTERNS:
        if base_path.is_file():
            # 直接指定的单个文件
            if base_path.exists():
                sources.append(base_path)
        elif base_path.is_dir():
            sources.extend(sorted(base_path.glob(pattern)))
    return sources


def _ts_path(lang: str) -> Path:
    """获取 .ts 文件路径"""
    return _TR_DIR / f"{_TR_PREFIX}{lang}.ts"


def _qm_path(lang: str) -> Path:
    """获取 .qm 文件路径"""
    return _TR_OUTPUT_DIR / f"{_TR_PREFIX}{lang}.qm"


def cmd_tr_extract(locations: bool = False) -> bool:
    """从源文件中提取可翻译字符串到 .ts 文件

    Args:
        locations: 是否在 .ts 文件中保留源码位置信息（默认 False）。
                   不保留位置信息可避免因代码行号变动导致 .ts 文件频繁变更。
    """
    lupdate_path = _find_pyside6_tool("lupdate")
    if not lupdate_path:
        print("✗ 未找到 pyside6-lupdate，请确认已安装 PySide6:")
        print("  pip install PySide6")
        return False

    sources = _collect_source_files()
    if not sources:
        print("[翻译] 未找到任何源文件。")
        return True

    _ensure_dir(_TR_DIR)

    loc_hint = "（保留位置信息）" if locations else "（不含位置信息）"
    print(f"[翻译] 从 {len(sources)} 个源文件中提取翻译字符串 {loc_hint}\n")

    success = True
    for lang in _TR_LANGUAGES:
        ts_file = _ts_path(lang)
        # pyside6-lupdate <源文件...> -ts <输出.ts>
        cmd = [lupdate_path] + [str(f) for f in sources]
        if not locations:
            cmd += ["-locations", "none"]
        cmd += ["-no-obsolete"]
        cmd += ["-ts", str(ts_file)]
        print(f"  ▸ 提取: {lang}.ts")
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ✗ 提取失败: {lang}.ts")
            if e.stderr:
                print(f"    错误信息: {e.stderr.strip()}")
            success = False

    status = "完成" if success else "部分失败"
    print(f"\n[翻译] 提取{status}，.ts 文件位于: {_TR_DIR.relative_to(_PROJECT_ROOT)}/")
    return success


def cmd_tr_compile(force: bool = False) -> bool:
    """将 .ts 文件编译为 .qm 文件"""
    lrelease_path = _find_pyside6_tool("lrelease")
    if not lrelease_path:
        print("✗ 未找到 pyside6-lrelease，请确认已安装 PySide6:")
        print("  pip install PySide6")
        return False

    # 查找已有的 .ts 文件
    ts_files = sorted(_TR_DIR.glob("*.ts"))
    if not ts_files:
        print("[翻译] 未找到任何 .ts 文件，请先运行提取: python build.py tr --extract")
        return True

    _ensure_dir(_TR_OUTPUT_DIR)

    compiled = 0
    skipped = 0
    failed = 0

    print(f"[翻译] 扫描到 {len(ts_files)} 个 .ts 文件\n")

    for ts_file in ts_files:
        qm_file = _TR_OUTPUT_DIR / f"{ts_file.stem}.qm"

        if not force and not _needs_compile(ts_file, qm_file):
            skipped += 1
            print(f"  ⊘ 跳过（未变更）: {ts_file.name}")
            continue

        print(f"  ▸ 编译: {ts_file.name} → generated/{qm_file.name}")
        cmd = [lrelease_path, str(ts_file), "-qm", str(qm_file)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            compiled += 1
        except subprocess.CalledProcessError as e:
            print(f"  ✗ 编译失败: {ts_file.name}")
            if e.stderr:
                print(f"    错误信息: {e.stderr.strip()}")
            failed += 1

    print(f"\n[翻译] 完成: {compiled} 个编译, {skipped} 个跳过, {failed} 个失败")
    return failed == 0


def cmd_tr(extract: bool = False, compile_only: bool = False,
           force: bool = False, auto_rcc: bool = True,
           locations: bool = False) -> bool:
    """翻译工作流：提取 + 编译

    当翻译编译完成后，默认会自动触发 rcc 重新编译，
    因为 qrc 引用了 qm 翻译文件。

    Args:
        auto_rcc: 编译完成后是否自动触发 rcc 重新编译（默认 True）。
                  在 cmd_all 中由外部统一管理 rcc，此参数设为 False。
    """
    if extract and not compile_only:
        # 仅提取
        return cmd_tr_extract(locations=locations)
    elif compile_only and not extract:
        # 仅编译
        ok = cmd_tr_compile(force=force)
        if ok and auto_rcc:
            print()
            cmd_rcc()  # qm 变更后重新编译资源
        return ok
    else:
        # 默认：提取 + 编译
        ok1 = cmd_tr_extract(locations=locations)
        print()
        ok2 = cmd_tr_compile(force=True)  # 提取后强制重新编译
        if ok2 and auto_rcc:
            print()
            cmd_rcc()  # qm 变更后重新编译资源
        return ok1 and ok2


# ============================================================
# 项目打包（Nuitka）
# ============================================================

def _read_version() -> str:
    """从 src/__version__.py 中读取版本号

    Returns:
        版本号字符串（如 "0.7.2"），读取失败则返回空字符串
    """
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
        return match.group(1) if match else ""
    except OSError:
        return ""


def cmd_dist() -> bool:
    """使用 Nuitka 将项目打包为独立可执行文件

    打包前会自动执行全量构建（UI + 翻译 + 资源编译），
    确保所有编译产物就绪后再进行 Nuitka 编译。
    由于构建步骤有增量检测，开销很低，因此始终执行。

    Nuitka 的项目级配置（产品名、图标、插件等）已通过
    src/main.py 顶部的 # nuitka-project: 注释声明，
    此处无需重复指定。
    """
    # 检查 Nuitka 是否可用（通过当前 Python 解释器的 -m nuitka 调用）
    try:
        subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("✗ 未找到 Nuitka，请确认已安装:")
        print("  pip install nuitka")
        return False

    # 步骤 1：全量构建
    print("=" * 50)
    print("  步骤 1/2: 全量构建")
    print("=" * 50 + "\n")

    ok1 = cmd_ui(force=False)
    print()
    ok2 = cmd_tr(force=False, auto_rcc=False)
    print()
    ok0 = cmd_rcc(force=False)

    if not (ok0 and ok1 and ok2):
        print("\n✗ 构建步骤失败，中止打包。")
        return False
    print()

    # 步骤 2：Nuitka 打包
    print("=" * 50)
    print("  步骤 2/2: Nuitka 编译打包")
    print("=" * 50 + "\n")

    # 从 __version__.py 中读取版本号
    version = _read_version()
    if not version:
        print("✗ 无法从 __version__.py 中读取版本号")
        return False
    print(f"  产品版本: {version}\n")

    # 构建 Nuitka 命令
    # 项目级配置已在 main.py 中通过 # nuitka-project: 注释声明
    # --product-version 和 --include-data-files 由 build.py 动态注入
    cmd = [sys.executable, "-m", "nuitka",
           "--assume-yes-for-downloads",
           f"--product-version={version}"]
    # 内置脚本 .py 文件需作为数据打包（Nuitka 不认 .py 为数据文件），
    # 由 build.py 动态扫描，增删脚本无需手动同步
    builtins_dir = _PROJECT_ROOT / "src" / "builtins"
    for f in sorted(builtins_dir.glob("__*.py")):
        cmd.append(f"--include-data-files={f}=builtins/{f.name}")
    cmd.append(str(_MAIN_SCRIPT))

    print(f"  ▸ 执行: {' '.join(cmd)}\n")

    try:
        # Nuitka 输出较多，直接透传到终端
        result = subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
        if result.returncode != 0:
            print(f"\n✗ Nuitka 打包失败（退出码: {result.returncode}）")
            return False
    except FileNotFoundError:
        print("✗ 无法启动 Nuitka，请检查安装。")
        return False

    print(f"\n✓ 打包完成，输出目录: {_DIST_DIR.relative_to(_PROJECT_ROOT)}/")
    return True


# ============================================================
# 综合命令
# ============================================================

def cmd_all(force: bool = False):
    """构建全部：UI 编译 + 翻译编译 + 资源编译

    注意执行顺序：先编译 UI 和翻译，最后编译资源。
    因为 qrc 引用了 QSS 样式和 QM 翻译文件，
    需要确保这些文件就绪后再编译资源。
    """
    print("=" * 50)
    print("  构建全部")
    print("=" * 50 + "\n")

    ok1 = cmd_ui(force=force)
    print()
    ok2 = cmd_tr(force=force, auto_rcc=False)
    print()
    # 资源编译放在最后，因为 qrc 引用了 qss 和 qm 文件
    ok0 = cmd_rcc(force=force)

    print("\n" + "=" * 50)
    if ok0 and ok1 and ok2:
        print("  ✓ 全部构建成功")
    else:
        print("  ✗ 构建过程中存在错误")
    print("=" * 50)

    if not (ok0 and ok1 and ok2):
        sys.exit(1)


def cmd_clean():
    """清空所有构建产物"""
    cleaned = False

    # 清理资源编译产物
    if _RES_OUTPUT_FILE.exists():
        _RES_OUTPUT_FILE.unlink()
        print(f"已删除 [资源]: {_RES_OUTPUT_FILE.relative_to(_PROJECT_ROOT)}")
        cleaned = True

    for label, directory in [("UI", _UI_OUTPUT_DIR), ("翻译", _TR_OUTPUT_DIR)]:
        if directory.exists():
            shutil.rmtree(directory)
            print(f"已清空 [{label}]: {directory.relative_to(_PROJECT_ROOT)}")
            cleaned = True

    # 清理 Nuitka 打包产物
    if _DIST_DIR.exists():
        shutil.rmtree(_DIST_DIR)
        print(f"已清空 [打包]: {_DIST_DIR.relative_to(_PROJECT_ROOT)}")
        cleaned = True

    if cleaned:
        print("\n✓ 清理完成")
    else:
        print("没有需要清理的产物。")


def cmd_check():
    """检查哪些文件需要重新构建"""
    # 检查 UI 文件
    ui_files = _find_ui_files()
    ui_needs = []
    ui_ok = []
    for f in ui_files:
        if _needs_compile(f, _ui_output_path(f)):
            ui_needs.append(f)
        else:
            ui_ok.append(f)

    print("[UI 文件]")
    if ui_needs:
        print(f"  需要编译 ({len(ui_needs)}):")
        for f in ui_needs:
            print(f"    ▸ {f.name}")
    else:
        print("  所有文件均为最新。")
    if ui_ok:
        print(f"  已是最新 ({len(ui_ok)}):")
        for f in ui_ok:
            print(f"    ✓ {f.name}")

    # 检查资源文件
    print("\n[资源文件]")
    if _RES_QRC_FILE.exists():
        if _needs_compile(_RES_QRC_FILE, _RES_OUTPUT_FILE):
            print(f"  需要编译: {_RES_QRC_FILE.name}")
        else:
            print(f"  已是最新: {_RES_QRC_FILE.name}")
    else:
        print("  未找到 .qrc 文件。")

    # 检查翻译文件
    ts_files = sorted(_TR_DIR.glob("*.ts"))
    tr_needs = []
    tr_ok = []
    for f in ts_files:
        qm_file = _TR_OUTPUT_DIR / f"{f.stem}.qm"
        if _needs_compile(f, qm_file):
            tr_needs.append(f)
        else:
            tr_ok.append(f)

    print("\n[翻译文件]")
    if not ts_files:
        print("  未找到 .ts 文件，请先运行: python build.py tr --extract")
    elif tr_needs:
        print(f"  需要编译 ({len(tr_needs)}):")
        for f in tr_needs:
            print(f"    ▸ {f.name}")
    else:
        print("  所有文件均为最新。")
    if tr_ok:
        print(f"  已是最新 ({len(tr_ok)}):")
        for f in tr_ok:
            print(f"    ✓ {f.name}")


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
description="HawkingHand 项目构建脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python build.py                 构建全部（UI + 翻译 + 资源编译）
  python build.py rcc             仅编译 .qrc 资源文件
  python build.py rcc --force     强制重新编译资源
  python build.py ui              仅编译 .ui 文件
  python build.py tr              提取翻译字符串并编译 .qm（自动触发 rcc）
  python build.py tr --extract    仅提取/更新 .ts 文件
  python build.py tr --compile    仅编译 .ts → .qm 文件（自动触发 rcc）
  python build.py tr --locations  提取时保留源码位置信息（默认不保留）
  python build.py dist            打包项目（自动先执行全量构建）
  python build.py clean           清空所有构建产物
  python build.py check           仅检查，不执行构建
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    # 子命令: rcc
    rcc_parser = subparsers.add_parser("rcc", help="编译 .qrc 资源文件为 Python 模块")
    rcc_parser.add_argument("--force", action="store_true", help="强制重新编译")

    # 子命令: ui
    ui_parser = subparsers.add_parser("ui", help="编译 .ui 文件为 Python 模块")
    ui_parser.add_argument("--force", action="store_true", help="强制重新编译所有文件")

    # 子命令: tr
    tr_parser = subparsers.add_parser("tr", help="翻译提取与编译")
    tr_group = tr_parser.add_mutually_exclusive_group()
    tr_group.add_argument("--extract", action="store_true", help="仅提取/更新 .ts 文件")
    tr_group.add_argument("--compile", action="store_true", help="仅编译 .ts → .qm 文件")
    tr_parser.add_argument("--force", action="store_true", help="强制重新编译")
    tr_parser.add_argument("--locations", action="store_true",
                           help="提取时保留源码位置信息（默认不保留，避免行号变动导致无意义的 diff）")

    # 子命令: dist
    subparsers.add_parser("dist", help="使用 Nuitka 打包项目为可执行文件")

    # 子命令: clean
    subparsers.add_parser("clean", help="清空所有构建产物")

    # 子命令: check
    subparsers.add_parser("check", help="仅检查哪些文件需要重新构建")

    args = parser.parse_args()

    if args.command == "rcc":
        if not cmd_rcc(force=args.force):
            sys.exit(1)
    elif args.command == "ui":
        if not cmd_ui(force=args.force):
            sys.exit(1)
    elif args.command == "tr":
        if not cmd_tr(extract=args.extract, compile_only=args.compile,
                     force=args.force, locations=args.locations):
            sys.exit(1)
    elif args.command == "dist":
        if not cmd_dist():
            sys.exit(1)
    elif args.command == "clean":
        cmd_clean()
    elif args.command == "check":
        cmd_check()
    else:
        # 无子命令：构建全部
        cmd_all()


if __name__ == "__main__":
    main()
