"""Cross-platform resource and writable application data paths."""

import os
import shutil
import sys

APP_NAME = "LQPriceCheck"


def resource_path(relative_path):
    """Return a bundled resource path in development or PyInstaller mode."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def user_data_dir():
    """Return the per-user writable application directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, APP_NAME)


def data_dir():
    path = os.path.join(user_data_dir(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def learning_db_path():
    override = os.environ.get("OCR_LEARNING_DB")
    if override:
        return override
    return os.path.join(data_dir(), "ocr_learning.db")


def ensure_learning_db_seed():
    """Copy a bundled learning DB once, without overwriting user data."""
    target = learning_db_path()
    if os.path.exists(target) or os.environ.get("OCR_LEARNING_DB"):
        return target
    source = resource_path(os.path.join("data", "ocr_learning.db"))
    if os.path.exists(source):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    return target


def legacy_shop_city_db_path():
    """Return the bundled legacy DB used only by the one-time migration."""
    return resource_path(os.path.join("data", "shop_city.db"))
