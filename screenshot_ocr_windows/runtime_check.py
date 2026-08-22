"""
运行时自检与资源定位（PyInstaller 单文件打包支持）

功能：
  1. resource_path() - 兼容开发环境与 PyInstaller _MEIPASS 解压目录的资源定位
  2. get_user_data_dir() - 用户可写数据目录 %LOCALAPPDATA%\\LQPriceCheck
  3. ensure_db() - 首次启动把内嵌 shop_city.db 复制到用户数据目录
  4. run_preflight() - 启动前自检（平台/磁盘/资源/VC++运行库/OCR动态库）
"""

import ctypes
import logging
import logging.handlers
import os
import platform
import shutil
import subprocess
import sys

APP_NAME = "LQPriceCheck"


def get_log_dir():
    path = os.path.join(get_user_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def get_log_path():
    return os.path.join(get_log_dir(), "application.log")


def configure_logging():
    """Configure rotating file logging for the windowed EXE."""
    log_path = get_log_path()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_lqpricecheck", False) for h in root.handlers):
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler._lqpricecheck = True
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(process)d/%(threadName)s] "
            "%(name)s: %(message)s"
        ))
        root.addHandler(handler)
    logging.getLogger(__name__).info(
        "startup frozen=%s python=%s platform=%s exe=%s cwd=%s data=%s log=%s",
        is_frozen(), sys.version.replace("\n", " "), sys.platform,
        sys.executable, os.getcwd(), get_user_data_dir(), log_path,
    )
    return log_path


def install_exception_logging():
    """Persist uncaught exceptions that would otherwise close a windowed EXE."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("uncaught").critical(
            "unhandled exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
    sys.excepthook = handle_exception


def resource_path(relative):
    """兼容开发环境与 PyInstaller 打包的资源路径

    PyInstaller onefile 模式下，内嵌资源解压到 sys._MEIPASS 临时目录；
    开发模式下直接用 __file__ 所在目录。

    Args:
        relative: 相对路径，如 "模板.xlsx" 或 os.path.join("data", "shop_city.db")
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative)


def is_frozen():
    """是否处于 PyInstaller 打包运行状态"""
    return hasattr(sys, "_MEIPASS")


def get_user_data_dir():
    """用户可写数据目录: %LOCALAPPDATA%\\LQPriceCheck"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def get_db_path():
    """返回店铺城市数据库路径

    打包模式: %LOCALAPPDATA%\\LQPriceCheck\\data\\shop_city.db（首次从内嵌复制）
    开发模式: 项目 data\\shop_city.db（直接读写）
    """
    if is_frozen():
        return os.path.join(get_user_data_dir(), "data", "shop_city.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shop_city.db")


def get_db_dir():
    """返回数据库所在目录"""
    return os.path.dirname(get_db_path())


def ensure_db():
    """确保店铺城市数据库存在（首次启动从内嵌资源复制到用户数据目录）

    Returns:
        str: 数据库路径
    """
    dst = get_db_path()
    if os.path.exists(dst):
        return dst

    src = resource_path(os.path.join("data", "shop_city.db"))
    if os.path.exists(src):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as e:
            raise RuntimeError(f"无法创建数据库文件: {e}")
    else:
        # 内嵌资源缺失（非打包模式直接建空库）
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    return dst


def get_desktop_dir():
    """桌面目录（导出 Excel 默认位置），无桌面则回退到用户数据目录 output"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return desktop
    # 中文系统桌面可能叫"桌面"
    desktop_zh = os.path.join(os.path.expanduser("~"), "桌面")
    if os.path.isdir(desktop_zh):
        return desktop_zh
    out = os.path.join(get_user_data_dir(), "output")
    os.makedirs(out, exist_ok=True)
    return out


def _show_error(title, message):
    """用原生 MessageBox 显示错误（不依赖 QApplication）"""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    else:
        print(f"[ERROR] {title}: {message}")


def _check_vc_redist():
    """检测 VC++ 2015-2022 x64 运行库是否安装（注册表检测）"""
    import winreg

    paths = [
        r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    ]
    for hive, hive_name in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"),
                            (winreg.HKEY_CURRENT_USER, "HKCU")):
        for path in paths:
            try:
                with winreg.OpenKey(hive, path) as key:
                    installed, _ = winreg.QueryValueEx(key, "Installed")
                    if installed == 1:
                        return True
            except OSError:
                continue
    return False


def _check_ocr_importable():
    """尝试导入 OCR 动态库，返回 (ok, error_msg)"""
    try:
        import onnxruntime  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
        return True, ""
    except ImportError as e:
        msg = str(e)
        if "DLL load failed" in msg or "onnxruntime_pybind11_state" in msg:
            return False, (
                "OCR 动态库加载失败：缺少 Microsoft Visual C++ 2015-2022 "
                "Redistributable (x64)。\n\n"
                "请安装后重新运行：\n"
                "https://aka.ms/vs/17/release/vc_redist.x64.exe"
            )
        return False, f"OCR 组件导入失败: {msg}"
    except Exception as e:
        return False, f"OCR 组件初始化失败: {e}"


def run_preflight():
    """启动前自检

    检查项：
      1. 平台：Windows 10/11、64 位、x86_64
      2. 临时目录可写且可用空间 >= 1GB
      3. 内嵌资源存在（模板.xlsx、初始数据库）
      4. VC++ 2015-2022 x64 运行库
      5. OCR 动态库可导入

    失败时弹中文错误对话框并退出（返回 False）；全部通过返回 True。
    """
    # 1. 平台检查
    if sys.platform != "win32":
        _show_error("平台不兼容", "本程序仅支持 Windows 10/11 64 位系统。")
        return False

    machine = platform.machine()
    if machine not in ("AMD64", "x86_64"):
        _show_error("架构不兼容", f"本程序仅支持 64 位系统（当前架构: {machine}）。")
        return False

    # 2. 临时目录可写 + 磁盘空间
    try:
        temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or "."
        if not os.path.isdir(temp_dir):
            temp_dir = "."
        probe = os.path.join(temp_dir, "_lqcheck_probe.tmp")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)

        usage = shutil.disk_usage(temp_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1.0:
            _show_error(
                "磁盘空间不足",
                f"临时目录可用空间不足 1GB（当前约 {free_gb:.1f}GB）。\n\n"
                "请清理磁盘空间后重新启动。",
            )
            return False
    except OSError as e:
        _show_error("临时目录不可写", f"无法写入临时目录：{e}\n\n请检查系统权限。")
        return False

    # 3. 内嵌资源存在
    template = resource_path("模板.xlsx")
    if not os.path.exists(template):
        _show_error("资源缺失", f"找不到模板文件：\n{template}")
        return False

    # 4. VC++ 运行库（打包模式才检测；开发环境装了 Python 一般已有）
    if is_frozen():
        try:
            if not _check_vc_redist():
                _show_error(
                    "缺少运行库",
                    "未检测到 Microsoft Visual C++ 2015-2022 Redistributable (x64)。\n\n"
                    "请安装后重新运行：\n"
                    "https://aka.ms/vs/17/release/vc_redist.x64.exe",
                )
                return False
        except Exception:
            # 注册表检测失败不阻塞启动，OCR 导入时会再兜底
            pass

    # 5. OCR 动态库可导入
    ok, err = _check_ocr_importable()
    if not ok:
        _show_error("OCR 组件异常", err)
        return False

    # 6. 确保数据库就绪
    try:
        ensure_db()
    except RuntimeError as e:
        _show_error("数据库初始化失败", str(e))
        return False

    return True
