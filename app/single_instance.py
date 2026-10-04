"""单实例保护（QLocalServer）。

第二次启动时探测到已存在的本地命名服务，发送一条 "raise" 消息后退出；
已有实例收到消息后发出 activated 信号（用于唤出宠物窗）。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "LinguaPet-SingleInstance-0d6f1a"
MUTEX_NAME = "Local\\LinguaPet-SingleInstance-Mutex-0d6f1a"
ERROR_ALREADY_EXISTS = 183


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, name: str = SERVER_NAME) -> None:
        super().__init__()
        self._name = name
        self._server: QLocalServer | None = None
        self._mutex_handle = None
        mutex_exists = self._acquire_mutex()
        pipe_connected = self._probe_existing()
        # Mutex 是强保险：管道未就绪时也能挡住双开
        self.is_second = pipe_connected or mutex_exists
        if mutex_exists and not pipe_connected:
            from .logger import get_logger

            get_logger("single_instance").warning(
                "检测到已有实例的 Mutex，但命名管道未响应"
            )

    def _acquire_mutex(self) -> bool:
        """创建命名 Mutex。返回 True 表示已被别的实例持有。"""
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
            self._mutex_handle = handle  # 保持引用，进程结束由 OS 释放
            return ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        except Exception as exc:
            from .logger import get_logger

            get_logger("single_instance").warning("Mutex 创建失败（仅靠管道判重）：%s", exc)
            return False

    def _probe_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        connected = socket.waitForConnected(300)
        if connected:
            try:
                socket.write(b"raise")
                socket.flush()
                socket.waitForBytesWritten(500)
            finally:
                socket.disconnectFromServer()
        socket.deleteLater()
        return bool(connected)

    def acquire(self) -> bool:
        """首个实例调用：清理残留套接字并开始监听。"""
        QLocalServer.removeServer(self._name)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        ok = self._server.listen(self._name)
        if not ok:
            # 失败不致命：退化为允许多开，但记日志
            from .logger import get_logger

            get_logger("single_instance").error(
                "单实例服务监听失败：%s", self._server.errorString()
            )
        return ok

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        try:
            if socket.waitForReadyRead(300):
                payload = bytes(socket.readAll()).decode("utf-8", "ignore")
                if "raise" in payload:
                    self.activated.emit()
        finally:
            socket.disconnectFromServer()
            socket.deleteLater()
