"""置顶透明无边框桌宠窗口。

要点：
- FramelessWindowHint + WindowStaysOnTopHint + Tool（不在任务栏显示）
- WA_TranslucentBackground 透明背景；绝不使用 WA_TransparentForMouseEvents
- Emoji QLabel 固定像素 96x96 + Segoe UI Emoji；按字形 alpha 生成窗口 mask，
  视觉透明的角落不拦截鼠标
- 左键：按下记录位置，移动超过 5px 判定拖动，释放不触发点击；
  未移动的点击用 QTimer 区分单击/双击，间隔取系统 GetDoubleClickTime()
- 启动校验坐标落在任一屏幕可用区域内，越界回默认位
- closeEvent 仅隐藏（缩托盘）
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QRegion
from PySide6.QtWidgets import QLabel, QWidget

from .logger import get_logger

log = get_logger("pet")

PET_SIZE = 96
DRAG_THRESHOLD = 5  # px
EMOJI = "🐧"
ALPHA_THRESHOLD = 24
HINT_DURATION_MS = 1800


def system_double_click_interval() -> int:
    """读取系统双击间隔（毫秒）；失败回退 Qt 的系统封装值。"""
    try:
        import ctypes

        ms = int(ctypes.windll.user32.GetDoubleClickTime())
        if ms > 0:
            return ms
    except Exception as exc:
        log.warning("GetDoubleClickTime 调用失败：%s", exc)
    from PySide6.QtWidgets import QApplication

    return QApplication.doubleClickInterval() or 500


def position_visible_on_screen(x: int, y: int, size: int = PET_SIZE) -> bool:
    """窗口矩形是否与任一屏幕的可用几何（排除任务栏等）有交集。"""
    rect = QRect(int(x), int(y), size, size)
    for screen in QGuiApplication.screens():
        if screen.availableGeometry().intersects(rect):
            return True
    return False


class PetWindow(QWidget):
    speakRequested = Signal(str)       # 单击：朗读当前词
    definitionRequested = Signal(str)  # 双击：弹出释义

    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        self.current_word: str = ""

        self._drag_offset = QPoint()
        self._press_global = QPoint()
        self._dragging = False
        self._pending_clicks = 0
        self._mask_applied = False

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_click_phase_done)

        self._hint: QWidget | None = None
        self._hint_label: QLabel | None = None
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._hide_hint)

        self._init_ui()
        self._init_position()

    # ---------------- UI ----------------
    def _init_ui(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # Windows 下不在任务栏显示按钮
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(PET_SIZE, PET_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("LinguaPet（F2 取词，单击朗读，双击查词）")

        self.label = QLabel(EMOJI, self)
        self.label.setFixedSize(PET_SIZE, PET_SIZE)
        self.label.setAlignment(Qt.AlignCenter)
        font = QFont("Segoe UI Emoji")
        font.setPixelSize(64)
        self.label.setFont(font)
        # QLabel 默认透明背景，仅渲染彩色 Emoji
        self.label.setStyleSheet("background: transparent;")

    def _init_position(self) -> None:
        x, y = self._config.get("pet_pos", [100, 100])
        if not position_visible_on_screen(int(x), int(y)):
            log.warning("保存的宠物坐标 (%s, %s) 越界，回默认位", x, y)
            x, y = 100, 100
            self._config.update(pet_pos=[x, y])
        self.move(int(x), int(y))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._mask_applied:
            # 等字体渲染后按 alpha 建一次 mask 即可
            QTimer.singleShot(0, self._apply_alpha_mask)

    def _apply_alpha_mask(self) -> None:
        """按 Emoji 实际像素 alpha 裁剪窗口命中区域，透明角不挡鼠标。

        离屏把 Emoji 绘制到透明 QPixmap 提取轮廓；若图形栈不可信（如某些
        远程/沙箱会话 pixmap 无 alpha 通道，整图不透明），回退几何圆角矩形。
        """
        try:
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QPainter, QPixmap

            pixmap = QPixmap(PET_SIZE, PET_SIZE)
            if not pixmap.isNull() and pixmap.hasAlphaChannel():
                pixmap.fill(Qt.transparent)
                painter = QPainter(pixmap)
                painter.setFont(self.label.font())
                painter.drawText(QRectF(0, 0, PET_SIZE, PET_SIZE), Qt.AlignCenter, EMOJI)
                painter.end()
                image = pixmap.toImage()
                region = self._region_from_alpha(image)
                if region is not None:
                    self.setMask(region)
                    self._mask_applied = True
                    log.info("已按 Emoji alpha 生成点击遮罩")
                    return
            log.info("当前图形栈无可用 alpha 通道，使用几何圆角遮罩")
        except Exception as exc:
            log.warning("Emoji alpha 遮罩生成失败：%s", exc)

        self._apply_geometric_mask()

    @staticmethod
    def _region_from_alpha(image) -> QRegion | None:
        """扫描 alpha 构建区域；结果可疑（四角不透明/覆盖近整窗）时返回 None。"""
        region = QRegion()
        visible_count = 0
        for row in range(image.height()):
            seg_start = -1
            for col in range(image.width()):
                visible = QColor(image.pixel(col, row)).alpha() > ALPHA_THRESHOLD
                if visible:
                    visible_count += 1
                    if seg_start < 0:
                        seg_start = col
                elif seg_start >= 0:
                    region += QRect(seg_start, row, col - seg_start, 1)
                    seg_start = -1
            if seg_start >= 0:
                region += QRect(seg_start, row, image.width() - seg_start, 1)

        if region.isEmpty():
            return None
        # 合理性校验：真实 Emoji 四角必然透明，且不可能铺满整个矩形
        corners = [(2, 2), (PET_SIZE - 3, 2), (2, PET_SIZE - 3), (PET_SIZE - 3, PET_SIZE - 3)]
        opaque_corners = sum(
            1 for x, y in corners
            if QColor(image.pixel(x, y)).alpha() > ALPHA_THRESHOLD
        )
        coverage = visible_count / (PET_SIZE * PET_SIZE)
        if opaque_corners >= 3 or coverage > 0.95:
            return None
        return region

    def _apply_geometric_mask(self) -> None:
        """兜底：圆角矩形命中区域（不依赖 alpha 通道）。"""
        try:
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QBitmap, QPainter

            bitmap = QBitmap(PET_SIZE, PET_SIZE)
            bitmap.clear()
            painter = QPainter(bitmap)
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.black)  # QBitmap 黑色=可命中
            painter.drawRoundedRect(QRectF(0, 0, PET_SIZE, PET_SIZE), 28, 28)
            painter.end()
            region = QRegion(bitmap)
            if not region.isEmpty():
                self.setMask(region)
        except Exception as exc:
            log.warning("几何遮罩也失败，退回整窗可点：%s", exc)
        self._mask_applied = True

    # ---------------- 当前单词 ----------------
    def set_current_word(self, word: str) -> None:
        self.current_word = word
        if word:
            self.setToolTip(f"LinguaPet：{word}\n单击朗读，双击查词")
        else:
            self.setToolTip("LinguaPet（F2 取词，单击朗读，双击查词）")

    # ---------------- 取词气泡 ----------------
    def show_word_hint(self, text: str) -> None:
        """在宠物旁短暂显示一个深色圆角气泡。"""
        try:
            self._ensure_hint()
            assert self._hint is not None and self._hint_label is not None
            self._hint_label.setText(text)
            self._hint_label.adjustSize()
            self._hint.resize(self._hint_label.size())
            self._hint_label.move(0, 0)
            self._position_hint()
            self._hint.show()
            self._hint.raise_()
            self._hint_timer.start(HINT_DURATION_MS)
        except Exception as exc:
            log.warning("显示取词气泡失败：%s", exc)

    def _ensure_hint(self) -> None:
        if self._hint is not None:
            return
        hint = QWidget(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint,
        )
        hint.setAttribute(Qt.WA_TranslucentBackground, True)
        label = QLabel(hint)
        label.setStyleSheet(
            "background: rgba(33, 39, 54, 235);"
            "color: white;"
            "border-radius: 10px;"
            "padding: 6px 12px;"
            "font-size: 13px;"
        )
        label.setMaximumWidth(320)
        label.setWordWrap(False)
        self._hint = hint
        self._hint_label = label

    def _position_hint(self) -> None:
        """优先放宠物正上方，上方空间不足（含边界）放下方，并做横向夹取。"""
        assert self._hint is not None
        hint = self._hint
        pet_geo = self.frameGeometry()
        screen = QGuiApplication.screenAt(pet_geo.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()

        x = pet_geo.center().x() - hint.width() // 2
        x = max(avail.left() + 4, min(x, avail.right() - hint.width() - 4))

        gap = 8
        y_above = pet_geo.top() - hint.height() - gap
        if y_above >= avail.top():
            y = y_above
        else:
            y = pet_geo.bottom() + gap
            if y + hint.height() > avail.bottom():
                y = avail.bottom() - hint.height() - 4
        hint.move(x, y)

    def _hide_hint(self) -> None:
        if self._hint is not None:
            self._hint.hide()

    # ---------------- 鼠标交互 ----------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if not self._dragging and (
                abs(delta.x()) > DRAG_THRESHOLD or abs(delta.y()) > DRAG_THRESHOLD
            ):
                self._dragging = True
                # 进入拖动即取消尚未触发的点击流程
                self._pending_clicks = 0
                self._click_timer.stop()
            if self._dragging:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._dragging:
            self._dragging = False
            self._persist_position()
            event.accept()
            return
        # 未发生拖动：计入一次点击，按系统双击间隔判定
        self._pending_clicks += 1
        if self._pending_clicks == 1:
            self._click_timer.start(system_double_click_interval())
        event.accept()

    def _on_click_phase_done(self) -> None:
        clicks = self._pending_clicks
        self._pending_clicks = 0
        if clicks >= 2:
            log.info("双击：请求释义（当前词=%r）", self.current_word)
            self.definitionRequested.emit(self.current_word)
        else:
            log.info("单击：请求朗读（当前词=%r）", self.current_word)
            self.speakRequested.emit(self.current_word)

    def _persist_position(self) -> None:
        pos = self.frameGeometry().topLeft()
        self._config.update(pet_pos=[pos.x(), pos.y()])
        if not self._config.save():
            log.warning("宠物位置保存失败")

    # ---------------- 关闭=隐藏到托盘 ----------------
    def closeEvent(self, event) -> None:
        event.ignore()
        self._hide_hint()
        self.hide()
        log.info("宠物窗口已隐藏到托盘")

    def bring_to_front(self) -> None:
        """被单实例/托盘唤出时：显示、置顶并抢焦点。"""
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
