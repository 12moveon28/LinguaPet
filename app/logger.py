"""日志：控制台 + logs/lingua_pet.log，按 1MB x 3 轮转。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import LOGS_DIR

_LOGGER_NAME = "lingua_pet"
_initialized = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化根 logger，可重复调用。"""
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    if _initialized:
        return logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOGS_DIR / "lingua_pet.log",
        maxBytes=1 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # 开发期控制台输出；windowed 打包后无控制台，该 handler 无副作用
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    _initialized = True
    logger.info("日志系统初始化完成：%s", LOGS_DIR / "lingua_pet.log")
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """取子 logger，名称统一挂在 lingua_pet 命名空间下。"""
    if name is None or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(_LOGGER_NAME).getChild(name)
