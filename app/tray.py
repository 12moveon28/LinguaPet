"""系统托盘：显示/隐藏、错题本、开机启动开关、退出。只有"退出"才结束进程。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from . import autostart
from .logger import get_logger
from .paths import ICON_PATH

log = get_logger("tray")


def _load_icon() -> QIcon:
    icon = QIcon(str(ICON_PATH))
    if icon.isNull():
        log.warning("托盘图标加载失败：%s，使用系统内置图标", ICON_PATH)
        style = QWidget().style()
        return style.standardIcon(style.SP_ComputerIcon)
    return icon


class TrayController(QSystemTrayIcon):
    def __init__(self, parent: QWidget | None = None, config=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setIcon(_load_icon())
        self.setToolTip("LinguaPet 单词桌宠")

        menu = QMenu()

        self.action_show = QAction("显示/隐藏桌宠", menu)
        self.action_show.triggered.connect(self._on_toggle_show)
        menu.addAction(self.action_show)

        menu.addSeparator()

        self.action_wrong_book = QAction("打开错题本", menu)
        self.action_wrong_book.triggered.connect(self.wrongBookRequested.emit)
        menu.addAction(self.action_wrong_book)

        self.action_autostart = QAction("开机启动", menu, checkable=True)
        # 以注册表实际状态为准，同步回配置
        actual = autostart.is_enabled()
        self.action_autostart.setChecked(actual)
        if config is not None:
            config.update(autostart=actual)
            config.save()
        self.action_autostart.toggled.connect(self._on_autostart_toggled)
        menu.addAction(self.action_autostart)

        menu.addSeparator()

        self.action_quit = QAction("退出", menu)
        self.action_quit.triggered.connect(self.quitRequested.emit)
        menu.addAction(self.action_quit)

        self.setContextMenu(menu)

        # 双击托盘图标切换显示
        self.activated.connect(self._on_activated)

    # 由外部注入的事件
    wrongBookRequested = Signal()
    quitRequested = Signal()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._on_toggle_show()

    # 由 main 注入的回调（避免 tray 直接依赖 pet 实例）
    def set_show_hide_handler(self, handler) -> None:
        self._show_hide_handler = handler

    def _on_toggle_show(self) -> None:
        handler = getattr(self, "_show_hide_handler", None)
        if handler is not None:
            handler()

    def _on_autostart_toggled(self, checked: bool) -> None:
        ok = autostart.set_enabled(checked)
        real_state = autostart.is_enabled()
        # 若写入失败，回退勾选状态到真实值
        if not ok or real_state != checked:
            self.action_autostart.blockSignals(True)
            self.action_autostart.setChecked(real_state)
            self.action_autostart.blockSignals(False)
            self.showMessage("LinguaPet", "开机启动设置失败，详情见日志", QSystemTrayIcon.Warning)
        if self._config is not None:
            self._config.update(autostart=real_state)
            self._config.save()
        log.info("开机启动开关状态：%s", real_state)

    def notify(self, title: str, message: str, msec: int = 2500) -> None:
        """安全弹气泡：系统关闭托盘通知时不崩。"""
        try:
            if self.supportsMessages():
                self.showMessage(title, message, QSystemTrayIcon.Information, msec)
        except Exception as exc:
            log.warning("托盘通知失败：%s", exc)
