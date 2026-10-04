"""全局热键管理（pynput 低级键盘钩子）。

- 配置写法："f2"、"ctrl+alt+e"、"ctrl+alt+w"（不区分大小写）
- pynput 写法："<f2>"、"<ctrl>+<alt>+e"，本模块负责转换与校验
- 回调发生在 pynput 监听线程，通过 Qt 信号以队列方式投递到主线程，
  任何回调异常都在监听线程内吞掉并记日志，绝不让监听线程崩掉
"""
from __future__ import annotations

import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

from .logger import get_logger

log = get_logger("hotkey")

# Shift/Ctrl/Alt 的左右键虚拟键码
_MODIFIER_VKS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
_MODIFIER_WAIT_TIMEOUT = 2.0  # 用户一直按住不松手时的兜底超时（秒）
_POLL_INTERVAL = 0.015


def _any_modifier_physically_down() -> bool:
    """查询 Shift/Ctrl/Alt 当前是否有任意一个处于物理按下状态。

    非 Windows 返回 False（立即触发，不额外等待）。
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    for vk in _MODIFIER_VKS:
        # GetAsyncKeyState 返回 short，最高位为 1 表示当前按下
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def to_pynput_spec(spec: str) -> str:
    """'ctrl+alt+e' -> '<ctrl>+<alt>+e'；'f2' -> '<f2>'。

    单字符按键（如 e、1）保持原样，多字符名称包尖括号。
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"非法热键配置：{spec!r}")
    out = [p if len(p) == 1 else f"<{p}>" for p in parts]
    result = "+".join(out)
    # 用 pynput 自身的解析器校验合法性
    from pynput.keyboard import HotKey

    HotKey.parse(result)
    return result


class HotkeyManager(QObject):
    """name -> 配置热键 的多热键监听。"""

    hotkeyPressed = Signal(str)   # 参数：热键逻辑名
    registerFailed = Signal(str, str)  # 参数：逻辑名、原因

    def __init__(self, bindings: dict[str, str] | None = None) -> None:
        super().__init__()
        # name -> 用户配置 spec
        self._bindings: dict[str, str] = dict(bindings or {})
        self._listener = None
        self._pynput_specs: dict[str, str] = {}

    def update_bindings(self, bindings: dict[str, str]) -> None:
        """更换热键配置；监听运行中则重启。"""
        running = self._listener is not None
        if running:
            self.stop()
        self._bindings = dict(bindings)
        if running:
            self.start()

    def start(self) -> bool:
        """启动监听。全部热键非法/启动失败时返回 False（不崩）。"""
        if self._listener is not None:
            return True
        try:
            from pynput.keyboard import GlobalHotKeys
        except Exception as exc:
            log.error("pynput 不可用，全局热键失效：%s", exc)
            for name in self._bindings:
                self.registerFailed.emit(name, f"pynput 不可用：{exc}")
            return False

        hotkeys: dict[str, object] = {}
        for name, spec in self._bindings.items():
            try:
                pynput_spec = to_pynput_spec(spec)
            except Exception as exc:
                log.error("热键 %r 配置非法（%s）：%r", name, exc, spec)
                self.registerFailed.emit(name, f"热键配置非法：{spec}")
                continue
            hotkeys[pynput_spec] = self._make_callback(name)
            self._pynput_specs[name] = pynput_spec

        if not hotkeys:
            log.error("没有可注册的有效热键")
            return False

        try:
            self._listener = GlobalHotKeys(hotkeys)
            self._listener.start()
        except Exception as exc:
            log.error("全局热键监听启动失败：%s", exc)
            for name in self._bindings:
                self.registerFailed.emit(name, f"监听启动失败：{exc}")
            self._listener = None
            return False

        log.info("全局热键已注册：%s", self._pynput_specs)
        return True

    def _make_callback(self, name: str):
        def callback() -> None:
            try:
                log.info("触发热键：%s", name)
                # 必须在独立线程等待：修饰键抬起事件由 pynput 监听线程分发，
                # 在监听线程内等待会死锁
                threading.Thread(
                    target=self._fire_after_modifiers_up,
                    args=(name,),
                    name=f"hotkey-fire-{name}",
                    daemon=True,
                ).start()
            except Exception as exc:
                # pynput 线程内兜底
                log.error("热键 %s 回调异常：%s", name, exc)

        return callback

    def _fire_after_modifiers_up(self, name: str) -> None:
        """等物理修饰键全部抬起后再发信号。

        修复带修饰键的热键（如 ctrl+alt+e）：回调在最后一个普通键按下瞬间
        触发，此时 Ctrl/Alt 仍按着，立刻模拟 Ctrl+C 会让前台程序收到
        Ctrl+Alt+C 而复制失败。无修饰键的热键（如 F2）第一次轮询即放行。
        """
        waited = False
        deadline = time.monotonic() + _MODIFIER_WAIT_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if not _any_modifier_physically_down():
                    break
                waited = True
                time.sleep(_POLL_INTERVAL)
            if waited:
                # 让抬起消息先送达前台窗口，避免注入的 Ctrl 与残像粘连
                time.sleep(0.03)
                log.info("热键 %s 已等待修饰键释放", name)
        except Exception as exc:
            log.warning("热键 %s 等待修饰键状态异常（直接触发）：%s", name, exc)
        self.hotkeyPressed.emit(name)

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as exc:
                log.warning("停止热键监听异常：%s", exc)
            self._listener = None
            log.info("全局热键监听已停止")
