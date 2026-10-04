"""错题本窗口：搜索、朗读、删除。

- QListWidget 自定义行：单词 / 音标 / 释义 / 加入时间
- 双击行朗读；右键菜单（朗读/删除）；Delete 键删除；Esc 关闭
- 顶部搜索框按单词/音标/释义实时过滤
- 每次 show 自动从 WrongBook 重新加载（外部热键/弹窗改动立即可见）
- 删除直接落盘（WrongBook.remove），窗口只负责展示与发出朗读信号
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .logger import get_logger

log = get_logger("wrong_win")

WINDOW_W = 440
WINDOW_H = 560
ROW_MEANING_W = 348
WORD_ROLE = Qt.UserRole


class _WordRow(QFrame):
    """单行：单词+音标+时间 / 自动换行的释义。高度按释义行数计算。"""

    def __init__(self, record: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wordRow")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        word = QLabel(record.get("word", ""))
        word.setObjectName("rowWord")
        phonetic = QLabel(record.get("phonetic", ""))
        phonetic.setObjectName("rowPhonetic")
        added = QLabel(record.get("added_at", ""))
        added.setObjectName("rowTime")
        top.addWidget(word, 0, Qt.AlignLeft)
        top.addWidget(phonetic, 1, Qt.AlignLeft)
        top.addWidget(added, 0, Qt.AlignRight)
        root.addLayout(top)

        meaning_text = record.get("meaning", "") or "（暂无释义）"
        meaning = QLabel(meaning_text)
        meaning.setObjectName("rowMeaning" if record.get("meaning") else "rowMeaningEmpty")
        meaning.setFixedWidth(ROW_MEANING_W)
        meaning.setWordWrap(True)
        fm = QFontMetrics(meaning.font())
        rect = fm.boundingRect(
            0, 0, ROW_MEANING_W, 0,
            Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
            meaning_text,
        )
        meaning.setMinimumHeight(max(20, rect.height() + 2))
        meaning.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(meaning)


class WrongBookWindow(QWidget):
    """错题本主窗口。复用同一实例：关闭只是隐藏，托盘常驻不退出。"""

    speakRequested = Signal(str)  # 请求朗读单词

    def __init__(self, wrong_book, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wb = wrong_book
        self._items: list[dict] = []
        self._centered = False
        self.setWindowTitle("错题本 - LinguaPet")
        self.resize(WINDOW_W, WINDOW_H)
        self.setMinimumSize(360, 360)
        self._init_ui()

    # ---------------- UI ----------------
    def _init_ui(self) -> None:
        self.setStyleSheet(
            """
            WrongBookWindow, QWidget { background: #f7f9fc; }
            QLabel#title { font-size: 17px; font-weight: 700; color: #1f2733; }
            QLabel#count { font-size: 12px; color: #6b7484; }
            QLineEdit {
                border: 1px solid #cfd6e2; border-radius: 8px;
                padding: 7px 10px; font-size: 13px; background: #ffffff;
            }
            QLineEdit:focus { border: 1px solid #3a6ea5; }
            QListWidget {
                background: #ffffff; border: 1px solid #d9dee8;
                border-radius: 10px; outline: none;
            }
            QListWidget::item { border-bottom: 1px solid #eef1f5; }
            QListWidget::item:selected { background: #eaf1f8; }
            QFrame#wordRow { background: transparent; }
            QLabel#rowWord { font-size: 15px; font-weight: 700; color: #1f2733; }
            QLabel#rowPhonetic { font-size: 12px; color: #6b7484; }
            QLabel#rowTime { font-size: 11px; color: #9aa4b2; }
            QLabel#rowMeaning { font-size: 13px; color: #2b3240; }
            QLabel#rowMeaningEmpty { font-size: 13px; color: #9aa4b2; font-style: italic; }
            QLabel#emptyHint {
                font-size: 14px; color: #9aa4b2; background: transparent;
            }
            QLabel#footerHint { font-size: 12px; color: #8a93a3; }
            QPushButton {
                border: 1px solid #cfd6e2; border-radius: 8px;
                background: #f5f7fa; color: #2b3240;
                padding: 6px 14px; font-size: 13px;
            }
            QPushButton:hover { background: #eaeff6; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("📕 错题本")
        title.setObjectName("title")
        self.count_label = QLabel("")
        self.count_label.setObjectName("count")
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(self.count_label)
        header.addStretch(1)
        root.addLayout(header)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索单词、音标或释义…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._rebuild_list)
        root.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.itemDoubleClicked.connect(self._on_double_clicked)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self.list_widget, 1)

        self.empty_label = QLabel("还没有错题\n选中单词后按 Ctrl+Alt+E 即可加入", self.list_widget)
        self.empty_label.setObjectName("emptyHint")
        self.empty_label.setAlignment(Qt.AlignCenter)

        footer = QHBoxLayout()
        hint = QLabel("双击朗读 · 右键 / Delete 删除 · Esc 关闭")
        hint.setObjectName("footerHint")
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        footer.addWidget(hint)
        footer.addStretch(1)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

        # 列表持有焦点时 Delete 删除当前词条（事件过滤器直连，
        # 不依赖窗口活动状态；搜索框中的 Delete 是文本编辑，不会到达列表）
        self.list_widget.installEventFilter(self)

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        """从数据层重新加载并重建列表。"""
        try:
            self._items = self._wb.all()
        except Exception as exc:
            log.error("读取错题本失败：%s", exc)
            self._items = []
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        keyword = self.search_box.text().strip().lower()
        if keyword:
            filtered = [
                it for it in self._items
                if keyword in it.get("word", "").lower()
                or keyword in it.get("phonetic", "").lower()
                or keyword in it.get("meaning", "").lower()
            ]
        else:
            filtered = list(self._items)

        self.list_widget.clear()
        for record in filtered:
            item = QListWidgetItem(self.list_widget)
            item.setData(WORD_ROLE, record.get("word", ""))
            row = _WordRow(record)
            item.setSizeHint(row.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)

        total = len(self._items)
        if keyword:
            self.count_label.setText(f"{len(filtered)} / {total} 条")
        else:
            self.count_label.setText(f"共 {total} 条")
        self.empty_label.setVisible(len(filtered) == 0)
        self._position_empty_hint()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_empty_hint()

    def _position_empty_hint(self) -> None:
        if self.empty_label.isVisible():
            self.empty_label.setGeometry(self.list_widget.rect())

    # ---------------- 交互 ----------------
    def _current_word(self) -> str:
        item = self.list_widget.currentItem()
        if item is None:
            return ""
        return str(item.data(WORD_ROLE) or "")

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        word = str(item.data(WORD_ROLE) or "")
        if word:
            log.info("错题本双击朗读：%s", word)
            self.speakRequested.emit(word)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        word = str(item.data(WORD_ROLE) or "")
        menu = QMenu(self)
        action_speak = menu.addAction("🔊 朗读")
        action_delete = menu.addAction("🗑️ 删除")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen == action_speak:
            self.speakRequested.emit(word)
        elif chosen == action_delete:
            self._delete_word(word)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.list_widget and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Delete:
                self._delete_current()
                return True
        return super().eventFilter(obj, event)

    def _delete_current(self) -> None:
        word = self._current_word()
        if word:
            self._delete_word(word)

    def _delete_word(self, word: str) -> None:
        try:
            ok = self._wb.remove(word)
        except Exception as exc:
            log.error("删除错题失败：%s：%s", word, exc)
            ok = False
        if ok:
            log.info("已从错题本删除：%s", word)
            self.refresh()
        else:
            log.warning("删除未生效（可能已不存在）：%s", word)
            self.refresh()

    # ---------------- 窗口行为 ----------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        if not self._centered:
            self._center_on_screen()
            self._centered = True

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = avail.center().x() - self.width() // 2
        y = avail.center().y() - self.height() // 2
        self.move(max(avail.left(), x), max(avail.top(), y))

    def bring_to_front(self) -> None:
        """托盘/热键唤起：取消最小化、前置并聚焦搜索框。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_box.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
