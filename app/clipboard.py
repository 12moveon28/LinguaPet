"""取词链路：模拟 Ctrl+C → 等待 → 读剪贴板 → 清洗校验。

约定（已与用户确认）：
- 不还原剪贴板原内容（快速连按 F2 时还原反而互相干扰）；
- 剪贴板为空或清洗后长度 > 64 字符 → 返回 None，调用方保留上一个当前词；
- 内部空白/换行/制表符折叠为单个空格，接受短语（kd.exe 支持词组）。
"""
from __future__ import annotations

from .logger import get_logger

log = get_logger("clipboard")

MAX_LEN = 64
COPY_WAIT_MS = 150

_controller = None  # 惰性创建，避免无输入环境下导入即失败


def _get_controller():
    global _controller
    if _controller is None:
        from pynput.keyboard import Controller

        _controller = Controller()
    return _controller


def send_copy() -> bool:
    """向当前前台窗口发送 Ctrl+C。任何失败返回 False 并记日志，不抛异常。"""
    try:
        from pynput.keyboard import Key

        kb = _get_controller()
        kb.press(Key.ctrl)
        kb.press("c")
        kb.release("c")
        kb.release(Key.ctrl)
        return True
    except Exception as exc:
        log.error("模拟 Ctrl+C 失败：%s", exc)
        return False


def normalize_selected_text(raw: str) -> str | None:
    """清洗选中内容：折叠空白、去首尾；空或超长（>64）返回 None。"""
    if not raw:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) > MAX_LEN:
        log.info("选中内容超长（%s 字符 > %s），忽略：%r", len(text), MAX_LEN, text[:30])
        return None
    return text


def read_selection() -> str | None:
    """读取并清洗当前剪贴板内容；无效返回 None。"""
    try:
        import pyperclip

        raw = pyperclip.paste()
    except Exception as exc:
        log.error("读取剪贴板失败：%s", exc)
        return None
    if raw is None:
        return None
    text = normalize_selected_text(str(raw))
    if text is None:
        log.info("剪贴板为空或内容不合规，本次取词忽略（保留上一个词）")
    return text
