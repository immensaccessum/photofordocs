"""Мелкие виджеты стойки: стиль флажков, холсты и дерево файлов."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QProxyStyle,
    QSlider,
    QStyle,
    QStyleFactory,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from photofordocs.canvas import READY_MIME, ViewCanvas

PREVIEW_SIDE = 1400
EXPORT_DPI = 300
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
THUMB_SIDE = 22
THUMB_LIMIT = 80


class IndicatorStyle(QProxyStyle):
    """Рамка флажка и точка переключателя остаются видны и в выбранном состоянии.

    Общий фон окна заставляет системный стиль Windows прятать рамку отмеченного
    флажка и сам кружок выбранного переключателя.
    """

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            self._draw_box(option, painter, round_box=True)
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            self._draw_box(option, painter, round_box=False)
            return
        super().drawPrimitive(element, option, painter, widget)

    def _draw_box(self, option, painter: QPainter, round_box: bool) -> None:
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        rect = option.rect.adjusted(1, 1, -2, -2)
        palette = option.palette
        group = QPalette.ColorGroup.Disabled if not enabled else QPalette.ColorGroup.Active
        border = palette.color(group, QPalette.ColorRole.Mid)
        fill = palette.color(group, QPalette.ColorRole.Button)
        mark = palette.color(group, QPalette.ColorRole.ButtonText)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        if round_box:
            painter.drawRoundedRect(rect, 2, 2)
            if checked:
                pen = QPen(mark, 1.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                width = rect.width()
                height = rect.height()
                start = rect.topLeft() + QPoint(int(width * 0.22), int(height * 0.52))
                middle = rect.topLeft() + QPoint(int(width * 0.42), int(height * 0.74))
                end = rect.topLeft() + QPoint(int(width * 0.78), int(height * 0.30))
                painter.drawPolyline([start, middle, end])
        else:
            painter.drawEllipse(rect)
            if checked:
                inset = max(3, rect.width() // 4)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(mark)
                painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))
        painter.restore()


class UnitSlider(QSlider):
    """Один щелчок колеса сдвигает ползунок на 1, без тройного шага Windows."""

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        notches = int(delta / 120)
        if notches == 0:
            notches = 1 if delta > 0 else -1
        self.setValue(self.value() + notches)
        event.accept()


def install_indicator_style(app) -> None:
    base = QStyleFactory.create("Fusion") or QStyleFactory.create("windowsvista") or QStyleFactory.create("Windows")
    app.setStyle(IndicatorStyle(base))


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class FinishedView(ViewCanvas, QWidget):
    """Закладка «Готовая фотография»: уже обрезанный кадр, как его увидит оператор."""

    resized = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._init_view()
        self._pix = QPixmap()
        self._smooth = True
        self.setMouseTracking(True)

    def _content_size(self) -> tuple[int, int] | None:
        if self._pix.isNull():
            return None
        return self._pix.width(), self._pix.height()

    def set_photo(self, pixmap: QPixmap, smooth: bool = True) -> None:
        old = 0 if self._pix.isNull() else self._pix.width()
        self._pix = pixmap
        self._smooth = smooth
        self.view.note_content_resize(old, 0 if pixmap.isNull() else pixmap.width())
        self.update()

    def reveal_smooth(self) -> None:
        """После остановки ползунка один раз сгладить увеличение, если кадр мельче окна."""
        if self._smooth or self._pix.isNull():
            return
        self._smooth = True
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    def wheelEvent(self, event) -> None:
        self.view_wheel(event)

    def mousePressEvent(self, event) -> None:
        self.view_mouse_press(event)

    def mouseMoveEvent(self, event) -> None:
        self.view_mouse_move(event)

    def mouseReleaseEvent(self, event) -> None:
        self.view_mouse_release(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window))
        if self._pix.isNull():
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Откройте снимок")
            painter.end()
            return
        scale, ox, oy = self._view_transform()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._smooth and self._view_drag is None)
        painter.drawPixmap(QRectF(ox, oy, self._pix.width() * scale, self._pix.height() * scale), self._pix, QRectF(self._pix.rect()))
        painter.end()


class FileTree(QTreeWidget):
    """Дерево файлов. Готовую ветку можно перетащить на лист или в таблицу."""

    delete_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selectedItems():
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def startDrag(self, _supported) -> None:
        item = self.currentItem()
        if item is None or item.data(0, Qt.ItemDataRole.UserRole + 1) != "ready":
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        format_id = str(item.data(0, Qt.ItemDataRole.UserRole + 2) or "")
        mime = QMimeData()
        mime.setData(READY_MIME, f"{path}\n{format_id}".encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


def reveal_opened_branch(tree: QTreeWidget, item: QTreeWidgetItem) -> None:
    """Прокрутить список так, чтобы нижняя строка только что открытой ветки была видна."""
    if item.treeWidget() is not tree or not item.isExpanded():
        return
    last = item
    while last.isExpanded() and last.childCount():
        last = last.child(last.childCount() - 1)
    tree.scrollToItem(last, QAbstractItemView.ScrollHint.EnsureVisible)


class ReadyDropTable(QTableWidget):
    ready_dropped = Signal(str, str)

    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(READY_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(READY_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        raw = bytes(event.mimeData().data(READY_MIME)).decode("utf-8")
        path, format_id = raw.split("\n", 1)
        self.ready_dropped.emit(path, format_id)
        event.acceptProposedAction()
