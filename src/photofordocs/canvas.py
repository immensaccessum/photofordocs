"""Холст исходника и холст печатного листа."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPalette, QTransform
from PySide6.QtWidgets import QWidget

from photofordocs.imageops import guide_ratios
from photofordocs.session import SheetDoc, SheetItem, overflows, paper_mm, sheet_edges

READY_MIME = "application/x-photofordocs-ready"
RULER_PX = 20


class PrintCanvas(QWidget):
    """Лист, на котором кадры выбирают и двигают."""

    changed = Signal()
    status = Signal(str)
    ready_dropped = Signal(str, str)
    delete_selected = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.doc = SheetDoc()
        self.pixmaps: dict[str, QPixmap] = {}
        self.footer = ""
        self.selected: set[str] = set()
        self.zoom = 1.0
        self.pan = QPoint(24, 24)
        self._custom_view = False
        self.space_held = False
        self._view_drag: QPoint | None = None
        self._drag: QPoint | None = None
        self._press: QPoint | None = None
        self._press_items: dict[str, SheetItem] = {}
        self._drag_ids: set[str] = set()
        self._band: QRectF | None = None
        self._band_origin: QPoint | None = None
        self._band_add = False
        self._anchor_id = ""
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
        margin = 40
        room_w = max(1, self.width() - margin * 2)
        room_h = max(1, self.height() - margin * 2)
        self.zoom = min(room_w / width, room_h / height)
        self.pan = QPoint(int((self.width() - width * self.zoom) / 2), int((self.height() - height * self.zoom) / 2))
        self._custom_view = False
        self.update()

    def fit_view(self) -> None:
        self.fit()

    def zoom_view(self, factor: float, anchor: QPointF | None = None) -> None:
        """Меняет масштаб к точке. Пока лист не трогали, он вписан в окно."""
        old = self.zoom
        if old <= 0:
            return
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        new = max(0.2, min(80.0, old * factor))
        applied = new / old
        self.zoom = new
        self.pan = QPoint(
            int(round(anchor.x() - (anchor.x() - self.pan.x()) * applied)),
            int(round(anchor.y() - (anchor.y() - self.pan.y()) * applied)),
        )
        self._custom_view = True
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
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window))
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
            if item.kind == "photo" and item.ready_id not in self.pixmaps:
                painter.fillRect(rect, QColor("#f3f3f3"))
                painter.setPen(QPen(QColor("#888"), 1))
                painter.drawRect(rect)
            elif item.kind == "photo" and item.ready_id in self.pixmaps:
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
                painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform,
                    max(target.width(), target.height()) > min(pixmap.width(), pixmap.height()) * 0.85,
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
            self._cut(painter, rect, item)
        if self._band is not None:
            painter.setPen(QPen(QColor("#1d4e89"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._band)
        self._rulers(painter, paper)
        painter.end()

    def _cut(self, painter: QPainter, rect: QRectF, item: SheetItem) -> None:
        style = self.doc.cut_style if item.kind == "photo" and self.doc.cut_marks else "none"
        if style == "none":
            return
        if style == "dash":
            painter.setPen(QPen(QColor("#111"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(rect.adjusted(-3, -3, 3, 3))
            return
        if style == "box":
            painter.setPen(QPen(QColor("#111"), 1))
            painter.drawRect(rect.adjusted(-2, -2, 2, 2))
            return
        painter.setPen(QPen(QColor("#111"), 1))
        self._marks(painter, rect)

    def _marks(self, painter: QPainter, rect: QRectF) -> None:
        length = max(6.0, self.doc.cut_length_mm * self.zoom)
        for x, y, sx, sy in (
            (rect.left(), rect.top(), -1, -1),
            (rect.right(), rect.top(), 1, -1),
            (rect.left(), rect.bottom(), -1, 1),
            (rect.right(), rect.bottom(), 1, 1),
        ):
            painter.drawLine(int(x + sx * 2), int(y), int(x + sx * (2 + length)), int(y))
            painter.drawLine(int(x), int(y + sy * 2), int(x), int(y + sy * (2 + length)))

    def _rulers(self, painter: QPainter, paper: QRectF) -> None:
        """Линейка в сантиметрах, приклеена к верхнему и левому краю листа."""
        thickness = float(RULER_PX)
        if paper.width() < 8 or paper.height() < 8 or self.zoom <= 0:
            return
        horizontal = QRectF(paper.left(), paper.top() - thickness, paper.width(), thickness)
        vertical = QRectF(paper.left() - thickness, paper.top(), thickness, paper.height())
        corner = QRectF(paper.left() - thickness, paper.top() - thickness, thickness, thickness)
        ruler = self.palette().color(QPalette.ColorRole.Base)
        painter.fillRect(horizontal, ruler)
        painter.fillRect(vertical, ruler)
        painter.fillRect(corner, self.palette().color(QPalette.ColorRole.Window))
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
        painter.drawRect(horizontal)
        painter.drawRect(vertical)
        font = QFont(painter.font())
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        minor_mm = 50
        for candidate in (1, 2, 5, 10, 20, 50):
            if candidate * self.zoom >= 4:
                minor_mm = candidate
                break
        label_mm = 100
        for candidate in (10, 20, 50, 100):
            if candidate * self.zoom >= 28:
                label_mm = candidate
                break
        x = paper.left()
        mm = 0
        while x <= paper.right() + 0.5 and mm <= 2000:
            tall = mm % label_mm == 0
            mid = mm % 10 == 0
            length = 12 if tall else (8 if mid else 4)
            painter.drawLine(int(x), int(paper.top() - 1), int(x), int(paper.top() - length))
            if tall:
                painter.drawText(int(x) + 2, int(paper.top() - 8), str(mm // 10))
            x += minor_mm * self.zoom
            mm += minor_mm
        y = paper.top()
        mm = 0
        while y <= paper.bottom() + 0.5 and mm <= 2000:
            tall = mm % label_mm == 0
            mid = mm % 10 == 0
            length = 12 if tall else (8 if mid else 4)
            painter.drawLine(int(paper.left() - 1), int(y), int(paper.left() - length), int(y))
            if tall and mm:
                painter.drawText(int(paper.left() - thickness + 1), int(y) - 2, str(mm // 10))
            y += minor_mm * self.zoom
            mm += minor_mm

    def scroll_metrics(self) -> tuple[int, int, int, int] | None:
        width, height = paper_mm(self.doc)
        if width <= 0 or height <= 0 or self.zoom <= 0:
            return None
        return (
            int(round(width * self.zoom)) + RULER_PX,
            int(round(height * self.zoom)) + RULER_PX,
            self.pan.x() - RULER_PX,
            self.pan.y() - RULER_PX,
        )

    def scroll_to(self, sx: int | None = None, sy: int | None = None) -> None:
        """Сдвиг листа. None оставляет эту ось как есть, чтобы колесо не сбивало центр."""
        pan_x = self.pan.x() if sx is None else RULER_PX - int(sx)
        pan_y = self.pan.y() if sy is None else RULER_PX - int(sy)
        self.pan = QPoint(pan_x, pan_y)
        self._custom_view = True
        self.update()

    def wheelEvent(self, event) -> None:
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            wheel_scroll(self, event)
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            return
        notches = delta / 120
        if notches == 0:
            notches = 1 if delta > 0 else -1
        self.zoom_view(1.1 ** notches, event.position())
        event.accept()

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
        if self.space_held and event.button() == Qt.MouseButton.LeftButton:
            self._view_drag = point
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag = point
            self._drag_ids = set()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(point)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if hit is None:
            self._band_add = shift
            if not shift:
                self.selected = set()
            self._band_origin = point
            self._band = QRectF(point.x(), point.y(), 1, 1)
        else:
            removing = shift and hit.id in self.selected
            if shift:
                if removing:
                    self.selected.discard(hit.id)
                else:
                    self.selected.add(hit.id)
            elif hit.id not in self.selected:
                self.selected = {hit.id}
            if removing:
                self._drag = None
                self._drag_ids = set()
            else:
                self._drag = point
                self._press = point
                self._anchor_id = hit.id
                self._press_items = {item.id: item for item in self.doc.items}
                self._drag_ids = set(self.selected)
        self._announce()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self._view_drag is not None:
            delta = point - self._view_drag
            self._view_drag = point
            self.pan += delta
            self._custom_view = True
            self.update()
            return
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
        elif self._press is not None:
            # Сдвиг от точки нажатия держит захват. У поля ось зажимается,
            # а вдоль края кадр продолжает ехать за курсором.
            dx = (point.x() - self._press.x()) / self.zoom
            dy = (point.y() - self._press.y()) / self.zoom
            from photofordocs.session import snap_item

            anchor_id = self._anchor_id if self._anchor_id in self._drag_ids else next(iter(self._drag_ids))
            anchor = self._press_items[anchor_id]
            still = [item for item in self._press_items.values() if item.id not in self._drag_ids]
            adx, ady = self._slide(dx, dy)
            if self.doc.snap:
                snapped = snap_item(anchor.moved(adx, ady), self.doc, still)
                trial_x = snapped.x_mm - anchor.x_mm
                trial_y = snapped.y_mm - anchor.y_mm
                if not self._hits(trial_x, trial_y):
                    adx, ady = trial_x, trial_y
            moved = []
            for item in self._press_items.values():
                moved.append(item.moved(adx, ady) if item.id in self._drag_ids else item)
            self.doc = SheetDoc(
                self.doc.paper_id,
                self.doc.custom_w,
                self.doc.custom_h,
                self.doc.landscape,
                self.doc.margin_mm,
                self.doc.gap_mm,
                self.doc.snap,
                self.doc.grid,
                self.doc.grid_mm,
                self.doc.cut_marks,
                moved,
                self.doc.margin_top_mm,
                self.doc.margin_right_mm,
                self.doc.margin_bottom_mm,
                self.doc.margin_left_mm,
                self.doc.cut_style,
                self.doc.cut_length_mm,
            )
            self.changed.emit()
        self._announce()
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._view_drag is not None:
            self._view_drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.space_held else Qt.CursorShape.ArrowCursor)
            return
        if self._band is not None:
            picked = {item.id for item in self.doc.items if item.kind == "photo" and self._band.intersects(self._rect(item))}
            self.selected = self.selected | picked if self._band_add else picked
            self._announce()
        self._drag = None
        self._press = None
        self._press_items = {}
        self._drag_ids = set()
        self._anchor_id = ""
        self._band = None
        self._band_origin = None
        self._band_add = False
        self.update()

    def keyPressEvent(self, event) -> None:
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.5
        moves = {
            Qt.Key.Key_Left: (-step, 0),
            Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, -step),
            Qt.Key.Key_Down: (0, step),
        }
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected:
            self.delete_selected.emit()
            return
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

    def _dragged_photos(self) -> list[SheetItem]:
        return [item for item in self._press_items.values() if item.id in self._drag_ids and item.kind == "photo"]

    def _slide(self, dx: float, dy: float) -> tuple[float, float]:
        """Упираясь в соседний кадр, едет вдоль него к курсору, а не назад к началу."""
        dx, dy = self._clamp_sheet(dx, dy)
        if not self._hits(dx, dy):
            return dx, dy
        hx = self._approach(dx, 0.0, "x")
        hy = self._approach(hx, dy, "y")
        vy = self._approach(0.0, dy, "y")
        vx = self._approach(dx, vy, "x")
        ox = self._approach(dx, dy, "x")
        oy = self._approach(ox, dy, "y")
        oy_first = self._approach(dx, dy, "y")
        ox_second = self._approach(dx, oy_first, "x")
        best = (0.0, 0.0)
        best_dist = dx * dx + dy * dy
        for cx, cy in ((hx, hy), (vx, vy), (ox, oy), (ox_second, oy_first)):
            if self._hits(cx, cy):
                continue
            dist = (dx - cx) ** 2 + (dy - cy) ** 2
            if dist < best_dist:
                best = (cx, cy)
                best_dist = dist
        return best

    def _clamp_sheet(self, dx: float, dy: float) -> tuple[float, float]:
        if not self.keep_inside:
            return dx, dy
        top, right, bottom, left = sheet_edges(self.doc)
        width, height = paper_mm(self.doc)
        lo_x, hi_x = -1e9, 1e9
        lo_y, hi_y = -1e9, 1e9
        for item in self._dragged_photos():
            lo_x = max(lo_x, left - item.x_mm)
            hi_x = min(hi_x, width - right - item.w_mm - item.x_mm)
            lo_y = max(lo_y, top - item.y_mm)
            hi_y = min(hi_y, height - bottom - item.h_mm - item.y_mm)
        if lo_x > hi_x:
            lo_x = hi_x = 0.0
        if lo_y > hi_y:
            lo_y = hi_y = 0.0
        return min(max(dx, lo_x), hi_x), min(max(dy, lo_y), hi_y)

    def _hits(self, dx: float, dy: float) -> bool:
        if not self.block_overlap:
            return False
        moved = [(item.x_mm + dx, item.y_mm + dy, item.w_mm, item.h_mm) for item in self._dragged_photos()]
        others = [
            (item.x_mm, item.y_mm, item.w_mm, item.h_mm)
            for item in self._press_items.values()
            if item.id not in self._drag_ids and item.kind == "photo"
        ]

        def apart(a, b) -> bool:
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            return ax + aw <= bx + 1e-6 or bx + bw <= ax + 1e-6 or ay + ah <= by + 1e-6 or by + bh <= ay + 1e-6

        for index, rect in enumerate(moved):
            if any(not apart(rect, other) for other in moved[index + 1 :]):
                return True
            if any(not apart(rect, other) for other in others):
                return True
        return False

    def _approach(self, dx: float, dy: float, axis: str) -> float:
        full = dx if axis == "x" else dy
        if abs(full) < 1e-6:
            return full
        best = 0.0
        low, high = 0.0, 1.0
        for _step in range(18):
            scale = (low + high) / 2
            trial_x, trial_y = (full * scale, dy) if axis == "x" else (dx, full * scale)
            if self._hits(trial_x, trial_y):
                high = scale
            else:
                best = full * scale
                low = scale
        return best

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


def scroll_by(widget, dx: float, dy: float) -> bool:
    """Сдвигает холст по одной оси и не трогает вторую, даже если там ещё есть поля."""
    metrics = widget.scroll_metrics()
    if metrics is None or (dx == 0 and dy == 0):
        return False
    content_w, content_h, origin_x, origin_y = metrics
    new_x, new_y = origin_x, origin_y
    if dx:
        span = max(0, content_w - widget.width())
        new_x = max(-span, min(max(0, origin_x), origin_x + dx))
    if dy:
        span = max(0, content_h - widget.height())
        new_y = max(-span, min(max(0, origin_y), origin_y + dy))
    if new_x == origin_x and new_y == origin_y:
        return False
    widget.scroll_to(
        None if dx == 0 else -int(round(new_x)),
        None if dy == 0 else -int(round(new_y)),
    )
    return True


def wheel_scroll(widget, event) -> bool:
    """Колесо без Ctrl листает холст: вверх к началу, вниз дальше."""
    metrics = widget.scroll_metrics()
    if metrics is None:
        return False
    content_w, content_h, _origin_x, _origin_y = metrics
    vertical = event.angleDelta().y()
    horizontal = event.angleDelta().x()
    if vertical:
        if content_h <= widget.height():
            return False
        notches = vertical / 120 or (1 if vertical > 0 else -1)
        step = max(32, widget.height() // 14)
        moved = scroll_by(widget, 0, notches * step)
    elif horizontal:
        if content_w <= widget.width():
            return False
        notches = horizontal / 120 or (1 if horizontal > 0 else -1)
        step = max(32, widget.width() // 14)
        moved = scroll_by(widget, notches * step, 0)
    else:
        return False
    if not moved:
        return False
    event.accept()
    return True


class ViewTransform:
    """Масштаб показа. Пока его не трогали, снимок всегда вписан в окно."""

    def __init__(self, widget: QWidget) -> None:
        self.widget = widget
        self.fitted = True
        self.scale = 1.0
        self.ox = 0.0
        self.oy = 0.0

    def current(self, content: tuple[int, int] | None) -> tuple[float, float, float]:
        if self.fitted or content is None:
            return self._fit(content)
        return self.scale, self.ox, self.oy

    def _fit(self, content: tuple[int, int] | None) -> tuple[float, float, float]:
        if content is None:
            return 1.0, 0.0, 0.0
        width, height = content
        if width < 1 or height < 1:
            return 1.0, 0.0, 0.0
        scale = min((self.widget.width() - 24) / width, (self.widget.height() - 24) / height)
        scale = max(scale, 0.01)
        return scale, (self.widget.width() - width * scale) / 2, (self.widget.height() - height * scale) / 2

    def zoom(self, factor: float, anchor: QPointF, content: tuple[int, int] | None) -> None:
        old_scale, ox, oy = self.current(content)
        if old_scale <= 0:
            return
        new_scale = max(0.02, min(40.0, old_scale * factor))
        applied = new_scale / old_scale
        self.fitted = False
        self.scale = new_scale
        self.ox = anchor.x() - (anchor.x() - ox) * applied
        self.oy = anchor.y() - (anchor.y() - oy) * applied
        self.widget.update()

    def pan(self, dx: float, dy: float, content: tuple[int, int] | None) -> None:
        if self.fitted:
            self.scale, self.ox, self.oy = self._fit(content)
            self.fitted = False
        self.ox += dx
        self.oy += dy
        self.widget.update()

    def note_content_resize(self, old_w: int, new_w: int) -> None:
        """Новый снимок того же кадра не сбивает уже выбранный масштаб."""
        if self.fitted or old_w <= 0 or new_w <= 0 or old_w == new_w:
            return
        self.scale *= old_w / new_w


class ViewCanvas:
    """Кнопки масштаба, Ctrl+колесо к курсору и перетаскивание с пробелом."""

    def _init_view(self) -> None:
        self.view = ViewTransform(self)
        self.space_held = False
        self._view_drag: QPoint | None = None

    def _content_size(self) -> tuple[int, int] | None:
        raise NotImplementedError

    def fit_view(self) -> None:
        self.view.fitted = True
        self.update()

    def zoom_view(self, factor: float, anchor: QPointF | None = None) -> None:
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        self.view.zoom(factor, anchor, self._content_size())

    def pan_view(self, dx: float, dy: float) -> None:
        self.view.pan(dx, dy, self._content_size())

    def _view_transform(self) -> tuple[float, float, float]:
        return self.view.current(self._content_size())

    def scroll_metrics(self) -> tuple[int, int, int, int] | None:
        content = self._content_size()
        if content is None:
            return None
        scale, ox, oy = self._view_transform()
        return int(round(content[0] * scale)), int(round(content[1] * scale)), int(round(ox)), int(round(oy))

    def scroll_to(self, sx: int | None = None, sy: int | None = None) -> None:
        content = self._content_size()
        if content is None:
            return
        scale, ox, oy = self._view_transform()
        self.view.fitted = False
        self.view.scale = scale
        self.view.ox = ox if sx is None else -float(sx)
        self.view.oy = oy if sy is None else -float(sy)
        self.update()

    def view_wheel(self, event) -> bool:
        if self._content_size() is None:
            return False
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return wheel_scroll(self, event)
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            return False
        notches = delta / 120
        if notches == 0:
            notches = 1 if delta > 0 else -1
        self.zoom_view(1.1 ** notches, event.position())
        event.accept()
        return True

    def view_mouse_press(self, event) -> bool:
        if not self.space_held or event.button() != Qt.MouseButton.LeftButton or self._content_size() is None:
            return False
        self._view_drag = event.position().toPoint()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def view_mouse_move(self, event) -> bool:
        if self._view_drag is None:
            if self.space_held:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            return False
        point = event.position().toPoint()
        delta = point - self._view_drag
        self._view_drag = point
        self.pan_view(delta.x(), delta.y())
        return True

    def view_mouse_release(self, _event) -> bool:
        if self._view_drag is None:
            return False
        self._view_drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.space_held else Qt.CursorShape.ArrowCursor)
        return True


class SourceCanvas(ViewCanvas, QWidget):
    """Исходник с рамкой. По двум точкам видны макушка и подбородок, по трём — ещё и глаза."""

    pan = Signal(float, float)
    zoom = Signal(float)
    ruler = Signal(str)
    guide = Signal(str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._init_view()
        self.pixmap: QPixmap | None = None
        self.crop = None
        self.fmt = None
        self.angle = 0.0
        self.eye_t = 0.55
        self.guide_lines: tuple[float, float, float] | None = None
        self._origin: QPoint | None = None
        self._handle: str | None = None
        self.ruler_points: list[tuple[float, float]] = []
        self.setMinimumSize(480, 420)
        self.setMouseTracking(True)

    def _content_size(self) -> tuple[int, int] | None:
        if self.pixmap is None or self.pixmap.isNull():
            return None
        return self.pixmap.width(), self.pixmap.height()

    def set_frame(
        self,
        pixmap: QPixmap,
        crop,
        fmt,
        angle: float = 0.0,
        eye_t: float = 0.55,
        guide: tuple[float, float, float] | None = None,
    ) -> None:
        old = self.pixmap.width() if self.pixmap is not None and not self.pixmap.isNull() else 0
        self.pixmap = pixmap
        self.crop = crop
        self.fmt = fmt
        self.angle = angle
        self.eye_t = eye_t
        self.guide_lines = guide
        self.view.note_content_resize(old, 0 if pixmap is None or pixmap.isNull() else pixmap.width())
        self.update()

    def _fit(self) -> tuple[float, float, float]:
        return self._view_transform()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window))
        if self.pixmap is None or self.crop is None or self.fmt is None:
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Откройте снимок или папку")
            painter.end()
            return
        scale, ox, oy = self._fit()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._view_drag is None)
        photo = QRectF(ox, oy, self.pixmap.width() * scale, self.pixmap.height() * scale)
        painter.drawPixmap(photo, self.pixmap, QRectF(self.pixmap.rect()))
        rect = self._frame_rect()
        self._shade_outside(painter, photo, rect)
        painter.save()
        painter.translate(rect.center())
        painter.rotate(self.angle)
        painter.translate(-rect.center())
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawRect(rect.adjusted(-1, -1, -1, -1))
        painter.setPen(QPen(QColor("#111"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor("#222"), 1))
        ratios = self._shown_guides()
        for ratio in ratios.values():
            y = rect.top() + ratio * rect.height()
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
        painter.drawLine(int(rect.center().x()), int(rect.top()), int(rect.center().x()), int(rect.bottom()))
        crown_y = rect.top() + ratios["crown"] * rect.height()
        chin_y = rect.top() + ratios["chin"] * rect.height()
        head_h = chin_y - crown_y
        oval_w = head_h * 0.75
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("white"), 3))
        painter.drawEllipse(QRectF(rect.center().x() - oval_w / 2, crown_y, oval_w, head_h))
        painter.setPen(QPen(QColor("#222"), 1))
        painter.drawEllipse(QRectF(rect.center().x() - oval_w / 2, crown_y, oval_w, head_h))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#4a4a4a"), 1))
        painter.setBrush(QColor("white"))
        for handle in self._handles().values():
            painter.drawEllipse(QRectF(handle.x() - 7, handle.y() - 7, 14, 14))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.restore()
        if len(self.ruler_points) == 2 and self.crop is not None:
            painter.setPen(QPen(QColor("#9a3412"), 2))
            a, b = self.ruler_points
            painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        painter.end()

    def wheelEvent(self, event) -> None:
        if self.pixmap is None:
            return
        self.view_wheel(event)

    def mousePressEvent(self, event) -> None:
        if self.view_mouse_press(event):
            return
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
        if self.view_mouse_move(event):
            return
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

    def mouseReleaseEvent(self, event) -> None:
        if self.view_mouse_release(event):
            return
        self._origin = None
        self._handle = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.space_held else Qt.CursorShape.ArrowCursor)

    def _shade_outside(self, painter: QPainter, photo: QRectF, rect: QRectF) -> None:
        """Как в PhotoLab: всё, что осталось за рамкой кадрирования, темнее."""
        outside = QPainterPath()
        outside.addRect(photo)
        hole = QPainterPath()
        hole.addRect(rect)
        if abs(self.angle) > 1e-3:
            center = rect.center()
            turn = QTransform()
            turn.translate(center.x(), center.y())
            turn.rotate(self.angle)
            turn.translate(-center.x(), -center.y())
            hole = turn.map(hole)
        painter.save()
        painter.setClipRect(photo)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillPath(outside.subtracted(hole), QColor(0, 0, 0, 90))
        painter.restore()

    def _frame_rect(self) -> QRectF:
        scale, ox, oy = self._fit()
        return QRectF(ox + self.crop.left * scale, oy + self.crop.top * scale, self.crop.width * scale, self.crop.height * scale)

    def _shown_guides(self) -> dict[str, float]:
        """Двухточечный формат — макушка и подбородок. Трёхточечный добавляет линию глаз."""
        if self.guide_lines is not None:
            crown_r, eye_r, chin_r = self.guide_lines
        else:
            crown_r, eye_r, chin_r = guide_ratios(self.fmt, self.eye_t)
        if self.fmt.two_points:
            return {"crown": crown_r, "chin": chin_r}
        return {"crown": crown_r, "eyes": eye_r, "chin": chin_r}

    def _handles(self) -> dict[str, QPointF]:
        rect = self._frame_rect()
        return {
            name: QPointF(rect.center().x(), rect.top() + ratio * rect.height())
            for name, ratio in self._shown_guides().items()
        }

    def _unrotate(self, point: QPoint) -> QPointF:
        center = self._frame_rect().center()
        rad = math.radians(-self.angle)
        c, s = math.cos(rad), math.sin(rad)
        dx, dy = point.x() - center.x(), point.y() - center.y()
        return QPointF(center.x() + dx * c - dy * s, center.y() + dx * s + dy * c)

    def _hit(self, point: QPoint) -> str | None:
        if self.crop is None or self.fmt is None or self.pixmap is None:
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
