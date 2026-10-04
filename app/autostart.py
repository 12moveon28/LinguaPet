"""开机自启动：HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run。

- 打包后：注册 sys.executable（LinguaPet.exe）。
- 开发时：注册 "<pythonw.exe>" "<main.py 绝对路径>"。
仅写 HKCU（当前用户），不需要管理员权限。任何失败返回 False 并记日志，不抛异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .logger import get_logger
from .paths import FROZEN

log = get_logger("autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LinguaPet"


def _build_command() -> str:
    """构造注册表 Run 值（带引号，兼容含空格路径）。"""
    if FROZEN:
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(pyw if pyw.exists() else sys.executable)
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{interpreter}" "{main_py}"'


def is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        log.error("当前平台无 winreg，开机启动不可用")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.error("读取开机启动状态失败：%s", exc)
        return False


def enable() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    command = _build_command()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        log.info("开机启动已开启：%s", command)
        return True
    except OSError as exc:
        log.error("写入开机启动失败：%s", exc)
        return False


def disable() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        log.info("开机启动已关闭")
        return True
    except FileNotFoundError:
        # 本来就没有，视为关闭成功
        return True
    except OSError as exc:
        log.error("删除开机启动项失败：%s", exc)
        return False


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
