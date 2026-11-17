"""Холст исходника и холст печатного листа."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from photofordocs.imageops import guide_ratios
from photofordocs.session import SheetDoc, SheetItem, overflows, paper_mm, sheet_edges

READY_MIME = "application/x-photofordocs-ready"


class PrintCanvas(QWidget):
    """Лист, на котором кадры выбирают и двигают."""

    changed = Signal()
    status = Signal(str)
    ready_dropped = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.doc = SheetDoc()
        self.pixmaps: dict[str, QPixmap] = {}
        self.footer = ""
        self.selected: set[str] = set()
        self.zoom = 1.0
        self.pan = QPoint(24, 24)
        self._custom_view = False
        self._drag: QPoint | None = None
        self._drag_ids: set[str] = set()
        self._band: QRectF | None = None
        self._band_origin: QPoint | None = None
        self.keep_inside = True
        self.block_overlap = True
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(480, 420)

    def set_document(self, doc: SheetDoc, pixmaps: dict[str, QPixmap], footer: str) -> None:
        self.doc = doc
        self.pixmaps = pixmaps
        self.footer = footer
        self.selected = {item_id for item_id in self.selected if any(item.id == item_id for item in doc.items)}
        self.update()

    def fit(self) -> None:
        width, height = paper_mm(self.doc)
        if width <= 0 or height <= 0 or self.width() < 80 or self.height() < 80:
            return
        margin = 36
        room_w = max(1, self.width() - margin * 2)
        room_h = max(1, self.height() - margin * 2)
        self.zoom = min(room_w / width, room_h / height)
        self.pan = QPoint(int((self.width() - width * self.zoom) / 2), int((self.height() - height * self.zoom) / 2))
        self._custom_view = False
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._custom_view:
            self.fit()

    def _rect(self, item: SheetItem) -> QRectF:
        return QRectF(self.pan.x() + item.x_mm * self.zoom, self.pan.y() + item.y_mm * self.zoom, item.w_mm * self.zoom, item.h_mm * self.zoom)

    def _hit(self, point: QPoint) -> SheetItem | None:
        for item in reversed(self.doc.items):
            if self._rect(item).contains(point.x(), point.y()):
                return item
        return None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#c8c8c8"))
        width, height = paper_mm(self.doc)
        paper = QRectF(self.pan.x(), self.pan.y(), width * self.zoom, height * self.zoom)
        top, right, bottom, left = sheet_edges(self.doc)
        inner = QRectF(
            paper.left() + left * self.zoom,
            paper.top() + top * self.zoom,
            max(0.0, paper.width() - (left + right) * self.zoom),
            max(0.0, paper.height() - (top + bottom) * self.zoom),
        )
        painter.fillRect(paper, QColor("#c4c4c4"))
        painter.fillRect(inner, QColor("white"))
        painter.setPen(QPen(QColor("#888"), 1))
        painter.drawRect(paper)
        painter.setPen(QPen(QColor("#9a9a9a"), 1))
        painter.drawRect(inner)
        if self.doc.grid and self.doc.grid_mm > 0:
            painter.setPen(QPen(QColor("#e4e4e4"), 1))
            step = self.doc.grid_mm * self.zoom
            x = paper.left()
            while x <= paper.right():
                painter.drawLine(int(x), int(paper.top()), int(x), int(paper.bottom()))
                x += step
            y = paper.top()
            while y <= paper.bottom():
                painter.drawLine(int(paper.left()), int(y), int(paper.right()), int(y))
                y += step
        for item in self.doc.items:
            rect = self._rect(item)
            if item.kind == "photo" and item.ready_id in self.pixmaps:
                pixmap = self.pixmaps[item.ready_id]
                painter.save()
                target = rect
                turn = item.rotation % 360
                if turn:
                    # Ячейка уже лежит на боку. Снимок поворачиваем в его прежних пропорциях.
                    painter.translate(rect.center())
                    painter.rotate(turn)
                    painter.translate(-rect.center())
                    if turn % 180 == 90:
                        target = QRectF(
                            rect.center().x() - rect.height() / 2,
                            rect.center().y() - rect.width() / 2,
                            rect.height(),
                            rect.width(),
                        )
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
                painter.restore()
            elif item.kind == "footer" and self.footer.strip():
                painter.setPen(QColor("#222"))
                painter.drawText(rect.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.footer)
            elif item.kind == "logo":
                painter.fillRect(rect, QColor("#dde4ee"))
                painter.setPen(QColor("#334"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "логотип")
            if item.id in self.selected:
                painter.setPen(QPen(QColor("#1d4e89"), 2))
                painter.drawRect(rect)
            if self.doc.cut_marks and item.kind == "photo":
                painter.setPen(QPen(QColor("#111"), 1))
                self._marks(painter, rect)
        if self._band is not None:
            painter.setPen(QPen(QColor("#1d4e89"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._band)
        self._rulers(painter, paper)
        painter.end()

    def _marks(self, painter: QPainter, rect: QRectF) -> None:
        length = 8
        for x, y, sx, sy in (
            (rect.left(), rect.top(), -1, -1),
            (rect.right(), rect.top(), 1, -1),
            (rect.left(), rect.bottom(), -1, 1),
            (rect.right(), rect.bottom(), 1, 1),
        ):
            painter.drawLine(int(x + sx * 2), int(y), int(x + sx * (2 + length)), int(y))
            painter.drawLine(int(x), int(y + sy * 2), int(x), int(y + sy * (2 + length)))

    def _rulers(self, painter: QPainter, paper: QRectF) -> None:
        """Линейка в миллиметрах: короткое деление 5 мм, число каждые 10 мм."""
        painter.fillRect(0, 0, self.width(), 22, QColor("#eee"))
        painter.fillRect(0, 0, 22, self.height(), QColor("#eee"))
        painter.setPen(QColor("#333"))
        painter.drawText(2, 14, "мм")
        step_mm = 5
        minor = step_mm * self.zoom
        number = 10
        if minor < 4:
            step_mm = 10
            minor = step_mm * self.zoom
            number = 50
        if minor < 3:
            return
        x = paper.left()
        mm = 0
        while x <= paper.right() + 0.5 and mm < 2000:
            tall = mm % number == 0
            painter.drawLine(int(x), 22 - (12 if tall else 6), int(x), 22)
            if tall:
                painter.drawText(int(x) + 2, 12, str(mm))
            x += minor
            mm += step_mm
        y = paper.top()
        mm = 0
        while y <= paper.bottom() + 0.5 and mm < 2000:
            tall = mm % number == 0
            painter.drawLine(22 - (12 if tall else 6), int(y), 22, int(y))
            if tall and mm:
                painter.drawText(1, int(y) - 1, str(mm))
            y += minor
            mm += step_mm

    def wheelEvent(self, event) -> None:
        self._custom_view = True
        self.zoom = max(0.4, min(8.0, self.zoom * (1.1 if event.angleDelta().y() > 0 else 1 / 1.1)))
        self.update()

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

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        point = event.position().toPoint()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag = point
            self._drag_ids = set()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(point)
        if hit is None:
            self.selected = set()
            self._band_origin = point
            self._band = QRectF(point.x(), point.y(), 1, 1)
        else:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.selected.symmetric_difference_update({hit.id})
            elif hit.id not in self.selected:
                self.selected = {hit.id}
            self._drag = point
            self._drag_ids = set(self.selected)
        self._announce()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self._band_origin is not None:
            self._band = QRectF(self._band_origin.x(), self._band_origin.y(), point.x() - self._band_origin.x(), point.y() - self._band_origin.y()).normalized()
            self.update()
            return
        if self._drag is None:
            return
        delta = point - self._drag
        self._drag = point
        if not self._drag_ids:
            self._custom_view = True
            self.pan += delta
        else:
            dx = delta.x() / self.zoom
            dy = delta.y() / self.zoom
            from photofordocs.session import move_selected

            new_doc = move_selected(self.doc, self._drag_ids, dx, dy)
            if not self._move_allowed(new_doc, self._drag_ids):
                return
            self.doc = new_doc
            self.changed.emit()
        self._announce()
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._band is not None:
            self.selected = {item.id for item in self.doc.items if self._band.intersects(self._rect(item))}
            self._announce()
        self._drag = None
        self._drag_ids = set()
        self._band = None
        self._band_origin = None
        self.update()

    def keyPressEvent(self, event) -> None:
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.5
        moves = {
            Qt.Key.Key_Left: (-step, 0),
            Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, -step),
            Qt.Key.Key_Down: (0, step),
        }
        if event.key() in moves and self.selected:
            from photofordocs.session import move_selected

            dx, dy = moves[event.key()]
            new_doc = move_selected(self.doc, self.selected, dx, dy)
            if not self._move_allowed(new_doc, self.selected):
                return
            self.doc = new_doc
            self.changed.emit()
            self._announce()
            self.update()
            return
        super().keyPressEvent(event)

    def _move_allowed(self, doc: SheetDoc, ids: set[str]) -> bool:
        photos = [item for item in doc.items if item.kind == "photo"]
        width, height = paper_mm(doc)
        for item in photos:
            if item.id not in ids:
                continue
            if self.keep_inside:
                top, right, bottom, left = sheet_edges(doc)
                if item.x_mm < left - 0.05 or item.y_mm < top - 0.05:
                    return False
                if item.x_mm + item.w_mm > width - right + 0.05 or item.y_mm + item.h_mm > height - bottom + 0.05:
                    return False
            if self.block_overlap:
                for other in photos:
                    if other.id == item.id:
                        continue
                    apart = (
                        item.x_mm + item.w_mm <= other.x_mm
                        or other.x_mm + other.w_mm <= item.x_mm
                        or item.y_mm + item.h_mm <= other.y_mm
                        or other.y_mm + other.h_mm <= item.y_mm
                    )
                    if not apart:
                        return False
        return True

    def _announce(self) -> None:
        chosen = [item for item in self.doc.items if item.id in self.selected]
        if not chosen:
            self.status.emit(f"На листе {sum(1 for item in self.doc.items if item.kind == 'photo')} кадров")
            return
        item = chosen[-1]
        note = "не влезает" if overflows(item, self.doc) else "на листе"
        self.status.emit(f"{item.kind}  x {item.x_mm:.1f}  y {item.y_mm:.1f}  {item.w_mm:.0f}×{item.h_mm:.0f} мм  {note}")


class SourceCanvas(QWidget):
    """Исходник с рамкой. Макушка и подбородок задают голову, линия глаз — лицевую часть."""

    pan = Signal(float, float)
    zoom = Signal(float)
    ruler = Signal(str)
    guide = Signal(str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self.pixmap: QPixmap | None = None
        self.crop = None
        self.fmt = None
        self.show_frame = True
        self.angle = 0.0
        self.eye_t = 0.55
        self._origin: QPoint | None = None
        self._handle: str | None = None
        self.ruler_points: list[tuple[float, float]] = []
        self.setMinimumSize(480, 420)
        self.setMouseTracking(True)

    def set_frame(self, pixmap: QPixmap, crop, fmt, show_frame: bool, angle: float = 0.0, eye_t: float = 0.55) -> None:
        self.pixmap = pixmap
        self.crop = crop
        self.fmt = fmt
        self.show_frame = show_frame
        self.angle = angle
        self.eye_t = eye_t
        self.update()

    def _fit(self) -> tuple[float, float, float]:
        assert self.pixmap is not None
        scale = min((self.width() - 24) / self.pixmap.width(), (self.height() - 24) / self.pixmap.height())
        scale = max(scale, 0.01)
        drawn_w = self.pixmap.width() * scale
        drawn_h = self.pixmap.height() * scale
        return scale, (self.width() - drawn_w) / 2, (self.height() - drawn_h) / 2

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#d7d7d7"))
        if self.pixmap is None or self.crop is None or self.fmt is None:
            painter.setPen(QColor("#333"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Откройте снимок или папку")
            painter.end()
            return
        scale, ox, oy = self._fit()
        painter.drawPixmap(QRectF(ox, oy, self.pixmap.width() * scale, self.pixmap.height() * scale), self.pixmap, QRectF(self.pixmap.rect()))
        if self.show_frame:
            rect = self._frame_rect()
            painter.save()
            painter.translate(rect.center())
            painter.rotate(self.angle)
            painter.translate(-rect.center())
            painter.setPen(QPen(QColor("white"), 1))
            painter.drawRect(rect.adjusted(-1, -1, -1, -1))
            painter.setPen(QPen(QColor("#111"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(rect)
            painter.setPen(QPen(QColor("#222"), 1))
            crown_r, eye_r, chin_r = guide_ratios(self.fmt, self.eye_t)
            for ratio in (crown_r, eye_r, chin_r):
                y = rect.top() + ratio * rect.height()
                painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            painter.drawLine(int(rect.center().x()), int(rect.top()), int(rect.center().x()), int(rect.bottom()))
            crown_y = rect.top() + crown_r * rect.height()
            chin_y = rect.top() + chin_r * rect.height()
            head_h = chin_y - crown_y
            oval_w = head_h * 0.75
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("white"), 3))
            painter.drawEllipse(QRectF(rect.center().x() - oval_w / 2, crown_y, oval_w, head_h))
            painter.setPen(QPen(QColor("#222"), 1))
            painter.drawEllipse(QRectF(rect.center().x() - oval_w / 2, crown_y, oval_w, head_h))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(QColor("#111"), 1))
            painter.setBrush(QColor("white"))
            for handle in self._handles().values():
                painter.drawRect(QRectF(handle.x() - 4, handle.y() - 4, 8, 8))
            painter.restore()
        if len(self.ruler_points) == 2 and self.crop is not None:
            painter.setPen(QPen(QColor("#9a3412"), 2))
            a, b = self.ruler_points
            painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        painter.end()

    def wheelEvent(self, event) -> None:
        if self.pixmap is None:
            return
        self.zoom.emit(1.08 if event.angleDelta().y() > 0 else 1 / 1.08)

    def mousePressEvent(self, event) -> None:
        if self.pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.ruler_points.append((point.x(), point.y()))
            self.ruler_points = self.ruler_points[-2:]
            self._measure()
            self.update()
            return
        hit = self._hit(point)
        self._origin = point
        self._handle = hit
        self.setCursor(Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self.pixmap is None or self.crop is None:
            return
        point = event.position().toPoint()
        if self._origin is None:
            self.setCursor(Qt.CursorShape.SizeAllCursor if self._hit(point) else Qt.CursorShape.ArrowCursor)
            return
        delta = point - self._origin
        self._origin = point
        scale, _ox, _oy = self._fit()
        if scale <= 0 or self.crop.width < 1:
            return
        if self._handle:
            self.guide.emit(self._handle, delta.x() / scale, delta.y() / scale)
            return
        self.pan.emit(delta.x() / scale / self.crop.width, delta.y() / scale / self.crop.height)

    def mouseReleaseEvent(self, _event) -> None:
        self._origin = None
        self._handle = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _frame_rect(self) -> QRectF:
        scale, ox, oy = self._fit()
        return QRectF(ox + self.crop.left * scale, oy + self.crop.top * scale, self.crop.width * scale, self.crop.height * scale)

    def _handles(self) -> dict[str, QPointF]:
        rect = self._frame_rect()
        crown_r, eye_r, chin_r = guide_ratios(self.fmt, self.eye_t)
        ratios = {"crown": crown_r, "eyes": eye_r, "chin": chin_r}
        return {name: QPointF(rect.center().x(), rect.top() + ratio * rect.height()) for name, ratio in ratios.items()}

    def _unrotate(self, point: QPoint) -> QPointF:
        center = self._frame_rect().center()
        rad = math.radians(-self.angle)
        c, s = math.cos(rad), math.sin(rad)
        dx, dy = point.x() - center.x(), point.y() - center.y()
        return QPointF(center.x() + dx * c - dy * s, center.y() + dx * s + dy * c)

    def _hit(self, point: QPoint) -> str | None:
        if not self.show_frame or self.crop is None or self.fmt is None or self.pixmap is None:
            return None
        local = self._unrotate(point)
        found = None
        best = 10.0
        for name, handle in self._handles().items():
            distance = math.hypot(local.x() - handle.x(), local.y() - handle.y())
            if distance < best:
                found, best = name, distance
        return found

    def _measure(self) -> None:
        if len(self.ruler_points) < 2 or self.crop is None or self.fmt is None or self.pixmap is None:
            return
        scale, _ox, _oy = self._fit()
        ax, ay = self.ruler_points[0]
        bx, by = self.ruler_points[1]
        pixels = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / scale
        mm = pixels / self.crop.width * self.fmt.width_mm
        self.ruler.emit(f"Линейка {mm:.1f} мм")
