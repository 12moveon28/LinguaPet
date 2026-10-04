"""统一路径解析。

- 开发时：项目根目录（本文件的上两级）。
- PyInstaller onefile 运行时：
  - 可写数据（config.json / data / logs）放在 sys.executable 同级，保证便携；
  - 随包只读资源（builtin_dict.json / assets）从 sys._MEIPASS 读取。
- kd.exe 永远只从“程序目录”查找，不随包打包。
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN: bool = bool(getattr(sys, "frozen", False))

if FROZEN:
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = BASE_DIR

# 可写路径（exe 同级 / 项目根）
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "tts_cache"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config.json"
WRONG_WORDS_PATH = DATA_DIR / "wrong_words.json"

# 只读资源（随包打到 _MEIPASS）
BUILTIN_DICT_PATH = RESOURCE_DIR / "data" / "builtin_dict.json"
ASSETS_DIR = RESOURCE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"

# 外部词典程序：与最终 exe（开发时与项目根）同目录
KD_EXE_PATH = BASE_DIR / "kd.exe"


def ensure_dirs() -> None:
    """确保运行时可写目录存在；失败只抛给调用方记日志，不静默。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
