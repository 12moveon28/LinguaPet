"""释义弹窗：无边框圆角小窗，定位在宠物旁并做屏幕边界检测。

- 显示：单词、音标、中文释义、查询状态
- 按钮：朗读 / 加入错题本 / 关闭
- ESC 关闭
- 查询状态由外部（main）编排后调用 show_loading / show_entry / show_missing；
  本组件只负责展示与发出动作信号。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .dictionary import DictEntry
from .logger import get_logger

log = get_logger("popup")

POPUP_WIDTH = 300
MEANING_WIDTH = 260
GAP = 10

STATUS_TEXT = {
    "kd": "来源：kd.exe",
    "builtin": "来源：内置词库",
    "none": "",
    "timeout": "未找到释义（kd 超时，已查内置词库）",
    "error": "未找到释义（词典查询失败）",
}


class DefinitionPopup(QWidget):
    speakRequested = Signal(str)
    addRequested = Signal(str, str, str)  # word, phonetic, meaning

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(POPUP_WIDTH)
        self._word = ""

        self._init_ui()

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(
            """
            QFrame#card {
                background: #ffffff;
                border: 1px solid #d9dee8;
                border-radius: 12px;
            }
            QLabel { background: transparent; }
            QLabel#word { font-size: 20px; font-weight: 700; color: #1f2733; }
            QLabel#phonetic { font-size: 13px; color: #6b7484; }
            QLabel#meaning { font-size: 14px; color: #2b3240; }
            QLabel#status { font-size: 12px; color: #8a93a3; }
            QPushButton {
                border: 1px solid #cfd6e2;
                border-radius: 8px;
                background: #f5f7fa;
                color: #2b3240;
                padding: 6px 12px;
                font-size: 13px;
            }
            QPushButton:hover { background: #eaeff6; }
            QPushButton:disabled { color: #9aa4b2; background: #f2f4f7; }
            QPushButton#primary {
                background: #3a6ea5; color: white; border: 1px solid #3a6ea5;
            }
            QPushButton#primary:hover { background: #315e8f; }
            QPushButton#added {
                background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7;
            }
            """
        )
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.word_label = QLabel("")
        self.word_label.setObjectName("word")
        self.word_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.word_label)

        self.phonetic_label = QLabel("")
        self.phonetic_label.setObjectName("phonetic")
        self.phonetic_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.phonetic_label)

        self.meaning_label = QLabel("")
        self.meaning_label.setObjectName("meaning")
        self.meaning_label.setFixedWidth(MEANING_WIDTH)
        self.meaning_label.setWordWrap(True)
        self.meaning_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.meaning_label, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.speak_btn = QPushButton("🔊 朗读")
        self.speak_btn.setObjectName("primary")
        self.add_btn = QPushButton("加入错题本")
        self.close_btn = QPushButton("关闭")
        self.speak_btn.clicked.connect(lambda: self.speakRequested.emit(self._word))
        self.add_btn.clicked.connect(self._emit_add)
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.speak_btn)
        btn_row.addWidget(self.add_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    # ---------------- 状态展示 ----------------
    def show_loading(self, word: str) -> None:
        self._word = word
        self.word_label.setText(word)
        self.phonetic_label.setText("")
        self._set_meaning("正在查询释义…", "#8a93a3")
        self.status_label.setText("")
        self.add_btn.setEnabled(False)
        self.add_btn.setText("加入错题本")
        self.add_btn.setObjectName("")
        self._refresh_button_style()
        self._fit_height()

    def show_entry(self, entry: DictEntry, already_in_book: bool = False) -> None:
        self._word = entry.word or self._word
        self.word_label.setText(self._word)
        self.phonetic_label.setText(entry.phonetic or "（无音标）")
        self._set_meaning(entry.meaning or "（无释义）", "#2b3240")
        self.status_label.setText(STATUS_TEXT.get(entry.source, ""))
        if already_in_book:
            self._mark_added()
        else:
            self.add_btn.setEnabled(True)
            self.add_btn.setText("加入错题本")
            self.add_btn.setObjectName("")
            self._refresh_button_style()
        self._fit_height()

    def show_missing(self, word: str, status: str = "none") -> None:
        self._word = word
        self.word_label.setText(word)
        self.phonetic_label.setText("")
        self._set_meaning(STATUS_TEXT.get(status, "未找到释义"), "#b04a3f")
        self.status_label.setText("")
        # 查不到也允许把单词本身加入错题本（音标/释义为空）
        self.add_btn.setEnabled(True)
        self.add_btn.setText("加入错题本")
        self.add_btn.setObjectName("")
        self._refresh_button_style()
        self._fit_height()

    def mark_added(self) -> None:
        self._mark_added()

    def _mark_added(self) -> None:
        self.add_btn.setEnabled(False)
        self.add_btn.setText("✓ 已加入")
        self.add_btn.setObjectName("added")
        self._refresh_button_style()

    def _refresh_button_style(self) -> None:
        # 强制重新应用 objectName 对应的样式
        self.add_btn.style().unpolish(self.add_btn)
        self.add_btn.style().polish(self.add_btn)

    def _emit_add(self) -> None:
        phonetic = self.phonetic_label.text()
        if phonetic == "（无音标）":
            phonetic = ""
        meaning = self.meaning_label.text()
        if meaning.startswith("（") and meaning.endswith("）"):
            meaning = ""
        self.addRequested.emit(self._word, phonetic, meaning)
        self._mark_added()

    def _set_meaning(self, text: str, color: str) -> None:
        self.meaning_label.setText(text)
        self.meaning_label.setStyleSheet(f"color: {color};")
        fm = QFontMetrics(self.meaning_label.font())
        rect = fm.boundingRect(
            QRect(0, 0, MEANING_WIDTH, 0), Qt.TextWordWrap | Qt.AlignTop, text
        )
        self.meaning_label.setMinimumHeight(max(24, rect.height() + 4))
        self.meaning_label.setMaximumHeight(220)

    def _fit_height(self) -> None:
        self.adjustSize()
        self.setFixedHeight(self.layout().sizeHint().height())

    # ---------------- 定位 / 键盘 ----------------
    def position_near(self, anchor: QRect) -> None:
        """放在宠物右侧，空间不足放左侧，纵向对齐并夹取到可用屏幕内。"""
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()

        x = anchor.right() + GAP
        if x + self.width() > avail.right():
            x = anchor.left() - self.width() - GAP
        if x < avail.left() or x + self.width() > avail.right():
            # 仍越界则贴右/左边缘
            x = max(avail.left() + GAP, min(x, avail.right() - self.width() - GAP))

        y = anchor.center().y() - self.height() // 2
        y = max(avail.top() + GAP, min(y, avail.bottom() - self.height() - GAP))
        self.move(QPoint(int(x), int(y)))

    def present(self, word: str, anchor: QRect, in_book: bool = False) -> None:
        """loading 态弹出并定位。"""
        self.show_loading(word)
        if in_book:
            self._mark_added()
        self.position_near(anchor)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
