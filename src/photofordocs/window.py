"""Окно стойки: размеры, готовая фотография, лист печати."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import QDir, QEvent, QFileSystemWatcher, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDrag, QGuiApplication, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPalette, QPen, QPixmap, QPolygon, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QHeaderView,
    QMainWindow,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QRadioButton,
    QStyle,
    QStyleFactory,
    QScrollArea,
    QSizePolicy,
    QScrollBar,
    QSlider,
    QSplitter,
    QSpinBox,
    QDoubleSpinBox,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from photofordocs.canvas import READY_MIME, PrintCanvas, SourceCanvas, ViewCanvas, scroll_by
from photofordocs.dialogs import FormatCatalog, HistogramDialog, JournalDialog, SaveFileDialog, SettingsDialog
from photofordocs.clothes import all_clothes
from photofordocs.formats import Format, import_photolab_file, load_formats
from photofordocs.imageops import (
    detect_face,
    detected_eye_t,
    downscale,
    contain_guides,
    format_eye_t,
    ends_eye_t,
    head_share,
    head_pose_pixels,
    guide_ratios_from_face,
    frame_points,
    Rect,
    background_alpha,
    hex_to_rgb,
    open_image,
    ZOOM_MAX,
    ZOOM_MIN,
    initial_frame_fit,
    place_crop,
    move_middle,
    pose_from_ends,
    render_crop,
    render_photo,
    write_jpeg,
    zoom_shift_for_box,
)
from photofordocs import winprint
from photofordocs.session import (
    PAPER_MM,
    ReadyItem,
    Settings,
    SheetDoc,
    SheetItem,
    add_journal,
    archive_names,
    month_folder,
    day_total,
    load_ready_info,
    photolab_format_specs,
    read_ready,
    ready_belongs,
    ready_folder,
    ready_info_path,
    ready_photo_path,
    write_ready_info,
    load_journal,
    load_scheme,
    load_settings,
    next_number,
    paper_mm,
    sheet_edges,
    _slot,
    append_photos,
    center_photos,
    photo_too_big,
    report_text,
    save_scheme,
    save_settings,
    startup_folder,
    send_report,
    split_ready_name,
    SCHEMES_DIR,
)
from photofordocs.phs import import_phs
from photofordocs.sheet import PAPERS, Paper, plan_sheet, render_document

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


@dataclass
class Shot:
    path: Path
    image: Image.Image
    working: Image.Image
    face: object
    zoom: float = 1.0
    shift_x: float = 0.0
    shift_y: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    gamma: float = 0.0
    saturation: float = 0.0
    sharpness: float = 0.0
    black_point: float = 0.0
    white_point: float = 255.0
    tolerance: float = 42.0
    replace: bool = False
    clothes: str = ""
    angle: float = 0.0
    eye_t: float = 0.55
    guides_manual: bool = False
    # Линия глаз, которую оператор оставил на этом снимке. Двухточечный формат
    # её не показывает, но при возврате к трёхточечному она должна встать туда же.
    eye_line: float = 0.55
    crown: tuple[float, float] | None = None
    chin: tuple[float, float] | None = None


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



class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Фото на документы")
        self._ui_ready = False
        self.print_ad = True
        self.formats = load_formats()
        self.settings = load_settings()
        self.journal = load_journal()
        self.shots: list[Shot] = []
        self.folder_root: Path | None = None
        self.index = -1
        self.ready: list[ReadyItem] = []
        self.doc = self._fresh_doc()
        self.pages: list[SheetDoc] = [self.doc]
        self.page_index = 0
        self._hotkeys: list[QShortcut] = []
        self._seq = 0
        self._last_saved = ""
        self.clothes = all_clothes()
        self._watcher = None
        self.branches: dict[str, list[dict]] = {}
        self._file_order = (self.settings.sort_key, self.settings.sort_reverse)
        self._pending_folder: Path | None = None
        self.allow_rotate = bool(self.settings.allow_rotate)
        self._paper_horizontal = bool(self.settings.print_horizontal)
        self._bg_cache: dict[int, tuple] = {}
        self._alpha_cache: dict[int, tuple] = {}
        self._live_cache: dict[int, tuple] = {}
        self._sheet_pixmaps: dict[str, tuple] = {}
        self._shot_stamps: dict[str, tuple[int, int]] = {}
        self._source_watch = QFileSystemWatcher(self)
        self._source_watch.fileChanged.connect(self._on_source_file_changed)
        self._edit_poll = QTimer(self)
        self._edit_poll.setSingleShot(True)
        self._edit_poll.timeout.connect(self._poll_source_files)
        self._build()
        self._menu()
        self._apply_workplace()
        self._bind_hotkeys()
        self._set_mode("size")
        self.statusBar().showMessage("Откройте снимок или папку.")
        QApplication.instance().installEventFilter(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ui_ready = True

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._poll_source_files()

    def _text_focus(self) -> bool:
        widget = QApplication.focusWidget()
        return isinstance(widget, (QLineEdit, QAbstractSpinBox, QComboBox))

    def _set_space(self, held: bool) -> None:
        for canvas in (self.source, self.finished, self.printer):
            canvas.space_held = held
            if not held:
                canvas._view_drag = None
            if canvas._view_drag is None:
                canvas.setCursor(Qt.CursorShape.OpenHandCursor if held else Qt.CursorShape.ArrowCursor)

    def _view_host(self, canvas: QWidget) -> QWidget:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.setRowStretch(0, 1)
        grid.setColumnStretch(0, 1)
        grid.addWidget(canvas, 0, 0)
        self._add_scrollbars(grid, canvas)
        self._zoom_bar(canvas)
        return host

    def _add_scrollbars(self, grid: QGridLayout, canvas: QWidget) -> None:
        """Полосы у холста. Видны всегда, чтобы лист не прыгал, когда появляется запас."""
        if not hasattr(self, "_scrollbars"):
            self._scrollbars = {}
            self._scroll_lock = False
            self._scroll_pending = set()
        vertical = QScrollBar(Qt.Orientation.Vertical)
        horizontal = QScrollBar(Qt.Orientation.Horizontal)
        vertical.setFixedWidth(14)
        horizontal.setFixedHeight(14)
        corner = QWidget()
        corner.setObjectName("scrollcorner")
        corner.setFixedSize(14, 14)
        grid.addWidget(vertical, 0, 1)
        grid.addWidget(horizontal, 1, 0)
        grid.addWidget(corner, 1, 1)
        self._scrollbars[canvas] = (horizontal, vertical)
        horizontal.valueChanged.connect(lambda value, target=canvas: self._scroll_from_bar(target, "x", value))
        vertical.valueChanged.connect(lambda value, target=canvas: self._scroll_from_bar(target, "y", value))

    def _scroll_from_bar(self, canvas: QWidget, axis: str, value: int) -> None:
        if self._scroll_lock or canvas not in self._scrollbars:
            return
        metrics = canvas.scroll_metrics()
        if metrics is None:
            return
        _content_w, _content_h, origin_x, origin_y = metrics
        origin = origin_x if axis == "x" else origin_y
        delta = max(0, -int(origin)) - int(value)
        if axis == "x":
            scroll_by(canvas, delta, 0)
        else:
            scroll_by(canvas, 0, delta)

    def _refresh_scroll(self, canvas: QWidget) -> None:
        if getattr(self, "_scroll_lock", False):
            return
        pair = self._scrollbars.get(canvas)
        if pair is None or not hasattr(canvas, "scroll_metrics"):
            return
        metrics = canvas.scroll_metrics()
        if metrics is None:
            content_w = content_h = origin_x = origin_y = 0
        else:
            content_w, content_h, origin_x, origin_y = metrics
        horizontal, vertical = pair
        hmax = max(0, int(content_w) - max(1, canvas.width()))
        vmax = max(0, int(content_h) - max(1, canvas.height()))
        self._scroll_lock = True
        horizontal.blockSignals(True)
        vertical.blockSignals(True)
        horizontal.setPageStep(max(1, canvas.width()))
        vertical.setPageStep(max(1, canvas.height()))
        horizontal.setSingleStep(max(16, canvas.width() // 14))
        vertical.setSingleStep(max(16, canvas.height() // 14))
        horizontal.setRange(0, hmax)
        vertical.setRange(0, vmax)
        horizontal.setValue(min(hmax, max(0, -int(origin_x))))
        vertical.setValue(min(vmax, max(0, -int(origin_y))))
        horizontal.blockSignals(False)
        vertical.blockSignals(False)
        self._scroll_lock = False

    def _overlay(self, bar: QWidget) -> QWidget:
        """Панель поверх холста: сама прозрачная, кнопки просвечивают снимок."""
        bar.setObjectName("overlaybar")
        bar.setAutoFillBackground(False)
        bar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return bar

    def _glass_button(self, button: QWidget) -> QWidget:
        button.setAutoFillBackground(False)
        button.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return button

    def _zoom_bar(self, canvas: QWidget) -> QWidget:
        bar = self._overlay(QWidget(canvas))
        column = QVBoxLayout(bar)
        column.setContentsMargins(0, 0, 10, 10)
        column.setSpacing(4)
        for kind, tip, slot in (
            ("fit", "По размеру окна", canvas.fit_view),
            ("zoom-in", "Увеличить", lambda: canvas.zoom_view(1.25)),
            ("zoom-out", "Уменьшить", lambda: canvas.zoom_view(1 / 1.25)),
        ):
            button = self._glass_button(QToolButton())
            button.setObjectName("viewzoom")
            button.setIcon(self._zoom_icon(kind))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(28, 28)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, action=slot: action())
            column.addWidget(button)
        if not hasattr(self, "_zoom_bars"):
            self._zoom_bars = {}
        self._zoom_bars[canvas] = bar
        bar.show()
        self._place_overlays(canvas)
        return bar

    def _place_overlays(self, canvas: QWidget) -> None:
        """Кнопки остаются в углу холста и не уезжают вместе с прокруткой."""
        zoom = getattr(self, "_zoom_bars", {}).get(canvas)
        if zoom is not None:
            zoom.adjustSize()
            zoom.move(max(0, canvas.width() - zoom.width()), max(0, canvas.height() - zoom.height()))
            zoom.raise_()
        pages = getattr(self, "_page_bar", None)
        if pages is not None and canvas is getattr(self, "printer", None):
            pages.adjustSize()
            pages.move(0, 0)
            pages.raise_()

    def _sheet_host(self) -> QWidget:
        """Лист и кнопки страниц поверх него, сверху слева."""
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.setRowStretch(0, 1)
        grid.setColumnStretch(0, 1)
        grid.addWidget(self.printer, 0, 0)
        self._add_scrollbars(grid, self.printer)
        bar = self._overlay(QWidget(self.printer))
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 10, 0, 0)
        row.setSpacing(4)
        self.page_prev = self._glass_button(QToolButton())
        self.page_prev.setObjectName("viewzoom")
        self.page_prev.setText("‹")
        self.page_prev.setFixedSize(28, 28)
        self.page_prev.setToolTip("Предыдущий лист")
        self.page_prev.clicked.connect(lambda _checked=False: self._show_page(self.page_index - 1))
        self.page_no = self._glass_button(QToolButton())
        self.page_no.setObjectName("viewzoom")
        self.page_no.setText("1")
        self.page_no.setFixedSize(28, 28)
        self.page_no.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.page_no.setToolTip("Лист 1")
        self.page_next = self._glass_button(QToolButton())
        self.page_next.setObjectName("viewzoom")
        self.page_next.setText("›")
        self.page_next.setFixedSize(28, 28)
        self.page_next.setToolTip("Следующий лист")
        self.page_next.clicked.connect(lambda _checked=False: self._show_page(self.page_index + 1))
        row.addWidget(self.page_prev)
        row.addWidget(self.page_no)
        row.addWidget(self.page_next)
        self._page_bar = bar
        bar.show()
        self._zoom_bar(self.printer)
        self._place_overlays(self.printer)
        return host

    def _zoom_icon(self, kind: str) -> QIcon:
        pix = QPixmap(18, 18)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        blue = QColor("#2a62c9")
        painter.setPen(QPen(blue, 1.4))
        if kind == "fit":
            painter.drawRect(5, 5, 8, 8)
            for x1, y1, x2, y2 in (
                (1, 4, 4, 4),
                (4, 1, 4, 4),
                (14, 4, 17, 4),
                (14, 1, 14, 4),
                (1, 14, 4, 14),
                (4, 14, 4, 17),
                (14, 14, 17, 14),
                (14, 14, 14, 17),
            ):
                painter.drawLine(x1, y1, x2, y2)
        else:
            painter.drawEllipse(1, 1, 11, 11)
            painter.drawLine(10, 10, 16, 16)
            painter.drawLine(4, 6, 9, 6)
            if kind == "zoom-in":
                painter.drawLine(6, 4, 6, 9)
        painter.end()
        return QIcon(pix)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        left = QFrame()
        left.setObjectName("filepane")
        left.setFrameShape(QFrame.Shape.StyledPanel)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        self.drive_box = QComboBox()
        self.drive_box.setToolTip("Диск")
        self.dir_model = QFileSystemModel(self)
        self.dir_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.dir_model.setRootPath(QDir.homePath())
        self.dir_model.directoryLoaded.connect(self._directory_loaded)
        self.folders = QTreeView()
        self.folders.setModel(self.dir_model)
        self.folders.setHeaderHidden(True)
        self.folders.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.folders.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for column in range(1, 4):
            self.folders.hideColumn(column)
        self.folders.clicked.connect(self._folder_clicked)
        self.folders.setMinimumHeight(70)
        self.files = FileTree()
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.delete_requested.connect(self._delete_selected_file)
        self.files.setHeaderHidden(True)
        self.files.setDragEnabled(True)
        self.files.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.itemClicked.connect(self._file_clicked)
        self.files.itemDoubleClicked.connect(self._file_activated)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._file_menu)
        self.files.setMinimumHeight(70)
        self.files.setIconSize(QSize(THUMB_SIDE, THUMB_SIDE))
        self._thumbs: dict[str, tuple[int, int, QIcon]] = {}
        self._thumb_items: dict[str, QTreeWidgetItem] = {}
        self._thumb_folder: Path | None = None
        self._thumb_queue: list[str] = []
        self._thumb_token = 0
        self._thumb_arm = QTimer(self)
        self._thumb_arm.setSingleShot(True)
        self._thumb_arm.timeout.connect(self._arm_thumbs)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._thumb_step)
        self.files.verticalScrollBar().valueChanged.connect(lambda *_args: self._schedule_thumbs())
        self.files.itemExpanded.connect(self._opened_branch)
        self.files.itemCollapsed.connect(lambda *_args: self._schedule_thumbs())
        self.files.viewport().installEventFilter(self)
        files_box = QWidget()
        files_layout = QVBoxLayout(files_box)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)
        files_layout.addWidget(self.files, 1)
        files_layout.addLayout(self._file_tools())
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setMinimumHeight(70)
        self.thumb.setFrameShape(QFrame.Shape.Box)
        self.thumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._thumb_source: QPixmap | None = None
        self.thumb.installEventFilter(self)
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.setChildrenCollapsible(False)
        self.left_split.addWidget(self.folders)
        self.left_split.addWidget(files_box)
        self.left_split.addWidget(self.thumb)
        self.left_split.setStretchFactor(0, 2)
        self.left_split.setStretchFactor(1, 2)
        self.left_split.setStretchFactor(2, 1)
        self.left_split.setSizes(self._left_sizes())
        self._left_save = QTimer(self)
        self._left_save.setSingleShot(True)
        self._left_save.setInterval(400)
        self._left_save.timeout.connect(self._save_left_sizes)
        self.left_split.splitterMoved.connect(lambda *_args: self._left_save.start())
        left_layout.addWidget(self.drive_box)
        left_layout.addWidget(self.left_split, 1)
        self.browser = left
        left.setMinimumWidth(180)
        self._fill_drives()

        center_box = QVBoxLayout()
        center_box.setSpacing(0)
        steps = QHBoxLayout()
        steps.setSpacing(0)
        self.step_group = QButtonGroup(self)
        self.step_buttons: dict[str, QPushButton] = {}
        for key, title in (("size", "Размеры"), ("ready", "Готовая фотография"), ("print", "Печать")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setObjectName("step")
            self.step_group.addButton(button)
            self.step_buttons[key] = button
            button.clicked.connect(lambda _checked=False, name=key: self._set_mode(name))
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(title) + 22)
            steps.addWidget(button)
        steps.addStretch(1)
        self.source = SourceCanvas()
        self.source.pan.connect(self._pan)
        self.source.zoom.connect(self._zoom)
        self.source.guide.connect(self._nudge_guide)
        self.source.ruler.connect(lambda text: self.statusBar().showMessage(text))
        self.finished = FinishedView()
        self.finished.resized.connect(self._schedule_preview)
        self._finished_image: Image.Image | None = None
        self._mode = "size"
        self.printer = PrintCanvas()
        self.printer.changed.connect(self._sheet_changed)
        self.printer.delete_selected.connect(self._delete_from_sheet)
        self.printer.ready_dropped.connect(self._drop_ready)
        self.printer.status.connect(lambda text: self.statusBar().showMessage(text))
        self.print_host = QWidget()
        host_layout = QVBoxLayout(self.print_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        sheet_column = QWidget()
        sheet_layout = QVBoxLayout(sheet_column)
        sheet_layout.setContentsMargins(0, 0, 0, 0)
        sheet_layout.setSpacing(0)
        sheet_layout.addWidget(self._sheet_host(), 1)
        self.print_split = QSplitter(Qt.Orientation.Vertical)
        self.print_split.setChildrenCollapsible(True)
        self.print_split.addWidget(sheet_column)
        self.print_split.addWidget(self.print_host)
        self.print_split.setStretchFactor(0, 1)
        self.print_split.setSizes([800, 0])
        self.stack = QStackedWidget()
        self.stack.addWidget(self._view_host(self.source))
        self.stack.addWidget(self._view_host(self.finished))
        self.stack.addWidget(self.print_split)
        center_box.addLayout(steps)
        center_box.addWidget(self.stack, 1)
        center = QWidget()
        center.setLayout(center_box)

        self.right = QStackedWidget()
        self.right.addWidget(self._sizes_side())
        self.right.addWidget(self._finished_side())
        self.print_panel = self._print_side()
        self.right.addWidget(self.print_panel)
        self._paper_changed()
        self.right.setMinimumWidth(260)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(self.right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([210, 720, 300])
        self.splitter = splitter
        layout.addWidget(splitter)
        self._apply_workplace()
        from photofordocs.theme import bind_theme

        bind_theme(QApplication.instance())
        self.refresh_theme()

    def _slider(self, low: int, high: int, value: int, handler) -> QSlider:
        slider = UnitSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setSingleStep(1)
        slider.setValue(value)
        slider.valueChanged.connect(handler)
        self._paint_slider(slider, "#d5d5d5")
        return slider

    def _paint_slider(self, slider: QSlider, groove: str, *, remember: bool = True) -> None:
        """Одна и та же круглая ручка. Цвет дорожки задаёт смысл ползунка."""
        from photofordocs.theme import slider_chrome

        if remember or not slider.property("base_groove"):
            slider.setProperty("base_groove", groove)
        handle, edge, border = slider_chrome()
        slider.setStyleSheet(
            "QSlider::groove:horizontal {"
            f" height: 8px; border-radius: 4px; border: 1px solid {border}; background: {groove};"
            "}"
            "QSlider::handle:horizontal {"
            " width: 14px; height: 14px; margin: -4px 0; border-radius: 7px;"
            f" background: {handle}; border: 1px solid {edge};"
            "}"
        )

    def refresh_theme(self) -> None:
        """Ползунки держат свой цвет отдельно от общей таблицы, поэтому их перекрашиваем сами."""
        from photofordocs.theme import is_dark

        dark = is_dark()
        for slider in self.findChildren(UnitSlider):
            groove = slider.property("base_groove") or "#d5d5d5"
            if groove == "#d5d5d5" and dark:
                groove = "#5a5a5a"
            self._paint_slider(slider, groove, remember=False)

    def _spin_width(self, sample: str) -> int:
        return self.fontMetrics().horizontalAdvance(sample) + 20

    def _scroll(self, page: QWidget) -> QScrollArea:
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _action_button(self, kind: str, text: str, slot) -> QToolButton:
        button = QToolButton()
        button.setObjectName("sizeaction")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIcon(self._file_icon(kind))
        button.setIconSize(QSize(22, 22))
        button.setText(text)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(36)
        button.clicked.connect(slot)
        return button

    def _action_grid(self) -> QGridLayout:
        buttons = (
            self._action_button("save", "Сохранить\nфото", self.save_photo),
            self._action_button("save-file", "Сохранить\nв файл", self.save_photo_as),
            self._action_button("print", "Печать", self._print_from_panel),
            self._action_button("pencil", "Редактор", self._external_editor),
        )
        grid = QGridLayout()
        grid.setSpacing(4)
        for index, button in enumerate(buttons):
            grid.addWidget(button, index // 2, index % 2)
        return grid

    def _format_tooltip(self, fmt: Format) -> str:
        note = (fmt.note or "").strip()
        if not note:
            return f"{fmt.summary()}. Без описания."
        return f"{fmt.summary()}. {note}"

    def _fill_format_combo(self, box: QComboBox) -> None:
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(14)
        box.blockSignals(True)
        box.clear()
        for fmt in self.formats:
            box.addItem(fmt.name, fmt.id)
            idx = box.count() - 1
            box.setItemData(idx, self._format_tooltip(fmt), Qt.ItemDataRole.ToolTipRole)
        box.blockSignals(False)

    def _sync_format_combo_tooltip(self) -> None:
        idx = self.format_box.currentIndex()
        if idx < 0:
            return
        ident = self.format_box.currentData()
        match = next((item for item in self.formats if item.id == ident), None)
        if match is not None:
            self.format_box.setToolTip(self._format_tooltip(match))

    def _mm_box(self, low: float, high: float, value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(1)
        box.setSingleStep(0.5)
        box.setSuffix(" мм")
        box.setValue(value)
        return box

    def _sizes_side(self) -> QScrollArea:
        self._format_lock = False
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 6, 8)
        layout.setSpacing(8)
        self._sizes_layout = layout
        self.actions_panel = self._actions_box()
        layout.addWidget(self.actions_panel)
        layout.addWidget(self._format_params_box())
        layout.addStretch(1)
        page.setMinimumWidth(240)
        scroll = self._scroll(page)
        page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return scroll

    def _actions_box(self) -> QGroupBox:
        box = QGroupBox("Действия")
        inner = QVBoxLayout(box)
        if not hasattr(self, "format_box"):
            self.format_box = QComboBox()
            self._fill_format_combo(self.format_box)
            self._sync_format_combo_tooltip()
            self.rotate_box = QCheckBox("Разрешить поворот")
            self.rotate_box.setToolTip("Точки рамки можно уводить в сторону, рамка наклоняется и остаётся прямой. Без галочки она вертикальна.")
            self.rotate_box.setChecked(self.settings.allow_rotate)
            self.rotate_box.toggled.connect(self._allow_rotate)
            inner.addWidget(self.format_box)
            inner.addWidget(self.rotate_box)
        inner.addLayout(self._action_grid())
        return box

    def _labeled_mm(self, title: str, spin: QDoubleSpinBox) -> QWidget:
        spin.setSuffix("")
        spin.setFixedWidth(92)
        spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        label = QLabel(title)
        label.setMinimumWidth(92)
        column.addWidget(label)
        column.addWidget(spin)
        return host

    def _format_params_box(self) -> QGroupBox:
        box = QGroupBox("Параметры формата")
        self.format_params = box
        self.fmt_width = self._mm_box(10, 220, 35)
        self.fmt_height = self._mm_box(10, 260, 45)
        self.fmt_top = self._mm_box(0, 80, 2)
        self.fmt_bottom = self._mm_box(0.5, 80, 9)
        self.fmt_head = self._mm_box(1, 200, 34)
        self.dpi_box = QSpinBox(box)
        self.dpi_box.setRange(72, 1200)
        self.dpi_box.setValue(EXPORT_DPI)
        self.dpi_box.hide()
        self.retouch = QCheckBox("Ретушь", box)
        self.retouch.hide()
        self.ready_gray = QCheckBox("Цветная")
        self.ready_gray.setChecked(True)
        self.corner_on = QCheckBox("Уголок")
        self.corner = QComboBox()
        self.corner.addItem("Как в формате", "format")
        self.corner.addItem("Прямой слева", "straight-left")
        self.corner.addItem("Прямой справа", "straight-right")
        self.corner.addItem("Круглый слева", "round-left")
        self.corner.addItem("Круглый справа", "round-right")
        self.corner.setToolTip("Левый и правый уголок ставятся снизу. Прямой — треугольник, круглый — четверть круга из угла.")
        self.corner_radius = self._mm_box(0.5, 40, 18)
        self.corner_radius.setFixedWidth(92)
        self.corner_radius.setToolTip("Размер среза от угла кадра, в миллиметрах.")
        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel("Радиус"))
        radius_row.addWidget(self.corner_radius)
        radius_row.addStretch(1)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(self._labeled_mm("Ширина", self.fmt_width), 0, 0)
        grid.addWidget(self._labeled_mm("Высота", self.fmt_height), 0, 1)
        grid.addWidget(self._labeled_mm("Верх. поле", self.fmt_top), 1, 0)
        grid.addWidget(self._labeled_mm("Нижн. поле", self.fmt_bottom), 1, 1)
        grid.addWidget(self._labeled_mm("Голова", self.fmt_head), 2, 0)
        flags = QHBoxLayout()
        flags.addWidget(self.ready_gray)
        flags.addWidget(self.corner_on)
        flags.addStretch(1)
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        layout.addLayout(grid)
        layout.addLayout(flags)
        layout.addWidget(self.corner)
        layout.addLayout(radius_row)
        self.head_share = QLabel("Голова —")
        self.head_share.setWordWrap(True)
        self.head_share.setToolTip(
            "Доля высоты кадра между верхней и нижней лапкой. "
            "Требование «не менее 70%» считают так же: голова занимает столько полос, "
            "если высоту кадра разделить на десять равных частей."
        )
        layout.addWidget(self.head_share)
        box.setMinimumWidth(220)
        return box

    def _finished_side(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 4, 6, 8)
        layout.setSpacing(8)
        self._ready_layout = layout
        self.replace_box = QCheckBox("Не корректировать фон")
        self.replace_box.setChecked(bool(getattr(self.settings, "replace_background", False)))
        self.replace_box.toggled.connect(self._store_tone)
        self.fill_edge = QRadioButton("От краёв")
        self.fill_figure = QRadioButton("По фигуре")
        self.fill_method = QButtonGroup(self)
        self.fill_method.addButton(self.fill_edge, 0)
        self.fill_method.addButton(self.fill_figure, 1)
        if getattr(self.settings, "background_method", "edge") == "figure":
            self.fill_figure.setChecked(True)
        else:
            self.fill_edge.setChecked(True)
        self.fill_method.idClicked.connect(lambda _index: self._store_tone())
        self.paint_box = QCheckBox("Заменить цвет")
        self.paint_box.setChecked(bool(getattr(self.settings, "paint_background", False)))
        self.paint_box.toggled.connect(self._store_tone)
        self.color_box = QComboBox()
        self.color_box.addItem("Как в формате", "format")
        self.color_box.addItem("Белый", "#FFFFFF")
        self.color_box.addItem("Серый", "#E6E6E6")
        self.color_box.addItem("Голубой", "#D6E6F5")
        self.color_box.currentIndexChanged.connect(self._store_tone)
        sensitivity = 42
        if 0 < self.settings.ground_threshold <= 20:
            sensitivity = int(min(90, self.settings.ground_threshold * 10))
        self.tolerance = self._slider(0, 100, sensitivity, self._store_tone)
        self.bright = self._slider(-100, 100, int(self.settings.tone_brightness), self._store_tone)
        self.contrast = self._slider(-100, 100, int(self.settings.tone_contrast), self._store_tone)
        self.gamma = self._slider(-100, 100, int(self.settings.tone_gamma), self._store_tone)
        self.saturation = self._slider(-100, 100, int(self.settings.tone_saturation), self._store_tone)
        self.sharpness = self._slider(0, 100, int(self.settings.tone_sharpness), self._store_tone)
        self.black = self._slider(0, 80, 0, self._store_tone)
        self.white = self._slider(175, 255, 255, self._store_tone)
        self.balance = []
        for _band in range(3):
            row = []
            for _channel in range(3):
                box = QSpinBox(page)
                box.setRange(-100, 100)
                box.hide()
                row.append(box)
            self.balance.append(tuple(row))
        self.color_sliders = []
        self.color_percents = []
        self.balance_band = QButtonGroup(self)
        color = QGroupBox("Цветокоррекция")
        color_layout = QVBoxLayout(color)
        color_layout.setSpacing(8)
        band_row = QHBoxLayout()
        for index, title in enumerate(("Тени", "Центр", "Блики")):
            radio = QRadioButton(title)
            self.balance_band.addButton(radio, index)
            band_row.addWidget(radio)
            if index == 1:
                radio.setChecked(True)
        self.balance_band.idClicked.connect(lambda _band: self._sync_color_sliders())
        color_layout.addLayout(band_row)
        axes = (
            ("Бирюзовый/красный", "#26c6da", "#ef5350"),
            ("Пурпурный/зелёный", "#e040fb", "#43a047"),
            ("Жёлтый/синий", "#fdd835", "#1e88e5"),
        )
        for channel, (title, left, right) in enumerate(axes):
            color_layout.addWidget(self._color_axis(channel, title, left, right))
        reset_balance = QPushButton("Сброс по всем каналам")
        reset_balance.clicked.connect(self._reset_balance)
        color_layout.addWidget(reset_balance)
        self.brush = self._slider(0, 200, int(self.settings.brush_px), self._store_tone)
        other_color = QPushButton("Другой цвет")
        other_color.clicked.connect(self._pick_background)
        background = QGroupBox("Фон")
        background_layout = QVBoxLayout(background)
        background_layout.setSpacing(8)
        background_layout.addWidget(self.replace_box)
        methods = QHBoxLayout()
        methods.addWidget(self.fill_edge)
        methods.addWidget(self.fill_figure)
        methods.addStretch(1)
        background_layout.addLayout(methods)
        background_layout.addWidget(self._tone_line("Чувствительность", self.tolerance, None))
        background_layout.addWidget(self._tone_line("Размер кисти", self.brush, None))
        background_layout.addWidget(self.paint_box)
        background_layout.addWidget(QLabel("Цвет фона"))
        background_layout.addWidget(self.color_box)
        background_layout.addWidget(other_color)
        self.sharpness_box = QComboBox()
        self.sharpness_box.addItem("Нет", 0)
        self.sharpness_box.addItem("Слабая", 30)
        self.sharpness_box.addItem("Средняя", 60)
        self.sharpness_box.addItem("Сильная", 90)
        self.sharpness_box.currentIndexChanged.connect(self._sharpness_picked)
        self.sharpness.valueChanged.connect(lambda _value: self._sync_sharpness_box())
        self._sync_sharpness_box()
        tools = QGroupBox("Инструменты")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setSpacing(8)
        tools_layout.addWidget(self._tone_line("Яркость", self.bright, 0))
        tools_layout.addWidget(self._tone_line("Контраст", self.contrast, 0))
        tools_layout.addWidget(self._tone_line("Гамма", self.gamma, 0))
        tools_layout.addWidget(self._tone_line("Насыщ.", self.saturation, 0))
        sharp_host = QWidget()
        sharp_layout = QVBoxLayout(sharp_host)
        sharp_layout.setContentsMargins(0, 0, 0, 0)
        sharp_layout.setSpacing(2)
        sharp_layout.addWidget(QLabel("Резкость"))
        sharp = QHBoxLayout()
        sharp.addWidget(self.sharpness_box, 1)
        sharp_reset = QToolButton()
        sharp_reset.setText("×")
        sharp_reset.setFixedSize(22, 22)
        sharp_reset.setToolTip("Сбросить")
        sharp_reset.clicked.connect(lambda: self.sharpness.setValue(0))
        sharp.addWidget(sharp_reset)
        sharp_layout.addLayout(sharp)
        tools_layout.addWidget(sharp_host)
        tools_layout.addWidget(self._tone_line("Чёрная", self.black, 0))
        tools_layout.addWidget(self._tone_line("Белая", self.white, 255))
        reset = QPushButton("Сброс всех коррекций")
        reset.clicked.connect(self.reset_frame)
        tools_layout.addWidget(reset)
        layout.addWidget(color)
        layout.addWidget(tools)
        layout.addWidget(background)
        layout.addStretch(1)
        scroll = self._scroll(page)
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        # Полоса прокрутки всегда занимает свою колонку и не ложится на кнопки.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        return scroll

    def _print_side(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        buttons = QGridLayout()
        buttons.setSpacing(4)
        actions = (
            (0, 0, "print", "Печать", self.print_sheet),
            (0, 1, "save", "Сохранить\nлист", self.save_sheet),
            (1, 0, "clear", "Очистить\nлист", self._clear_sheet),
            (1, 1, "logo", "Добавить\nлоготип", self._add_logo),
        )
        for row, column, kind, title, slot in actions:
            button = QPushButton(title)
            button.setObjectName("printaction")
            button.setMinimumHeight(52)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if kind != "logo":
                button.setIcon(self._print_icon(kind))
                button.setIconSize(QSize(22, 22))
            button.clicked.connect(lambda _checked=False, action=slot: action())
            buttons.addWidget(button, row, column)
        layout.addLayout(buttons)
        tabs = QTabWidget()
        tabs.addTab(self._print_size_tab(), "Размеры")
        tabs.addTab(self._print_options_tab(), "Параметры")
        tabs.addTab(self._print_logo_tab(), "Логотип")
        layout.addWidget(tabs, 1)
        self.sheet_table = ReadyDropTable()
        self.sheet_table.ready_dropped.connect(self._drop_ready)
        self.sheet_table.setHorizontalHeaderLabels(["Формат", "Кол-во", "Цена"])
        self.sheet_table.verticalHeader().setVisible(False)
        self.sheet_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sheet_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sheet_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sheet_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.sheet_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.sheet_table.setMinimumHeight(120)
        self.sheet_table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.sheet_table)
        self.sheet_total = QLabel("Итого:")
        total_font = self.sheet_total.font()
        total_font.setBold(True)
        self.sheet_total.setFont(total_font)
        layout.addWidget(self.sheet_total)
        self._wire_formats()
        return page

    def _print_icon(self, kind: str) -> QIcon:
        pix = QPixmap(24, 24)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if kind == "print":
            painter.setPen(QPen(QColor("#444"), 1.2))
            painter.setBrush(QColor("#f8f8f8"))
            painter.drawRoundedRect(2, 7, 20, 10, 1, 1)
            painter.drawRect(7, 3, 10, 5)
            painter.setBrush(QColor("#d8d8d8"))
            painter.drawRect(6, 17, 12, 3)
        elif kind == "save":
            painter.setPen(QPen(QColor("#444"), 1.1))
            painter.setBrush(QColor("#f7f7f7"))
            painter.drawRect(3, 3, 13, 16)
            painter.setBrush(QColor("#4a7ec4"))
            painter.drawRect(5, 3, 9, 5)
            painter.setBrush(QColor("white"))
            painter.drawRect(6, 12, 7, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2f9d3a"))
            painter.drawPolygon(QPolygon([QPoint(13, 14), QPoint(22, 14), QPoint(17, 21)]))
        else:
            painter.setPen(QPen(QColor("#555"), 1.1))
            painter.setBrush(QColor("white"))
            painter.drawRect(3, 3, 12, 16)
            painter.setPen(QPen(QColor("#2a62c9"), 1.6))
            painter.drawLine(12, 16, 20, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3d7ad6"))
            painter.drawEllipse(17, 3, 5, 5)
        painter.end()
        return QIcon(pix)

    def _field_above(self, title: str, spin: QWidget) -> QWidget:
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(label)
        column.addWidget(spin, 0, Qt.AlignmentFlag.AlignHCenter)
        return host

    def _print_size_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.addWidget(QLabel("Формат бумаги"))
        self.paper = QComboBox()
        for item in PAPERS:
            self.paper.addItem(item.name, item.id)
        self.paper.addItem("Свой размер", "custom")
        paper_index = self.paper.findData(self.settings.default_paper)
        if paper_index >= 0:
            self.paper.setCurrentIndex(paper_index)
        layout.addWidget(self.paper)
        self.paper_w = self._mm_box(50, 400, 100)
        self.paper_h = self._mm_box(50, 500, 150)
        for box in (self.paper_w, self.paper_h):
            box.setSuffix("")
            box.setDecimals(0)
            box.setFixedWidth(72)
        size = QHBoxLayout()
        size.addWidget(QLabel("Размер (мм):"))
        size.addWidget(self.paper_w)
        size.addWidget(QLabel("x"))
        size.addWidget(self.paper_h)
        size.addStretch(1)
        layout.addLayout(size)
        self.portrait = QRadioButton("Книжная")
        self.landscape = QRadioButton("Альбомная")
        self.portrait.setChecked(not self.settings.default_landscape)
        self.landscape.setChecked(self.settings.default_landscape)
        orient = QButtonGroup(page)
        orient.addButton(self.portrait)
        orient.addButton(self.landscape)
        direction = QHBoxLayout()
        direction.addWidget(self.portrait)
        direction.addWidget(self.landscape)
        direction.addStretch(1)
        layout.addLayout(direction)
        self.margin_top = self._edge_box(self.settings.margin_top_mm)
        self.margin_right = self._edge_box(self.settings.margin_right_mm)
        self.margin_bottom = self._edge_box(self.settings.margin_bottom_mm)
        self.margin_left = self._edge_box(self.settings.margin_left_mm)
        fields = QGroupBox("Поля")
        grid = QGridLayout(fields)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.addWidget(self._field_above("Верхнее", self.margin_top), 0, 1)
        grid.addWidget(self._field_above("Левое", self.margin_left), 1, 0)
        grid.addWidget(self._field_above("Правое", self.margin_right), 1, 2)
        grid.addWidget(self._field_above("Нижнее", self.margin_bottom), 2, 1)
        layout.addWidget(fields)
        layout.addStretch(1)
        self.paper.currentIndexChanged.connect(self._paper_changed)
        self.paper_w.valueChanged.connect(self._paper_changed)
        self.paper_h.valueChanged.connect(self._paper_changed)
        self.landscape.toggled.connect(self._paper_changed)
        for box in (self.margin_top, self.margin_right, self.margin_bottom, self.margin_left):
            box.valueChanged.connect(self._paper_changed)
        return page

    def _edge_box(self, value: float = 3) -> QSpinBox:
        box = QSpinBox()
        box.setRange(0, 40)
        box.setValue(int(round(value)))
        box.setFixedWidth(62)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return box

    def _print_options_tab(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 6)
        self.overlap_box = QCheckBox("Контроль наложений фото")
        self.overlap_box.setChecked(self.settings.check_overlap)
        self.inside_box = QCheckBox("Контроль выхода за поля")
        self.inside_box.setChecked(self.settings.check_margins)
        self.center_box = QCheckBox("Центрировать у верхнего края")
        self.center_box.setToolTip("Ряд остаётся по центру листа, верх кадров — у верхнего поля")
        self.center_box.setChecked(self.settings.align_center)
        self.center_box.toggled.connect(lambda checked: setattr(self.settings, "align_center", checked))
        self.ask_printer_box = QCheckBox("Показывать диалог принтера")
        self.ask_printer_box.setChecked(self.settings.show_print_dialog)
        self.clear_after_box = QCheckBox("Очищать после печати")
        self.clear_after_box.setChecked(self.settings.clear_after_print)
        self.footer_box = QCheckBox("Рекламный колонтитул")
        self.footer_box.setChecked(self.settings.show_footer)
        self.footer = QLineEdit(self.settings.advert_text, page)
        self.footer.hide()
        self.footer.textChanged.connect(lambda _text: self._refresh_sheet())
        self.cut_style = QComboBox()
        self.cut_style.addItem("не рисовать", "none")
        self.cut_style.addItem("рамка", "box")
        self.cut_style.addItem("штрих-линии", "dash")
        self.cut_style.addItem("в углах", "corners")
        cut = self.cut_style.findData(self.settings.cut_style or "none")
        if cut >= 0:
            self.cut_style.setCurrentIndex(cut)
        self.snap_box = QCheckBox("Прилипание")
        self.snap_box.setChecked(self.settings.snap)
        self.snap_box.setToolTip("Кадр цепляется за край соседнего и за поле, когда почти совпал")
        for box in (self.overlap_box, self.inside_box, self.footer_box, self.snap_box):
            box.toggled.connect(self._paper_changed)
        self.cut_style.currentIndexChanged.connect(self._paper_changed)
        for box in (self.overlap_box, self.inside_box, self.center_box, self.ask_printer_box, self.clear_after_box, self.footer_box):
            layout.addWidget(box)
        for box in (self.center_box, self.ask_printer_box, self.clear_after_box):
            box.toggled.connect(lambda _checked=False: self._remember())
        cut_row = QHBoxLayout()
        cut_row.addWidget(QLabel("Линии отреза:"))
        cut_row.addWidget(self.cut_style, 1)
        layout.addLayout(cut_row)
        layout.addWidget(self.snap_box)
        layout.addStretch(1)
        return page

    def _print_logo_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 6)
        choose = QPushButton("Выбрать логотип")
        choose.clicked.connect(self._choose_logo)
        layout.addWidget(choose)
        self._logo_path = self.settings.logo_path
        self.logo_auto_box = QCheckBox("Автодобавление при размещении фотографий")
        self.logo_auto_box.setChecked(self.settings.logo_auto)
        self.logo_auto_box.toggled.connect(lambda checked: setattr(self.settings, "logo_auto", checked))
        self.logo_auto_box.toggled.connect(lambda _checked=False: self._remember())
        layout.addWidget(self.logo_auto_box)
        place = QGroupBox("Положение на листе")
        place_layout = QVBoxLayout(place)
        self.logo_place = QButtonGroup(page)
        for key, title in (
            ("top-left", "вверху / слева"),
            ("top-center", "вверху / центр"),
            ("top-right", "вверху / справа"),
            ("bottom-left", "внизу / слева"),
            ("bottom-center", "внизу / центр"),
            ("bottom-right", "внизу / справа"),
            ("among", "среди фото (на свободном месте)"),
        ):
            radio = QRadioButton(title)
            radio.setProperty("place", key)
            self.logo_place.addButton(radio)
            place_layout.addWidget(radio)
            if key == (self.settings.logo_place or "among"):
                radio.setChecked(True)
            radio.toggled.connect(lambda checked: self._remember() if checked else None)
        layout.addWidget(place)
        layout.addStretch(1)
        return page

    def _wire_formats(self) -> None:
        self.format_box.currentIndexChanged.connect(self._on_format_picked)
        for box in (self.fmt_width, self.fmt_height, self.fmt_top, self.fmt_bottom):
            box.valueChanged.connect(self._format_fields_edited)
        self.corner.currentIndexChanged.connect(lambda _i: self.refresh())
        self.corner_on.toggled.connect(self._corner_toggled)
        self.corner_radius.valueChanged.connect(self._corner_radius_edited)
        self.ready_gray.toggled.connect(lambda _v: self.refresh())
        self.retouch.toggled.connect(self._apply_retouch)
        self.fmt_head.valueChanged.connect(self._head_edited)
        self._load_format_fields()

    def _on_format_picked(self) -> None:
        self._sync_format_combo_tooltip()
        self._load_format_fields()
        shot = self.current_shot()
        if shot is not None:
            self._apply_saved_ends(shot)
        self.refresh()
        self._remember()

    def _capture_ends(self, shot: Shot) -> None:
        """Запоминает верхнюю и нижнюю лапки в пикселях снимка."""
        fmt = self.current_format()
        crop = self._crop(shot)
        guide = self._shot_guide_ratios(shot, crop)
        angle = shot.angle if self.allow_rotate else 0.0
        points = frame_points(crop, fmt, angle, shot.eye_t, guide)
        shot.crown = points["crown"]
        shot.chin = points["chin"]

    def _apply_saved_ends(self, shot: Shot) -> None:
        """Новый формат строится вокруг уже поставленных лапок."""
        if shot.crown is None or shot.chin is None:
            self._reset_shot_frame(shot)
            return
        fmt = self.current_format()
        eye_t = ends_eye_t(fmt, shot.eye_line)
        left, top, crop_h, angle = pose_from_ends(fmt, shot.crown, shot.chin, self.allow_rotate, eye_t)
        shot.eye_t = eye_t
        shot.angle = angle
        shot.guides_manual = True
        shot.zoom, shot.shift_x, shot.shift_y = zoom_shift_for_box(
            shot.working.size, shot.face, fmt, left, top, crop_h, eye_t
        )
        self._contain(shot)
        self._capture_ends(shot)

    def _reset_shot_frame(self, shot: Shot) -> None:
        """Снова ставит рамку по лицу. Так происходит при другой фотографии и при перезагрузке."""
        fmt = self.current_format()
        shot.angle = 0.0
        detected = (
            detected_eye_t(shot.face, shot.working.size, fmt)
            if shot.face is not None
            else format_eye_t(fmt)
        )
        shot.eye_line = detected
        if fmt.two_points or fmt.face_height_mm <= 0:
            shot.eye_t = format_eye_t(fmt)
        else:
            shot.eye_t = detected
        shot.guides_manual = False
        if shot.face is not None:
            shot.zoom, shot.shift_x, shot.shift_y = initial_frame_fit(
                shot.working.size, shot.face, fmt, shot.eye_t
            )
        else:
            shot.zoom, shot.shift_x, shot.shift_y = 1.0, 0.0, 0.0
        self._contain(shot)
        self._capture_ends(shot)

    def _apply_saved_format(self) -> None:
        if not hasattr(self, "format_box"):
            return
        ident = self.settings.default_format
        if not ident:
            return
        index = self.format_box.findData(ident)
        if index < 0:
            return
        self.format_box.blockSignals(True)
        self.format_box.setCurrentIndex(index)
        self.format_box.blockSignals(False)
        self._load_format_fields()

    def _format_is_pixel(self, base: Format) -> bool:
        return base.kind == 1 and base.file_dpi > 0

    def _field_from_mm(self, mm: float, base: Format) -> float:
        if self._format_is_pixel(base):
            return mm / 25.4 * base.file_dpi
        return mm

    def _mm_from_field(self, value: float, base: Format) -> float:
        if self._format_is_pixel(base):
            return value / base.file_dpi * 25.4
        return value

    def _apply_spin_units(self, base: Format) -> None:
        pixel = self._format_is_pixel(base)
        if pixel:
            limits = (
                (self.fmt_width, 1, 8000),
                (self.fmt_height, 1, 8000),
                (self.fmt_top, 0, 8000),
                (self.fmt_bottom, 0, 8000),
                (self.fmt_head, 1, 8000),
            )
            for spin, low, high in limits:
                spin.setDecimals(0)
                spin.setSingleStep(1)
                spin.setRange(low, high)
            return
        for spin, low, high in (
            (self.fmt_width, 10, 220),
            (self.fmt_height, 10, 260),
            (self.fmt_top, 0, 80),
            (self.fmt_bottom, 0.5, 80),
            (self.fmt_head, 1, 200),
        ):
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setRange(low, high)

    def _load_format_fields(self) -> None:
        base = next(item for item in self.formats if item.id == self.format_box.currentData())
        crown = max(0.0, base.height_mm - base.head_height_mm - base.chin_bottom_mm)
        top_mm = base.overhead2_mm if not base.two_points and base.overhead2_mm > 0 else crown
        self._format_lock = True
        self._apply_spin_units(base)
        show_bottom = base.two_points and not base.have_corner
        self.fmt_bottom.setVisible(show_bottom)
        for w in (self.fmt_width.parentWidget().findChildren(QLabel) if self.fmt_width.parentWidget() else ()):
            if w.text().startswith("Нижн."):
                w.setVisible(show_bottom)
            if not base.two_points and w.text().startswith("Голова"):
                w.setText("Лиц. часть")
            if base.two_points and w.text().startswith("Лиц."):
                w.setText("Голова")
        self.fmt_width.setValue(self._field_from_mm(base.width_mm, base))
        self.fmt_height.setValue(self._field_from_mm(base.height_mm, base))
        self.fmt_top.setValue(self._field_from_mm(top_mm, base))
        self.fmt_bottom.setValue(self._field_from_mm(base.chin_bottom_mm, base))
        if base.two_points:
            self._sync_head()
        else:
            face = base.face_height_mm if base.face_height_mm > 0 else base.head_height_mm * 0.45
            self.fmt_head.blockSignals(True)
            self.fmt_head.setValue(self._field_from_mm(face, base))
            self.fmt_head.blockSignals(False)
        dpi = base.file_dpi if base.kind == 1 or base.max_file_kb else self.settings.export_dpi
        self.dpi_box.setValue(int(dpi or self.settings.export_dpi or EXPORT_DPI))
        self.ready_gray.setChecked(base.colored)
        self.corner_on.setChecked(base.have_corner and base.corner_placement != 0)
        self.corner_radius.setValue(base.corner_rx_mm if base.corner_rx_mm > 0 else 18.0)
        self._sync_corner_controls()
        self._format_lock = False

    def _sync_head(self) -> None:
        head = self.fmt_height.value() - self.fmt_top.value() - self.fmt_bottom.value()
        self.fmt_head.blockSignals(True)
        self.fmt_head.setValue(max(0.5, head))
        self.fmt_head.blockSignals(False)

    def _head_edited(self, _value=None) -> None:
        if self._format_lock:
            return
        base = next(item for item in self.formats if item.id == self.format_box.currentData())
        if not base.two_points:
            shot = self.current_shot()
            if shot is not None:
                self._apply_saved_ends(shot)
            self.refresh()
            return
        bottom = self.fmt_height.value() - self.fmt_top.value() - self.fmt_head.value()
        self.fmt_bottom.blockSignals(True)
        self.fmt_bottom.setValue(max(0.5, bottom))
        self.fmt_bottom.blockSignals(False)
        self.refresh()

    def _format_fields_edited(self, _value=None) -> None:
        if self._format_lock:
            return
        base = next(item for item in self.formats if item.id == self.format_box.currentData())
        if base.two_points:
            self._sync_head()
        shot = self.current_shot()
        if shot is not None:
            self._apply_saved_ends(shot)
        self.refresh()

    def _apply_retouch(self, checked: bool) -> None:
        if checked and self.sharpness.value() < 30:
            koeff = self.settings.retouch_koeff or 0.35
            self.sharpness.setValue(max(30, min(100, round(koeff * 100))))
        self.refresh()

    def _corner_toggled(self, _on: bool) -> None:
        self._sync_corner_controls()
        if self._format_lock:
            return
        self.refresh()

    def _corner_radius_edited(self, _value: float) -> None:
        if self._format_lock:
            return
        self.refresh()

    def _sync_corner_controls(self) -> None:
        on = self.corner_on.isChecked()
        self.corner.setEnabled(on)
        self.corner_radius.setEnabled(on)

    def _wants_gray(self) -> bool:
        return not self.ready_gray.isChecked()

    def _corner_name(self) -> str:
        if not self.corner_on.isChecked():
            return "none"
        return str(self.corner.currentData() or "format")

    def _reload_blocks(self) -> None:
        if not hasattr(self, "block_list"):
            return
        self.block_list.clear()
        if not SCHEMES_DIR.exists():
            return
        for path in sorted(SCHEMES_DIR.glob("*.yaml")):
            self.block_list.addItem(path.stem)

    def _apply_block(self) -> None:
        if not hasattr(self, "block_list"):
            return
        item = self.block_list.currentItem()
        if item is None:
            return
        path = SCHEMES_DIR / f"{item.text()}.yaml"
        if path.exists():
            self.doc = load_scheme(path, self.doc)
            self.pages[self.page_index] = self.doc
            self._refresh_sheet()

    def _delete_block(self) -> None:
        if not hasattr(self, "block_list"):
            return
        item = self.block_list.currentItem()
        if item is None:
            return
        path = SCHEMES_DIR / f"{item.text()}.yaml"
        if path.exists():
            path.unlink()
        self._reload_blocks()

    def _clear_sheet(self) -> None:
        self.doc.items = []
        self.printer.selected = set()
        self._refresh_sheet()

    def _fill_sheet_table(self) -> None:
        if not hasattr(self, "sheet_table"):
            return
        groups: list[tuple[str, int]] = []
        for item in self.doc.items:
            if item.kind != "photo":
                continue
            if any(ready_id == item.ready_id for ready_id, _count in groups):
                continue
            count = sum(1 for other in self.doc.items if other.kind == "photo" and other.ready_id == item.ready_id)
            groups.append((item.ready_id, count))
        self._table_lock = True
        self.sheet_table.setRowCount(len(groups))
        total = 0.0
        for row, (ready_id, count) in enumerate(groups):
            ready = next((ready for ready in self.ready if ready.id == ready_id), None)
            fmt = next((fmt for fmt in self.formats if ready and fmt.id == ready.format_id), None)
            source = Path(ready.source).name if ready else ""
            self.sheet_table.setItem(row, 0, QTableWidgetItem(fmt.name if fmt else source))
            copies = QSpinBox()
            copies.setRange(1, 40)
            copies.setValue(count)
            copies.valueChanged.connect(lambda value, ident=ready_id: self._set_sheet_copies(ident, value))
            self.sheet_table.setCellWidget(row, 1, copies)
            amount = self.settings.price_for(ready.format_id) * count if ready else 0
            total += amount
            price = QTableWidgetItem(f"{amount:.0f}")
            price.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.sheet_table.setItem(row, 2, price)
        self.sheet_total.setText("Итого:" if not groups else f"Итого: {total:.0f}")
        self._table_lock = False

    def _set_sheet_copies(self, ready_id: str, count: int) -> None:
        if getattr(self, "_table_lock", False):
            return
        group = [item for item in self.doc.items if item.kind == "photo" and item.ready_id == ready_id]
        if not group:
            return
        proto = group[0]
        if count < len(group):
            drop = {item.id for item in group[count:]}
            self.doc.items = [item for item in self.doc.items if item.id not in drop]
            self._refresh_sheet()
            return
        extra = []
        for _index in range(count - len(group)):
            self._seq += 1
            extra.append(SheetItem(f"p{self._seq}", "photo", 0, 0, proto.w_mm, proto.h_mm, ready_id, proto.rotation, "", proto.path))
        self._lay_photos(extra)
        self._refresh_sheet()
    def _allow_rotate(self, value: bool) -> None:
        self.allow_rotate = value
        self._remember()
        shot = self.current_shot()
        if shot is not None and not value:
            shot.angle = 0.0
            self.refresh()

    def _all_formats(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        for fmt in self.formats:
            index = self.format_box.findData(fmt.id)
            if index >= 0:
                self.format_box.setCurrentIndex(index)
            self._to_ready(switch=False)
        self._set_mode("ready")

    def _print_from_panel(self) -> None:
        if not self.ready:
            self._to_ready(switch=False)
        if not self.doc.items:
            self._send_to_sheet()
        else:
            self._set_mode("print")

    def _bind_hotkeys(self) -> None:
        for shortcut in self._hotkeys:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._hotkeys = []
        used: set[str] = set()
        for fmt in self.formats:
            key = fmt.hotkey.replace(" ", "")
            if not key or key in used:
                continue
            sequence = QKeySequence(key)
            if sequence.isEmpty():
                continue
            used.add(key)
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda ident=fmt.id: self._pick_format(ident))
            self._hotkeys.append(shortcut)

    def _pick_format(self, ident: str) -> None:
        index = self.format_box.findData(ident)
        if index >= 0:
            self.format_box.setCurrentIndex(index)

    def _menu(self) -> None:
        style = self.style()

        def command(title: str, tip: str, pixmap, slot) -> QAction:
            action = QAction(style.standardIcon(pixmap), title, self)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            action.triggered.connect(slot)
            return action

        pix = style.StandardPixmap
        add_files = command("Добавить фотографии", "Добавить фотографии в базу", pix.SP_DialogOpenButton, self.open_photo)
        add_files.setShortcut(QKeySequence.StandardKey.Open)
        delete_file = command("Удалить фотографию", "Удалить выбранную фотографию", pix.SP_TrashIcon, self._delete_selected_file)
        search = command("Поиск файла", "Найти графический файл по имени", pix.SP_FileDialogContentsView, self._search_file)
        setup = command("Настройки программы", "Настройки программы", pix.SP_FileDialogDetailedView, self._settings)
        formats = command("Справочник форматов", "Редактор форматов: справочник форматов фотографий", pix.SP_FileDialogListView, self._open_format_catalog)
        formats.setIconText("Форматы")
        journal = command("Журнал действий", "Журнал действий", pix.SP_FileDialogInfoView, self._journal)
        about = command("О программе", "Сведения о программе", pix.SP_MessageBoxInformation, self._about_program)
        help_action = command("Справка", "Справка", pix.SP_DialogHelpButton, self._help)
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction(add_files)
        file_menu.addAction(search)
        file_menu.addAction(delete_file)
        file_menu.addSeparator()
        save_photo = QAction("Сохранить фото", self)
        save_photo.setShortcut(QKeySequence("Ctrl+E"))
        save_photo.triggered.connect(self.save_photo)
        save_as = QAction("Сохранить в файл…", self)
        save_as.triggered.connect(self.save_photo_as)
        save_sheet = QAction("Сохранить лист", self)
        save_sheet.setShortcut(QKeySequence("Ctrl+S"))
        save_sheet.triggered.connect(self.save_sheet)
        print_sheet = QAction("Печать", self)
        print_sheet.setShortcut(QKeySequence("Ctrl+P"))
        print_sheet.triggered.connect(self.print_sheet)
        leave = QAction("Выход", self)
        leave.triggered.connect(self.close)
        for action in (save_photo, save_as, save_sheet, print_sheet):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(leave)

        service = self.menuBar().addMenu("Сервис")
        service.addAction(setup)
        service.addAction(formats)
        import_formats = QAction("Импорт форматов из оригинала", self)
        import_formats.triggered.connect(self.import_original_formats)
        import_phs_action = QAction("Импорт настроек PhotoLab…", self)
        import_phs_action.triggered.connect(self.import_photolab_settings)
        service.addAction(import_formats)
        service.addAction(import_phs_action)
        service.addAction(journal)
        service.addSeparator()
        save_scheme_button = QAction("Сохранить схему листа", self)
        save_scheme_button.triggered.connect(self._save_scheme)
        open_scheme_button = QAction("Открыть схему листа", self)
        open_scheme_button.triggered.connect(self._open_scheme)
        service.addAction(save_scheme_button)
        service.addAction(open_scheme_button)

        view = self.menuBar().addMenu("Вид")
        paper = view.addMenu("Окно печати")
        layout_group = QActionGroup(self)
        self.view_vert = QAction("Вертикальная раскладка", self)
        self.view_horz = QAction("Горизонтальная раскладка", self)
        for action, horizontal in ((self.view_vert, False), (self.view_horz, True)):
            action.setCheckable(True)
            layout_group.addAction(action)
            paper.addAction(action)
            action.triggered.connect(lambda _checked=False, flag=horizontal: self._set_paper_layout(flag))
        self.view_vert.setChecked(not self._paper_horizontal)
        self.view_horz.setChecked(self._paper_horizontal)

        windows = self.menuBar().addMenu("Окна")
        histogram = QAction("Гистограмма", self)
        histogram.triggered.connect(self._histogram)
        windows.addAction(histogram)

        help_menu = self.menuBar().addMenu("Помощь")
        help_menu.addAction(about)
        help_menu.addAction(help_action)

        bar = QToolBar("Быстрый доступ", self)
        bar.setMovable(False)
        bar.setIconSize(QSize(24, 24))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(bar)
        for action in (add_files, delete_file, search, setup, formats, journal):
            bar.addAction(action)
        bar.addSeparator()
        self._tunes = [
            {"name": str(item.get("name", "")).strip(), "tone": dict(item["tone"])}
            for item in self.settings.quick_tunes
            if str(item.get("name", "")).strip() and isinstance(item.get("tone"), dict)
        ]
        self.tune_menu = QMenu(self)
        tune_button = QToolButton()
        tune_button.setIcon(style.standardIcon(pix.SP_FileDialogListView))
        tune_button.setToolTip("Сохранённые коррекции")
        tune_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tune_button.setMenu(self.tune_menu)
        tune_button.setAutoRaise(True)
        bar.addWidget(tune_button)
        self._fill_tune_menu()
        bar.addSeparator()
        bar.addAction(about)
        bar.addAction(help_action)
        tool = bar.widgetForAction(formats)
        if tool is not None:
            tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.quick_bar = bar
        if not self.settings.show_quick_bar:
            bar.hide()
        bar.visibilityChanged.connect(lambda _visible: self._remember())

    def _set_mode(self, mode: str) -> None:
        order = {"size": 0, "ready": 1, "print": 2}
        self._mode = mode
        self.step_buttons[mode].setChecked(True)
        self.stack.setCurrentIndex(order[mode])
        if not (self._paper_horizontal and mode == "print"):
            if self.right.indexOf(self.print_panel) < 0:
                self.right.addWidget(self.print_panel)
            self.right.setCurrentIndex(order[mode])
        self._apply_paper_layout()
        if mode == "ready":
            self._ready_layout.insertWidget(0, self.actions_panel)
            self._ready_layout.insertWidget(1, self.format_params)
        elif mode == "size":
            self._sizes_layout.insertWidget(0, self.actions_panel)
            self._sizes_layout.insertWidget(1, self.format_params)
        if mode == "print":
            self._refresh_sheet()
            self.printer.fit()
            self.statusBar().showMessage("Печать: лист слева, параметры листа справа")
        elif mode == "ready":
            self._show_finished()
            self.statusBar().showMessage("Готовая фотография: кадр после обрезки и коррекции")
        else:
            self.refresh()

    def current_shot(self) -> Shot | None:
        if 0 <= self.index < len(self.shots):
            return self.shots[self.index]
        return None

    def current_format(self) -> Format:
        base = next(item for item in self.formats if item.id == self.format_box.currentData())
        width = self._mm_from_field(self.fmt_width.value(), base)
        height = self._mm_from_field(self.fmt_height.value(), base)
        top = self._mm_from_field(self.fmt_top.value(), base)
        bottom = self._mm_from_field(self.fmt_bottom.value(), base)
        if top + bottom >= height:
            bottom = max(0.5, height - top - 1)
        head = height - top - bottom
        if base.two_points:
            return replace(
                base,
                width_mm=width,
                height_mm=height,
                head_height_mm=head,
                chin_bottom_mm=bottom,
                overhead_mm=top,
                corner_rx_mm=float(self.corner_radius.value()),
                corner_ry_mm=float(self.corner_radius.value()),
            )
        face = self._mm_from_field(self.fmt_head.value(), base)
        return replace(
            base,
            width_mm=width,
            height_mm=height,
            head_height_mm=head,
            chin_bottom_mm=bottom,
            overhead2_mm=top,
            face_height_mm=face,
            corner_rx_mm=float(self.corner_radius.value()),
            corner_ry_mm=float(self.corner_radius.value()),
        )

    def _shot_guide_ratios(self, shot: Shot, crop) -> tuple[float, float, float] | None:
        if shot.guides_manual or shot.face is None:
            return None
        return guide_ratios_from_face(crop, shot.face, shot.working.size, self.current_format())

    def _append_shot(self, path: Path) -> None:
        image = open_image(str(path))
        working = downscale(image, PREVIEW_SIDE)
        face = detect_face(working) if self.settings.face_detect else None
        self.shots.append(
            Shot(
                path,
                image,
                working,
                face,
                brightness=self.settings.tone_brightness,
                contrast=self.settings.tone_contrast,
                gamma=self.settings.tone_gamma,
                saturation=self.settings.tone_saturation,
                sharpness=self.settings.tone_sharpness,
                tolerance=self._tolerance_value(),
                replace=bool(self.replace_box.isChecked()),
                eye_t=format_eye_t(self.current_format()),
                guides_manual=False,
            )
        )
        self._reset_shot_frame(self.shots[-1])
        self._remember_shot_file(self.shots[-1])

    def _remember_shot_file(self, shot: Shot) -> None:
        if not shot.path.is_file():
            return
        try:
            stat = shot.path.stat()
        except OSError:
            return
        self._shot_stamps[str(shot.path)] = (stat.st_mtime_ns, stat.st_size)

    def _reload_shot_from_disk(self, shot: Shot) -> None:
        try:
            image = open_image(str(shot.path))
        except OSError:
            return
        shot.image = image
        shot.working = downscale(image, PREVIEW_SIDE)
        shot.face = detect_face(shot.working) if self.settings.face_detect else None
        self._reset_shot_frame(shot)
        self._bg_cache.pop(id(shot), None)
        self._alpha_cache.pop(id(shot), None)
        self._live_cache.pop(id(shot), None)
        self._remember_shot_file(shot)
        if self.current_shot() is shot:
            self._show_preview(shot.path)
            self.refresh()

    def _poll_source_files(self) -> None:
        shot = self.current_shot()
        if shot is None or not shot.path.is_file():
            return
        try:
            stat = shot.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return
        key = str(shot.path)
        previous = self._shot_stamps.get(key)
        if previous is not None and previous != stamp:
            self._reload_shot_from_disk(shot)
            self.statusBar().showMessage(f"С диска подхвачен {shot.path.name}")

    def _on_source_file_changed(self, path: str) -> None:
        if path in self._source_watch.files():
            self._source_watch.addPath(path)
        self._edit_poll.start(400)

    def _file_tools(self):
        specs = (
            ("expand", "Развернуть ветки", self._expand_files),
            ("collapse", "Свернуть ветки", self._collapse_files),
            ("home", "Главная папка базы", self._open_archive_root),
            ("today", "Папка месяца", self._open_month),
            ("explorer", "Открыть папку в проводнике", self._reveal_folder),
            ("name-down", "По имени, по убыванию", lambda: self._sort_files("name", True)),
            ("date-down", "По дате, новые сверху", lambda: self._sort_files("date", True)),
            ("pencil", "Внешний редактор", self._external_editor),
            ("turn-left", "Повернуть против часовой", lambda: self._rotate_shot(-90)),
            ("turn-right", "Повернуть по часовой", lambda: self._rotate_shot(90)),
            ("delete", "Удалить", self._delete_selected_file),
            ("refresh", "Обновить", self._fill_files),
            ("name-up", "По имени, по возрастанию", lambda: self._sort_files("name", False)),
            ("date-up", "По дате, старые сверху", lambda: self._sort_files("date", False)),
        )
        grid = QGridLayout()
        grid.setSpacing(2)
        for index, (kind, tip, handler) in enumerate(specs):
            button = QToolButton()
            button.setObjectName("filetool")
            button.setAutoRaise(False)
            button.setIcon(self._file_icon(kind))
            button.setIconSize(QSize(20, 20))
            button.setFixedSize(26, 26)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            row = 0 if index < 7 else 1
            column = index if index < 7 else index - 7
            if column >= 5:
                column += 1
            grid.addWidget(button, row, column)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        grid.addWidget(line, 0, 5, 2, 1)
        return grid

    def _file_icon(self, kind: str) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        yellow = QColor("#f0c43a")
        edge = QColor("#8d5a12")
        blue = QColor("#2a62c9")
        green = QColor("#2f9d3a")
        red = QColor("#d23b2f")

        def folder(x: int, y: int, w: int = 11, h: int = 8) -> None:
            painter.setPen(QPen(edge, 1))
            painter.setBrush(yellow)
            painter.drawRect(x, y + 2, w, h - 2)
            painter.drawRect(x, y, max(4, w // 2), 3)

        def sheet(x: int, y: int) -> None:
            painter.setPen(QPen(QColor("#8aa0b8"), 1))
            painter.setBrush(QColor("#f7fbff"))
            painter.drawRoundedRect(x, y, 11, 13, 1, 1)

        def triangle(x: int, y: int, up: bool, color: QColor) -> None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if up:
                painter.drawPolygon([QPoint(x, y + 5), QPoint(x + 6, y + 5), QPoint(x + 3, y)])
            else:
                painter.drawPolygon([QPoint(x, y), QPoint(x + 6, y), QPoint(x + 3, y + 5)])

        if kind in {"expand", "collapse"}:
            folder(1, 7)
            folder(4, 2)
            painter.setPen(QPen(blue, 2))
            painter.drawLine(14, 3, 14, 9)
            if kind == "expand":
                painter.drawLine(11, 6, 17, 6)
        elif kind == "home":
            folder(2, 8, 14, 10)
            painter.setPen(QPen(blue, 1))
            painter.setBrush(blue)
            painter.drawPolygon([QPoint(5, 9), QPoint(9, 6), QPoint(13, 9)])
            painter.drawRect(7, 9, 4, 4)
        elif kind == "today":
            folder(1, 8, 14, 10)
            painter.setPen(QPen(blue, 1))
            painter.setBrush(QColor("#eaf2ff"))
            painter.drawRect(5, 6, 8, 7)
            painter.fillRect(5, 6, 8, 2, blue)
            painter.setBrush(red)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(7, 9, 2, 2)
        elif kind == "explorer":
            folder(1, 8, 12, 9)
            painter.setPen(QPen(green, 2))
            painter.drawLine(8, 10, 16, 3)
            painter.drawLine(12, 3, 16, 3)
            painter.drawLine(16, 3, 16, 7)
        elif kind in {"name-down", "name-up", "date-down", "date-up"}:
            sheet(1, 3)
            down = kind.endswith("down")
            if kind.startswith("name"):
                painter.setPen(blue)
                painter.drawText(QRectF(1, 3, 11, 10), Qt.AlignmentFlag.AlignCenter, "A")
            else:
                painter.setPen(QPen(blue, 1))
                painter.setBrush(Qt.GlobalColor.transparent)
                painter.drawEllipse(3, 5, 7, 7)
                painter.drawLine(6, 8, 6, 6)
                painter.drawLine(6, 8, 8, 9)
            triangle(12, 8 if down else 7, not down, green if down else red)
        elif kind == "pencil":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f2d24b"))
            painter.drawPolygon([QPoint(3, 16), QPoint(5, 14), QPoint(7, 16)])
            painter.setBrush(blue)
            painter.drawPolygon([QPoint(5, 14), QPoint(14, 4), QPoint(16, 6), QPoint(7, 16)])
        elif kind in {"turn-left", "turn-right"}:
            painter.setPen(QPen(QColor("#9aa7b5"), 1))
            painter.setBrush(QColor("#eef3f8"))
            painter.drawRoundedRect(2, 5, 12, 10, 1, 1)
            painter.setPen(QPen(blue, 2))
            if kind == "turn-left":
                painter.drawArc(8, 2, 9, 9, 20 * 16, 200 * 16)
                painter.drawLine(8, 6, 8, 3)
                painter.drawLine(8, 6, 11, 6)
            else:
                painter.drawArc(3, 2, 9, 9, 0, 200 * 16)
                painter.drawLine(12, 6, 12, 3)
                painter.drawLine(12, 6, 9, 6)
        elif kind == "delete":
            painter.setPen(QPen(QColor("#9aa7b5"), 1))
            painter.setBrush(QColor("#eef3f8"))
            painter.drawRoundedRect(2, 4, 12, 11, 1, 1)
            painter.setPen(QPen(red, 2))
            painter.drawLine(5, 7, 11, 13)
            painter.drawLine(11, 7, 5, 13)
        elif kind == "save":
            painter.setPen(QPen(QColor("#1d4f89"), 1))
            painter.setBrush(QColor("#d9e6f5"))
            painter.drawRoundedRect(3, 4, 14, 12, 1, 1)
            painter.setBrush(red)
            painter.drawRect(6, 8, 8, 5)
        elif kind == "save-file":
            painter.setPen(QPen(QColor("#8aa0b8"), 1))
            painter.setBrush(QColor("#f7fbff"))
            painter.drawRoundedRect(3, 2, 10, 14, 1, 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e48bb8"))
            painter.drawRoundedRect(8, 8, 9, 6, 1, 1)
        elif kind == "print":
            painter.setPen(QPen(QColor("#5d6b7a"), 1))
            painter.setBrush(QColor("#e8eef5"))
            painter.drawRoundedRect(3, 6, 14, 8, 1, 1)
            painter.drawRect(6, 3, 8, 4)
            painter.setBrush(QColor("#9aa7b5"))
            painter.drawEllipse(5, 13, 3, 3)
            painter.drawEllipse(12, 13, 3, 3)
        elif kind == "refresh":
            painter.setPen(QPen(green, 2))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawArc(4, 3, 12, 12, 40 * 16, 140 * 16)
            painter.drawArc(4, 5, 12, 12, 220 * 16, 140 * 16)
            painter.drawLine(14, 5, 16, 3)
            painter.drawLine(14, 5, 14, 8)
            painter.drawLine(4, 14, 2, 16)
            painter.drawLine(4, 14, 4, 11)
        painter.end()
        return QIcon(pix)

    def _mounts(self) -> list[str]:
        """Корни, которые можно выбрать в списке дисков.

        На Linux это точки монтирования из /proc/mounts. На Windows файла нет,
        а путь «/» Qt считает текущей папкой процесса, поэтому берутся буквы дисков.
        """
        proc = Path("/proc/mounts")
        if proc.exists():
            found = {"/"}
            allowed = {"ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk"}
            try:
                for line in proc.read_text(encoding="utf-8", errors="replace").splitlines():
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mount, fstype = parts[1], parts[2]
                    if fstype in allowed and mount.startswith("/") and "/snap/" not in mount:
                        found.add(mount)
            except OSError:
                pass
            return sorted(found, key=lambda item: (item != "/", item))
        drives = []
        for info in QDir.drives():
            path = info.absoluteFilePath()
            if path:
                drives.append(path)
        return drives or [QDir.rootPath()]

    def _mount_for(self, folder: Path) -> str:
        folder_text = os.path.normcase(os.path.normpath(str(folder)))
        match = self._mounts()[0]
        best = -1
        for mount in self._mounts():
            root = os.path.normcase(os.path.normpath(mount))
            prefix = root if root.endswith(os.sep) else root + os.sep
            if folder_text == root.rstrip(os.sep) or folder_text.startswith(prefix):
                if len(root) > best:
                    best = len(root)
                    match = mount
        return match

    def _left_sizes(self) -> list[int]:
        stored = [int(item) for item in self.settings.left_sizes[:3]]
        if len(stored) == 3 and min(stored) >= 48:
            return stored
        preview = self.settings.preview_height if self.settings.preview_height >= 70 else 150
        return [220, 240, preview]

    def _save_left_sizes(self) -> None:
        if hasattr(self, "left_split"):
            sizes = [int(item) for item in self.left_split.sizes()]
            if len(sizes) == 3 and min(sizes) >= 48:
                self.settings.left_sizes = sizes
        self._capture_workplace()
        save_settings(self.settings)

    def _remember(self) -> None:
        if not getattr(self, "_ui_ready", False):
            return
        timer = getattr(self, "_left_save", None)
        if timer is not None:
            timer.start()

    def _capture_workplace(self) -> None:
        """Забирает с окна то, что оператор включил, чтобы это пережило перезапуск."""
        settings = self.settings
        if hasattr(self, "overlap_box"):
            settings.check_overlap = self.overlap_box.isChecked()
            settings.check_margins = self.inside_box.isChecked()
            settings.align_center = self.center_box.isChecked()
            settings.show_print_dialog = self.ask_printer_box.isChecked()
            settings.clear_after_print = self.clear_after_box.isChecked()
            settings.show_footer = self.footer_box.isChecked()
            settings.snap = self.snap_box.isChecked()
            settings.logo_auto = self.logo_auto_box.isChecked()
            settings.logo_place = self._logo_place_key()
            settings.cut_style = str(self.cut_style.currentData() or settings.cut_style)
            paper = self.paper.currentData()
            if paper:
                settings.default_paper = str(paper)
            settings.default_landscape = self.landscape.isChecked()
            settings.margin_top_mm = float(self.margin_top.value())
            settings.margin_right_mm = float(self.margin_right.value())
            settings.margin_bottom_mm = float(self.margin_bottom.value())
            settings.margin_left_mm = float(self.margin_left.value())
        folder = getattr(self, "folder_root", None)
        if folder is not None and Path(folder).is_dir():
            settings.last_folder = str(Path(folder))
        settings.allow_rotate = bool(self.allow_rotate)
        settings.print_horizontal = bool(self._paper_horizontal)
        if hasattr(self, "format_box"):
            ident = self.format_box.currentData()
            if ident:
                settings.default_format = str(ident)
        if hasattr(self, "quick_bar"):
            settings.show_quick_bar = self.quick_bar.isVisible()
            settings.quick_tunes = [
                {"name": item["name"], "tone": dict(item["tone"])} for item in getattr(self, "_tunes", [])
            ]
        self._persist_tone_defaults()

    def _persist_tone_defaults(self) -> None:
        """Последние значения правой панели — подставляются при следующем запуске."""
        if not hasattr(self, "bright"):
            return
        settings = self.settings
        settings.tone_brightness = float(self.bright.value())
        settings.tone_contrast = float(self.contrast.value())
        settings.tone_gamma = float(self.gamma.value())
        settings.tone_saturation = float(self.saturation.value())
        settings.tone_sharpness = float(self.sharpness.value())
        value = float(self.tolerance.value())
        settings.ground_threshold = value / 10.0 if value > 0 else 0.0
        if hasattr(self, "balance"):
            flat: list[float] = []
            for row in self.balance:
                for box in row:
                    flat.append(float(box.value()))
            settings.tone_balance = flat
        settings.brush_px = int(self.brush.value())
        if hasattr(self, "replace_box"):
            settings.replace_background = self.replace_box.isChecked()
        if hasattr(self, "fill_figure"):
            settings.background_method = "figure" if self.fill_figure.isChecked() else "edge"
        if hasattr(self, "paint_box"):
            settings.paint_background = self.paint_box.isChecked()

    def _set_thumb(self, image: Image.Image) -> None:
        self._thumb_source = pil_to_pixmap(image)
        self._thumb_fitted = None
        self._fit_thumb()

    def _fit_thumb(self) -> None:
        source = self._thumb_source
        if source is None or source.isNull():
            return
        target = self.thumb.size()
        if target.width() < 8 or target.height() < 8:
            return
        fitted = (target.width(), target.height())
        if getattr(self, "_thumb_fitted", None) == fitted:
            return
        self._thumb_fitted = fitted
        self.thumb.setPixmap(source.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            if not self._text_focus():
                self._set_space(kind == QEvent.Type.KeyPress)
                return True
        if watched is getattr(self, "thumb", None) and kind == QEvent.Type.Resize:
            self._fit_thumb()
        files = getattr(self, "files", None)
        if files is not None and watched is files.viewport() and kind == QEvent.Type.Resize:
            self._schedule_thumbs()
        if watched in getattr(self, "_scrollbars", {}) and kind in (QEvent.Type.Paint, QEvent.Type.Resize):
            if kind == QEvent.Type.Resize:
                self._place_overlays(watched)
            pending = getattr(self, "_scroll_pending", None)
            if pending is None:
                self._scroll_pending = pending = set()
            if watched not in pending:
                pending.add(watched)
                QTimer.singleShot(0, lambda target=watched: self._flush_scroll(target))
        return super().eventFilter(watched, event)

    def _flush_scroll(self, canvas: QWidget) -> None:
        self._scroll_pending.discard(canvas)
        self._refresh_scroll(canvas)

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if hasattr(self, "_thumb_timer"):
            self._thumb_arm.stop()
            self._thumb_timer.stop()
        if hasattr(self, "left_split"):
            self._save_left_sizes()
        super().closeEvent(event)

    def _dialog_folder(self) -> str:
        """Стартовая папка диалогов. На Linux пустой путь открывает домашнюю, а не прошлую."""
        folder = self.folder_root if self.folder_root is not None else startup_folder(self.settings)
        return str(folder if Path(folder).is_dir() else Path.home())

    def _fill_drives(self) -> None:
        self._file_order = (self.settings.sort_key, self.settings.sort_reverse)
        self.drive_box.blockSignals(True)
        self.drive_box.clear()
        for mount in self._mounts():
            title = "Корень /" if mount == "/" else str(Path(mount))
            self.drive_box.addItem(title, mount)
        self.drive_box.blockSignals(False)
        self.drive_box.currentIndexChanged.connect(self._drive_changed)
        self._show_folder(startup_folder(self.settings))

    def _drive_changed(self) -> None:
        mount = Path(str(self.drive_box.currentData()))
        self.folders.setRootIndex(self.dir_model.index(str(mount)))
        self.folder_root = mount
        self._watch_open_folder()
        self._fill_files()
        self._remember()

    def _show_folder(self, folder: Path) -> None:
        folder = folder if folder.is_dir() else folder.parent
        mount = self._mount_for(folder)
        index = self.drive_box.findData(mount)
        self.drive_box.blockSignals(True)
        if index >= 0:
            self.drive_box.setCurrentIndex(index)
        self.drive_box.blockSignals(False)
        self.folders.setRootIndex(self.dir_model.index(mount))
        self.folder_root = folder
        self._watch_open_folder()
        self._pending_folder = folder
        self.dir_model.setRootPath(str(folder))
        self._fill_files()
        self._remember()

    def _directory_loaded(self, path: str) -> None:
        pending = self._pending_folder
        if pending is None:
            return
        loaded = Path(path)
        try:
            same = loaded.resolve() == pending.resolve()
            ancestor = pending.resolve().is_relative_to(loaded.resolve())
        except (OSError, ValueError):
            same = loaded == pending
            ancestor = loaded == pending or loaded in pending.parents
        if same or ancestor:
            index = self.dir_model.index(str(loaded))
            self.folders.expand(index)
        if same:
            index = self.dir_model.index(str(pending))
            parent = index.parent()
            while parent.isValid():
                self.folders.expand(parent)
                parent = parent.parent()
            if index.isValid():
                self.folders.setCurrentIndex(index)
                self.folders.scrollTo(index)
            self._pending_folder = None

    def _folder_clicked(self, index) -> None:
        path = Path(self.dir_model.filePath(index))
        if path.is_dir():
            self.folder_root = path
            self._watch_open_folder()
            self._fill_files()
            self._remember()

    def _watch_open_folder(self) -> None:
        if not hasattr(self, "_folder_watch"):
            self._folder_watch = QFileSystemWatcher(self)
            self._folder_watch.directoryChanged.connect(self._folder_changed)
        watched = self._folder_watch.directories()
        if watched:
            self._folder_watch.removePaths(list(watched))
        folder = self.folder_root
        if folder is not None and folder.is_dir():
            self._folder_watch.addPath(str(folder))

    def _folder_changed(self, path: str) -> None:
        if self.folder_root is not None and Path(path) == self.folder_root:
            self._fill_files()

    def _fill_files(self) -> None:
        current = self.files.currentItem()
        selected = str(current.data(0, Qt.ItemDataRole.UserRole)) if current is not None else ""
        self.files.clear()
        self._thumb_token += 1
        self._thumb_items = {}
        self._thumb_queue.clear()
        folder = self.folder_root
        if self._thumb_folder != folder:
            self._thumbs.clear()
            self._thumb_folder = folder
        if folder is None or not folder.is_dir():
            return
        self._reveal_expand = False
        try:
            entries = [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith(".")]
        except OSError:
            self._reveal_expand = True
            return
        try:
            stems = {path.stem for path in entries}
            key_name, reverse = self._file_order
            if key_name == "date":
                entries.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=reverse)
            else:
                entries.sort(key=lambda path: path.name.lower(), reverse=reverse)
            style = self.style()
            pix = style.StandardPixmap
            source_icon = style.standardIcon(pix.SP_FileIcon)
            ready_icon = style.standardIcon(pix.SP_DialogSaveButton)
            printed_icon = style.standardIcon(pix.SP_DialogApplyButton)
            for path in entries:
                node = QTreeWidgetItem([path.name])
                node.setData(0, Qt.ItemDataRole.UserRole, str(path))
                node.setData(0, Qt.ItemDataRole.UserRole + 1, "source")
                self._thumb_items[str(path)] = node
                cached = self._thumb_icon(str(path))
                node.setIcon(0, cached if cached is not None else source_icon)
                saved_rows = read_ready(path, stems)
                self.branches[str(path)] = saved_rows
                for saved in saved_rows:
                    title = saved["name"] + (" · напечатано" if saved.get("printed") else "")
                    child = QTreeWidgetItem([title])
                    child.setData(0, Qt.ItemDataRole.UserRole, saved["path"])
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, "ready")
                    child.setData(0, Qt.ItemDataRole.UserRole + 2, saved["format_id"])
                    self._thumb_items[saved["path"]] = child
                    ready_cached = self._thumb_icon(saved["path"])
                    child.setIcon(0, ready_cached if ready_cached is not None else (printed_icon if saved.get("printed") else ready_icon))
                    node.addChild(child)
                if node.childCount():
                    node.setExpanded(True)
                self.files.addTopLevelItem(node)
                if selected and (str(path) == selected or any(saved["path"] == selected for saved in saved_rows)):
                    chosen = node if str(path) == selected else next(node.child(i) for i in range(node.childCount()) if node.child(i).data(0, Qt.ItemDataRole.UserRole) == selected)
                    self.files.setCurrentItem(chosen)
        finally:
            self._reveal_expand = True
        self._schedule_thumbs()

    def _opened_branch(self, item: QTreeWidgetItem) -> None:
        self._schedule_thumbs()
        if not getattr(self, "_reveal_expand", True):
            return
        QTimer.singleShot(0, lambda target=item: reveal_opened_branch(self.files, target))

    def _schedule_thumbs(self) -> None:
        if hasattr(self, "_thumb_arm"):
            self._thumb_arm.start(40)

    def _arm_thumbs(self) -> None:
        self._thumb_queue = [path for path in self._visible_image_paths() if self._thumb_icon(path) is None]
        if self._thumb_queue and not self._thumb_timer.isActive():
            self._thumb_timer.start(0)

    def _visible_image_paths(self) -> list[str]:
        view = self.files.viewport().rect()
        paths: list[str] = []
        for index in range(self.files.topLevelItemCount()):
            self._consider_thumb(self.files.topLevelItem(index), view, paths)
        if paths:
            return paths
        for index in range(min(20, self.files.topLevelItemCount())):
            raw = str(self.files.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) or "")
            if raw:
                paths.append(raw)
        return paths

    def _consider_thumb(self, item: QTreeWidgetItem, view, paths: list[str]) -> None:
        rect = self.files.visualItemRect(item)
        visible = rect.height() > 0 and rect.intersects(view)
        if visible:
            raw = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            if Path(raw).suffix.lower() in IMAGE_SUFFIXES:
                paths.append(raw)
        if item.isExpanded():
            for index in range(item.childCount()):
                self._consider_thumb(item.child(index), view, paths)

    def _thumb_icon(self, path: str) -> QIcon | None:
        found = self._thumbs.get(path)
        if found is None:
            return None
        mtime, size, icon = found
        try:
            stat = Path(path).stat()
        except OSError:
            self._thumbs.pop(path, None)
            return None
        if stat.st_mtime_ns != mtime or stat.st_size != size:
            self._thumbs.pop(path, None)
            return None
        self._thumbs.pop(path)
        self._thumbs[path] = found
        return icon

    def _remember_thumb(self, path: str, icon: QIcon) -> None:
        try:
            stat = Path(path).stat()
        except OSError:
            return
        self._thumbs.pop(path, None)
        self._thumbs[path] = (stat.st_mtime_ns, stat.st_size, icon)
        while len(self._thumbs) > THUMB_LIMIT:
            self._thumbs.pop(next(iter(self._thumbs)))

    def _read_thumb(self, path: Path) -> QIcon | None:
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            return None
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            if size.width() >= size.height():
                target = QSize(THUMB_SIDE, max(1, round(THUMB_SIDE * size.height() / size.width())))
            else:
                target = QSize(max(1, round(THUMB_SIDE * size.width() / size.height())), THUMB_SIDE)
            reader.setScaledSize(target)
        image = reader.read()
        if image.isNull():
            return None
        plate = QPixmap(THUMB_SIDE, THUMB_SIDE)
        plate.fill(Qt.GlobalColor.transparent)
        painter = QPainter(plate)
        pix = QPixmap.fromImage(image)
        painter.drawPixmap((THUMB_SIDE - pix.width()) // 2, (THUMB_SIDE - pix.height()) // 2, pix)
        painter.end()
        return QIcon(plate)

    def _thumb_step(self) -> None:
        token = self._thumb_token
        if not self._thumb_queue:
            return
        path = self._thumb_queue.pop(0)
        icon = self._thumb_icon(path)
        if icon is None:
            icon = self._read_thumb(Path(path))
            if icon is not None and token == self._thumb_token:
                self._remember_thumb(path, icon)
        if token != self._thumb_token:
            return
        item = self._thumb_items.get(path)
        if item is not None and icon is not None:
            item.setIcon(0, icon)
        if self._thumb_queue:
            self._thumb_timer.start(16)

    def _file_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if raw:
            self._show_preview(Path(str(raw)))

    def _file_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw:
            return
        if kind == "ready":
            parent = item.parent()
            source = Path(str(parent.data(0, Qt.ItemDataRole.UserRole)))
            format_id = str(item.data(0, Qt.ItemDataRole.UserRole + 2))
            if self._mode == "print":
                photo = Path(str(raw))
                parsed = split_ready_name(photo.name)
                picture = photo if parsed is not None and parsed[1] != "jpg" else None
                self._place_saved_format(source, format_id, picture)
            else:
                self._load_source(source)
                index = self.format_box.findData(format_id)
                if index >= 0:
                    self.format_box.setCurrentIndex(index)
                self._set_mode("ready")
            return
        if self._mode == "print":
            return
        self._load_source(Path(str(raw)))

    def _show_preview(self, path: Path) -> None:
        shot = next((item for item in self.shots if item.path == path), None)
        image = shot.working if shot else downscale(open_image(str(path)), 480)
        self._set_thumb(image)

    def _load_source(self, path: Path) -> None:
        for index, shot in enumerate(self.shots):
            if shot.path == path:
                if self.current_shot() is not shot:
                    self._reset_shot_frame(shot)
                self._select_row(index)
                self._mark_tree(path)
                return
        self._append_shot(path)
        self._select_row(len(self.shots) - 1)
        self._mark_tree(path)

    def _mark_tree(self, path: Path) -> None:
        found = self.files.findItems(path.name, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
        if found:
            self.files.setCurrentItem(found[0])
            self.files.scrollToItem(found[0])

    def _expand_files(self) -> None:
        self._reveal_expand = False
        try:
            self.files.expandAll()
        finally:
            self._reveal_expand = True

    def _collapse_files(self) -> None:
        self.files.collapseAll()

    def _open_archive_root(self) -> None:
        root = Path(self.settings.archive_root)
        root.mkdir(parents=True, exist_ok=True)
        self._show_folder(root)

    def _open_month(self) -> None:
        self._show_folder(month_folder(Path(self.settings.archive_root)))

    def _reveal_folder(self) -> None:
        if not self.folder_root:
            return
        if os.name == "nt":
            os.startfile(self.folder_root)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(self.folder_root)])

    def _sort_files(self, key: str, reverse: bool) -> None:
        self._file_order = (key, reverse)
        self._fill_files()

    def _rotate_shot(self, degrees: int) -> None:
        shot = self.current_shot()
        if shot is None:
            item = self.files.currentItem()
            if item is None or item.data(0, Qt.ItemDataRole.UserRole + 1) != "source":
                return
            self._load_source(Path(str(item.data(0, Qt.ItemDataRole.UserRole))))
            shot = self.current_shot()
        if shot is None:
            return
        turn = Image.Transpose.ROTATE_270 if degrees > 0 else Image.Transpose.ROTATE_90
        if abs(degrees) == 180:
            turn = Image.Transpose.ROTATE_180
        shot.image = shot.image.transpose(turn)
        shot.working = downscale(shot.image, PREVIEW_SIDE)
        shot.face = detect_face(shot.working) if self.settings.face_detect else None
        self._reset_shot_frame(shot)
        self._show_preview(shot.path)
        self.refresh()

    def _ready_files(self, source: Path) -> list[Path]:
        folder = ready_folder(source)
        if not folder.is_dir():
            return []
        stems = {path.stem for path in source.parent.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES}
        found = []
        for path in folder.iterdir():
            if path.is_file() and ready_belongs(source, path.name, stems):
                found.append(path)
                info = ready_info_path(path)
                if info.is_file():
                    found.append(info)
        return found

    def _remove_ready_dir(self, folder: Path) -> None:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()

    def _delete_ready_files(self, source: Path) -> None:
        folder = ready_folder(source)
        for path in self._ready_files(source):
            path.unlink(missing_ok=True)
        self._remove_ready_dir(folder)

    def _rename_ready_files(self, source: Path, target: Path) -> None:
        for path in self._ready_files(source):
            tail = path.name[len(source.stem) :]
            path.rename(path.with_name(target.stem + tail))

    def _delete_selected_file(self) -> None:
        chosen = list(self.files.selectedItems())
        if not chosen and self.files.currentItem() is not None:
            chosen = [self.files.currentItem()]
        sources: list[Path] = []
        readies: list[tuple[str, str]] = []
        source_paths: set[str] = set()
        for item in chosen:
            raw = item.data(0, Qt.ItemDataRole.UserRole)
            if not raw:
                continue
            kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if kind == "ready":
                parent = item.parent()
                parent_path = str(parent.data(0, Qt.ItemDataRole.UserRole)) if parent is not None else ""
                readies.append((parent_path, str(raw)))
            else:
                sources.append(Path(str(raw)))
                source_paths.add(str(raw))
        readies = [(parent_path, saved) for parent_path, saved in readies if parent_path not in source_paths]
        if not sources and not readies:
            return
        count = len(sources) + len(readies)
        if self.settings.ask_before_delete:
            if count == 1 and sources:
                question = f"Удалить файл {sources[0].name}?"
            elif count == 1:
                question = "Удалить готовую фотографию?"
            else:
                question = f"Удалить выбранные файлы ({count})?"
            answer = QMessageBox.question(self, "Удалить", question)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            for path in sources:
                path.unlink(missing_ok=True)
                self._delete_ready_files(path)
                self.branches.pop(str(path), None)
            for parent_path, saved_path in readies:
                path = Path(saved_path)
                ready_info_path(path).unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                self._remove_ready_dir(path.parent)
                self.branches[parent_path] = [saved for saved in self.branches.get(parent_path, []) if saved["path"] != saved_path]
        except OSError as error:
            QMessageBox.warning(self, "Удалить", str(error))
            return
        gone = {path for path in sources}
        self.shots = [shot for shot in self.shots if shot.path not in gone]
        if self.index >= len(self.shots):
            self.index = len(self.shots) - 1
        self._fill_files()

    def _delete_branch(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        if parent is None:
            return
        source = str(parent.data(0, Qt.ItemDataRole.UserRole))
        saved_path = str(item.data(0, Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(self, "Удалить", "Удалить готовую фотографию?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = Path(saved_path)
        info = ready_info_path(path)
        try:
            if path.exists():
                path.unlink()
            if info.exists():
                info.unlink()
        except OSError as error:
            QMessageBox.warning(self, "Удалить", str(error))
            return
        self._remove_ready_dir(path.parent)
        self.branches[source] = [saved for saved in self.branches.get(source, []) if saved["path"] != saved_path]
        self._fill_files()

    def _file_menu(self, position) -> None:
        item = self.files.itemAt(position)
        if item is None:
            return
        self.files.setCurrentItem(item)
        menu = QMenu(self)
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if kind == "source":
            listed = [fmt for fmt in self.formats if fmt.show_in_menu]
            if listed:
                formats = menu.addMenu("Формат")
                for fmt in listed:
                    action = formats.addAction(fmt.name)
                    action.triggered.connect(lambda _checked=False, ident=fmt.id, node=item: self._apply_menu_format(node, ident))
            menu.addAction("Загрузить", lambda: self._load_source(Path(str(item.data(0, Qt.ItemDataRole.UserRole)))))
            turn = menu.addMenu("Повернуть")
            turn.addAction("90°", lambda: self._rotate_shot(90))
            turn.addAction("180°", lambda: self._rotate_shot(180))
            turn.addAction("270°", lambda: self._rotate_shot(-90))
            menu.addAction("Внешний редактор", self._external_editor)
            menu.addAction("Переименовать", lambda: self._rename_file(item))
        else:
            menu.addAction("Внешний редактор", lambda: self._edit_saved(item))
            menu.addAction("Изменить атрибут «Напечатан»", lambda: self._toggle_printed(item))
        menu.addAction("Удалить", self._delete_selected_file)
        menu.exec(self.files.viewport().mapToGlobal(position))

    def _apply_menu_format(self, item: QTreeWidgetItem, format_id: str) -> None:
        self._load_source(Path(str(item.data(0, Qt.ItemDataRole.UserRole))))
        index = self.format_box.findData(format_id)
        if index >= 0:
            self.format_box.setCurrentIndex(index)

    def _rename_file(self, item: QTreeWidgetItem) -> None:
        path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
        name, ok = QInputDialog.getText(self, "Переименовать", "Имя файла", text=path.name)
        if not ok or not name.strip() or name.strip() == path.name:
            return
        target = path.with_name(name.strip())
        path.rename(target)
        self._rename_ready_files(path, target)
        for shot in self.shots:
            if shot.path == path:
                shot.path = target
        self.branches.pop(str(path), None)
        self._fill_files()

    def _edit_saved(self, item: QTreeWidgetItem) -> None:
        path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
        editor = self.settings.editor
        if editor and path.exists():
            subprocess.Popen([editor, str(path)])
        else:
            self.statusBar().showMessage("Укажите внешний редактор в настройках")

    def _toggle_printed(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        if parent is None:
            return
        source = str(parent.data(0, Qt.ItemDataRole.UserRole))
        saved_path = str(item.data(0, Qt.ItemDataRole.UserRole))
        photo = Path(saved_path)
        info = load_ready_info(photo)
        info["printed"] = not bool(info.get("printed"))
        info.setdefault("name", photo.stem)
        write_ready_info(photo, info)
        self._fill_files()

    def _ask_place(self) -> tuple[int, bool] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("На лист")
        form = QFormLayout(dialog)
        fmt = self.current_format()
        if not self.settings.ask_place:
            return max(1, fmt.default_count), self.settings.place_horizontal
        copies = QSpinBox()
        copies.setRange(1, 40)
        copies.setValue(max(1, fmt.default_count))
        orient = QComboBox()
        orient.addItem("Вертикально", False)
        orient.addItem("Горизонтально", True)
        orient.setCurrentIndex(1 if self.settings.place_horizontal else 0)
        form.addRow("Копий", copies)
        form.addRow("Ориентация кадра", orient)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return copies.value(), bool(orient.currentData())

    def _original_for_ready(self, photo: Path) -> Path:
        info = load_ready_info(photo)
        named = info.get("source")
        if named:
            candidate = photo.parent.parent / str(named)
            if candidate.is_file():
                return candidate
        parsed = split_ready_name(photo.name)
        if parsed is None or parsed[1] == "jpg":
            return photo
        body = parsed[0]
        folders = [photo.parent.parent]
        nested = photo.parent.parent / photo.parent.parent.name
        if nested.is_dir():
            folders.append(nested)
        for folder in folders:
            if not folder.is_dir():
                continue
            for candidate in folder.iterdir():
                if candidate.is_file() and candidate.stem.lower() == body.lower() and candidate.suffix.lower() in IMAGE_SUFFIXES:
                    return candidate
        return photo

    def _format_millimetres(self, format_id: str) -> tuple[float, float]:
        fmt = next((item for item in self.formats if item.id == format_id), None)
        if fmt is not None:
            return fmt.width_mm, fmt.height_mm
        spec = photolab_format_specs().get(format_id.lower())
        if spec is not None:
            return spec[1], spec[2]
        current = self.current_format()
        return current.width_mm, current.height_mm

    def _drop_ready(self, path: str, format_id: str) -> None:
        photo = Path(path)
        source = self._original_for_ready(photo)
        parsed = split_ready_name(photo.name)
        picture = photo if parsed is not None and parsed[1] != "jpg" else None
        self._place_saved_format(source, format_id, picture)

    def _place_saved_format(self, source: Path, format_id: str, picture: Path | None = None) -> None:
        asked = self._ask_place()
        if asked is None:
            return
        copies, horizontal = asked
        self._load_source(source)
        index = self.format_box.findData(format_id)
        if index >= 0:
            self.format_box.setCurrentIndex(index)
        width, height = self._format_millimetres(format_id)
        rotation = 0
        if horizontal:
            width, height = height, width
            rotation = 90
        ready = self._same_sheet_photo(str(source), format_id, rotation)
        if ready is None:
            self._seq += 1
            ready = ReadyItem(
                f"r{self._seq}",
                str(source),
                format_id,
                copies,
                self._corner_name(),
                self._wants_gray(),
                float(self.corner_radius.value()),
            )
            self.ready.append(ready)
        else:
            ready.copies += copies
            ready.corner = self._corner_name()
            ready.corner_mm = float(self.corner_radius.value())
        shown = str(picture) if picture is not None else str(source)
        fresh = []
        for _copy in range(copies):
            self._seq += 1
            fresh.append(SheetItem(f"p{self._seq}", "photo", 0, 0, width, height, ready.id, rotation, "", shown))
        self._lay_photos(fresh)
        self._set_mode("print")

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= len(self.shots):
            return
        self.index = row
        shot = self.shots[row]
        self._load_tone(shot)
        found = "лицо найдено" if shot.face else "лицо не найдено, двигайте рамку"
        self._set_thumb(shot.working)
        self.statusBar().showMessage(f"{shot.path.name} · {found} · Shift и два щелчка — линейка")
        self.refresh()

    def open_photo(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Открыть снимок",
            self._dialog_folder(),
            "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)",
        )
        if path:
            self.open_paths([Path(path)], reset_job=False)

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Открыть папку", self._dialog_folder())
        if not folder:
            return
        self.folder_root = Path(folder)
        self._show_folder(self.folder_root)

    def open_paths(self, paths: list[Path], reset_job: bool) -> None:
        if reset_job:
            self.ready = []
            self.doc = self._fresh_doc()
        self.shots = []
        self._bg_cache.clear()
        self._alpha_cache.clear()
        self._live_cache.clear()
        self._sheet_pixmaps = {}
        for path in paths:
            self._append_shot(path)
        if paths:
            self._show_folder(paths[0].parent)
        if self.shots:
            self._select_row(0)
            self._mark_tree(self.shots[0].path)

    def _tone_line(self, title: str, slider: QSlider, neutral: int | None) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QLabel(title))
        row = QHBoxLayout()
        row.setSpacing(6)
        number = QSpinBox()
        number.setRange(slider.minimum(), slider.maximum())
        number.setValue(slider.value())
        number.setFixedWidth(self._spin_width("-100"))
        number.setAlignment(Qt.AlignmentFlag.AlignRight)
        if not hasattr(self, "_tone_numbers"):
            self._tone_numbers = []
        self._tone_numbers.append((slider, number))

        def from_slider(value: int) -> None:
            number.blockSignals(True)
            number.setValue(value)
            number.blockSignals(False)

        slider.valueChanged.connect(from_slider)
        number.valueChanged.connect(slider.setValue)
        row.addWidget(slider, 1)
        row.addWidget(number)
        if neutral is not None:
            reset = QToolButton()
            reset.setText("×")
            reset.setFixedSize(22, 22)
            reset.setToolTip("Сбросить")
            reset.clicked.connect(lambda: slider.setValue(neutral))
            row.addWidget(reset)
        layout.addLayout(row)
        return host

    def _color_axis(self, channel: int, title: str, left: str, right: str) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QLabel(title))
        row = QHBoxLayout()
        slider = UnitSlider(Qt.Orientation.Horizontal)
        slider.setRange(-100, 100)
        slider.setSingleStep(1)
        self._paint_slider(
            slider,
            f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {left}, stop:1 {right})",
        )
        percent = QSpinBox()
        percent.setRange(-100, 100)
        percent.setSuffix(" %")
        percent.setFixedWidth(self._spin_width("-100 %"))
        percent.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider.valueChanged.connect(lambda value, index=channel: self._color_slider_moved(index, value))
        percent.valueChanged.connect(lambda value, index=channel: self._color_spin_edited(index, value))
        row.addWidget(slider, 1)
        row.addWidget(percent)
        layout.addLayout(row)
        self.color_sliders.append(slider)
        self.color_percents.append(percent)
        return host

    def _active_balance_band(self) -> int:
        band = self.balance_band.checkedId()
        return 1 if band < 0 else band

    def _color_slider_moved(self, channel: int, value: int) -> None:
        percent = self.color_percents[channel]
        percent.blockSignals(True)
        percent.setValue(value)
        percent.blockSignals(False)
        self._write_balance_channel(channel, value)

    def _color_spin_edited(self, channel: int, value: int) -> None:
        slider = self.color_sliders[channel]
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        self._write_balance_channel(channel, value)

    def _write_balance_channel(self, channel: int, shown: int) -> None:
        box = self.balance[self._active_balance_band()][channel]
        box.blockSignals(True)
        box.setValue(-int(shown))
        box.blockSignals(False)
        self._store_tone()

    def _sync_tone_numbers(self) -> None:
        for slider, number in getattr(self, "_tone_numbers", []):
            number.blockSignals(True)
            number.setValue(slider.value())
            number.blockSignals(False)

    def _sync_color_sliders(self) -> None:
        if not hasattr(self, "color_sliders"):
            return
        band = self._active_balance_band()
        for channel, slider in enumerate(self.color_sliders):
            shown = -int(self.balance[band][channel].value())
            slider.blockSignals(True)
            slider.setValue(shown)
            slider.blockSignals(False)
            percent = self.color_percents[channel]
            percent.blockSignals(True)
            percent.setValue(shown)
            percent.blockSignals(False)

    def _reset_balance(self) -> None:
        for row in self.balance:
            for box in row:
                box.setValue(0)
        self._sync_color_sliders()
        self._store_tone()

    def _pick_background(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if not color.isValid():
            return
        value = color.name().upper()
        index = self.color_box.findData(value)
        if index < 0:
            self.color_box.addItem(value, value)
            index = self.color_box.findData(value)
        self.color_box.setCurrentIndex(index)

    def _sharpness_picked(self) -> None:
        self.sharpness.setValue(int(self.sharpness_box.currentData()))

    def _sync_sharpness_box(self) -> None:
        if not hasattr(self, "sharpness_box"):
            return
        value = self.sharpness.value()
        best = 0
        gap = 10**9
        for index in range(self.sharpness_box.count()):
            distance = abs(int(self.sharpness_box.itemData(index)) - value)
            if distance < gap:
                best, gap = index, distance
        self.sharpness_box.blockSignals(True)
        self.sharpness_box.setCurrentIndex(best)
        self.sharpness_box.blockSignals(False)

    def _store_tone(self, _value=None) -> None:
        self._persist_tone_defaults()
        shot = self.current_shot()
        if shot is not None:
            shot.brightness = float(self.bright.value())
            shot.contrast = float(self.contrast.value())
            shot.gamma = float(self.gamma.value())
            shot.saturation = float(self.saturation.value())
            shot.sharpness = float(self.sharpness.value())
            shot.black_point = float(self.black.value())
            shot.white_point = float(self.white.value())
            shot.tolerance = float(self.tolerance.value())
            shot.replace = self.replace_box.isChecked()
            self._schedule_preview()
        self._fill_tune_menu()
        self._remember()

    def _load_tone(self, shot: Shot) -> None:
        for slider, value in (
            (self.bright, shot.brightness),
            (self.contrast, shot.contrast),
            (self.gamma, shot.gamma),
            (self.saturation, shot.saturation),
            (self.sharpness, shot.sharpness),
            (self.black, shot.black_point),
            (self.white, shot.white_point),
            (self.tolerance, shot.tolerance),
        ):
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
        self.replace_box.blockSignals(True)
        self.replace_box.setChecked(shot.replace)
        self.replace_box.blockSignals(False)
        self._sync_sharpness_box()
        self._sync_tone_numbers()

    def _balance(self) -> tuple:
        return tuple(tuple(float(box.value()) for box in row) for row in self.balance)

    def _clothes_image(self, shot: Shot) -> Image.Image | None:
        for name, image in self.clothes:
            if name == shot.clothes:
                return image
        return None

    def _crop(self, shot: Shot):
        return place_crop(
            shot.working.size,
            shot.face,
            self.current_format(),
            shot.zoom,
            shot.shift_x,
            shot.shift_y,
            eye_t=shot.eye_t,
        )

    def _background(self) -> str:
        chosen = self.color_box.currentData()
        return self.current_format().background if chosen == "format" else str(chosen)

    def _background_method(self) -> str:
        if getattr(self, "fill_figure", None) is not None and self.fill_figure.isChecked():
            return "figure"
        return "edge"

    def _paint_background(self) -> bool:
        box = getattr(self, "paint_box", None)
        return bool(box is not None and box.isChecked())

    def _schedule_preview(self) -> None:
        """Ползунок не ждёт пересчёт: кадр догоняет уже с последним значением."""
        timer = getattr(self, "_preview_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._preview_due)
            self._preview_timer = timer
            smooth = QTimer(self)
            smooth.setSingleShot(True)
            smooth.timeout.connect(self.finished.reveal_smooth)
            self._smooth_timer = smooth
        if not timer.isActive():
            timer.start(0)

    def _preview_due(self) -> None:
        if self._mode != "ready":
            return
        self._show_finished(smooth=False)
        self._smooth_timer.start(60)

    def _background_alpha(self, shot: Shot) -> np.ndarray | None:
        """Маска фона по превью. Цвет заливки и ползунки её не пересчитывают."""
        if not shot.replace or shot.tolerance <= 0:
            return None
        face = shot.face
        face_key = None if face is None else (round(face.x, 3), round(face.y, 3), round(face.w, 3), round(face.h, 3))
        method = self._background_method()
        key = (id(shot.working), round(float(shot.tolerance)), int(self.settings.brush_px), face_key, method)
        cached = self._bg_cache.get(id(shot))
        if cached is not None and cached[0] == key:
            return cached[1]
        alpha = background_alpha(shot.working, shot.tolerance, self.settings.brush_px, face, method)
        self._bg_cache[id(shot)] = (key, alpha)
        return alpha

    def _render(self, shot: Shot, image: Image.Image, dpi: int, corner: str = "none", grayscale: bool = False, prepared: bool = False, fmt: Format | None = None) -> Image.Image:
        fmt = self.current_format() if fmt is None else fmt
        crop = place_crop(
            shot.working.size, shot.face, fmt, shot.zoom, shot.shift_x, shot.shift_y, eye_t=shot.eye_t
        )
        scale_x = image.width / shot.working.width
        scale_y = image.height / shot.working.height
        crop = type(crop)(crop.left * scale_x, crop.top * scale_y, crop.width * scale_x, crop.height * scale_y)
        return render_photo(
            image,
            crop,
            fmt,
            dpi,
            replace=shot.replace and not prepared,
            tolerance=shot.tolerance,
            brightness=shot.brightness,
            contrast=shot.contrast,
            grayscale=grayscale,
            background=self._background(),
            gamma=shot.gamma,
            saturation=shot.saturation,
            sharpness=shot.sharpness,
            black_point=shot.black_point,
            white_point=shot.white_point,
            balance=self._balance(),
            corner=corner,
            clothes=self._clothes_image(shot),
            angle=shot.angle,
            brush=self.settings.brush_px,
            resample=Image.Resampling.BILINEAR if prepared else Image.Resampling.BICUBIC,
            face=shot.face,
            method=self._background_method(),
            paint=self._paint_background(),
        )

    def _show_head_share(self, shot: Shot | None, crop, guide) -> None:
        """Процент кадра между верхней и нижней лапкой и та же длина в миллиметрах формата."""
        label = getattr(self, "head_share", None)
        if label is None:
            return
        if shot is None or crop is None or crop.height < 1:
            label.setText("Голова —")
            return
        percent, mm = head_share(
            crop,
            self.current_format(),
            shot.angle if self.allow_rotate else 0.0,
            shot.eye_t,
            guide,
        )
        label.setText(f"Голова {percent:.0f}% кадра · {mm:.0f} мм")

    def refresh(self) -> None:
        self._update_size_preview()
        shot = self.current_shot()
        if shot is None:
            self._show_head_share(None, None, None)
            return
        crop = self._crop(shot)
        guide = self._shot_guide_ratios(shot, crop)
        self._show_head_share(shot, crop, guide)
        self.source.set_frame(
            pil_to_pixmap(shot.working),
            crop,
            self.current_format(),
            shot.angle if self.allow_rotate else 0.0,
            shot.eye_t,
            guide,
        )
        if self._mode == "ready":
            self._show_finished()

    def _update_size_preview(self) -> None:
        if not hasattr(self, "format_preview"):
            return
        shot = self.current_shot()
        if shot is None:
            self.format_preview.setPixmap(QPixmap())
            self._fill_copies(None)
            return
        fmt = self.current_format()
        crop = self._crop(shot)
        box = (
            max(0, int(crop.left)),
            max(0, int(crop.top)),
            min(shot.working.width, int(crop.left + crop.width)),
            min(shot.working.height, int(crop.top + crop.height)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            self.format_preview.setPixmap(QPixmap())
            self._fill_copies(None)
            return
        pix = pil_to_pixmap(shot.working.crop(box)).scaled(
            72,
            96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        guide = QPixmap(pix)
        painter = QPainter(guide)
        if fmt.height_mm > 0:
            y = int(pix.height() * (float(self.fmt_top.value()) / fmt.height_mm))
            painter.setPen(QPen(QColor("#d02020"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(2, y, max(2, pix.width() - 2), y)
        painter.end()
        self.format_preview.setPixmap(guide)
        self._fill_copies(guide)

    def _fill_copies(self, pix: QPixmap | None) -> None:
        while self.copy_row.count():
            item = self.copy_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if pix is None or pix.isNull():
            return
        fmt = self.current_format()
        count = max(1, fmt.default_count)
        width, height = paper_mm(self.doc)
        plan = plan_sheet(fmt, Paper("sheet", "", width, height), self.doc.margin_mm, self.doc.gap_mm)
        if plan.fits:
            count = max(count, plan.count)
        count = min(12, count)
        thumb = pix.scaled(28, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for _ in range(count):
            label = QLabel()
            label.setPixmap(thumb)
            self.copy_row.addWidget(label)
        self.copy_row.addStretch(1)

    def _live_pixel_size(self) -> tuple[int, int]:
        """Размер кадра на экране. Длинная сторона не больше 720, чтобы коррекция успевала за ползунком."""
        fmt = self.current_format()
        avail_w = max(1, self.finished.width() - 48)
        avail_h = max(1, self.finished.height() - 48)
        if avail_w < 80 or avail_h < 80:
            avail_w, avail_h = 420, 560
        aspect = fmt.width_mm / max(fmt.height_mm, 0.01)
        if avail_w / avail_h > aspect:
            height = avail_h
            width = max(1, int(round(height * aspect)))
        else:
            width = avail_w
            height = max(1, int(round(width / aspect)))
        long_side = max(width, height)
        if long_side > 720:
            scale = 720 / long_side
            width = max(1, int(round(width * scale)))
            height = max(1, int(round(height * scale)))
        return width, height

    def _live_base(self, shot: Shot) -> np.ndarray:
        """Обрезанный кадр до коррекции. Пока едут ползунки, обрезка не повторяется."""
        size = self._live_pixel_size()
        crop = self._crop(shot)
        angle = shot.angle if self.allow_rotate else 0.0
        key = (
            id(shot.working),
            round(crop.left, 2),
            round(crop.top, 2),
            round(crop.width, 2),
            round(crop.height, 2),
            round(float(angle), 2),
            size,
        )
        cached = self._live_cache.get(id(shot))
        if cached is not None and cached[0] == key:
            return cached[1]
        scaled = self._scaled_crop(shot, crop, shot.working.size)
        image = render_crop(shot.working, scaled, size, hex_to_rgb(self._background()), angle, Image.Resampling.BILINEAR)
        array = np.ascontiguousarray(np.asarray(image.convert("RGB")))
        self._live_cache[id(shot)] = (key, array)
        return array

    def _scaled_crop(self, shot: Shot, crop, size: tuple[int, int]):
        scale_x = size[0] / max(shot.working.width, 1)
        scale_y = size[1] / max(shot.working.height, 1)
        return type(crop)(crop.left * scale_x, crop.top * scale_y, crop.width * scale_x, crop.height * scale_y)

    def _live_alpha(self, shot: Shot) -> np.ndarray | None:
        """Та же обрезка, что у кадра, но для маски фона. Снаружи снимка — полная заливка."""
        alpha = self._background_alpha(shot)
        if alpha is None:
            return None
        size = self._live_pixel_size()
        crop = self._crop(shot)
        angle = shot.angle if self.allow_rotate else 0.0
        key = (
            id(shot.working),
            round(float(shot.tolerance)),
            int(self.settings.brush_px),
            self._background_method(),
            round(crop.left, 2),
            round(crop.top, 2),
            round(crop.width, 2),
            round(crop.height, 2),
            round(float(angle), 2),
            size,
        )
        cached = self._alpha_cache.get(id(shot))
        if cached is not None and cached[0] == key:
            return cached[1]
        mask = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), "L")
        cropped = render_crop(mask, self._scaled_crop(shot, crop, mask.size), size, 255, angle, Image.Resampling.BILINEAR)
        array = np.asarray(cropped).astype(np.float32) / 255.0
        self._alpha_cache[id(shot)] = (key, array)
        return array

    def _show_finished(self, smooth: bool = True) -> None:
        shot = self.current_shot()
        if shot is None:
            self._finished_image = None
            self.finished.set_photo(QPixmap(), smooth)
            return
        base = self._live_base(shot)
        preview = Image.fromarray(base, "RGB")
        image = render_photo(
            preview,
            Rect(0, 0, preview.width, preview.height),
            self.current_format(),
            72,
            replace=shot.replace,
            tolerance=shot.tolerance,
            brightness=shot.brightness,
            contrast=shot.contrast,
            grayscale=self._wants_gray(),
            background=self._background(),
            gamma=shot.gamma,
            saturation=shot.saturation,
            sharpness=shot.sharpness,
            black_point=shot.black_point,
            white_point=shot.white_point,
            balance=self._balance(),
            corner=self._corner_name(),
            clothes=self._clothes_image(shot),
            prepared_alpha=self._live_alpha(shot),
            already_cropped=True,
            method=self._background_method(),
            paint=self._paint_background(),
        )
        self._finished_image = image
        self.finished.set_photo(pil_to_pixmap(image), smooth)

    def _preview_source(self, shot: Shot) -> Image.Image:
        """Исходник, уменьшенный так, чтобы кадр на экране остался около 2000 пикселей по длинной стороне."""
        crop = self._crop(shot)
        full_scale = shot.image.width / max(shot.working.width, 1)
        source_long = max(crop.width, crop.height) * full_scale
        if source_long <= 2000 or source_long <= 1:
            return shot.image
        factor = 2000 / source_long
        return shot.image.resize(
            (max(1, int(shot.image.width * factor)), max(1, int(shot.image.height * factor))),
            Image.Resampling.LANCZOS,
        )

    def _preview_dpi(self, shot: Shot) -> int:
        fmt = self.current_format()
        crop = self._crop(shot)
        full_scale = shot.image.width / max(shot.working.width, 1)
        source_long = max(crop.width, crop.height) * full_scale
        target = max(1.0, min(source_long, 2000))
        long_mm = max(fmt.width_mm, fmt.height_mm, 1)
        return max(72, int(round(target * 25.4 / long_mm)))

    def _contain(self, shot: Shot) -> None:
        fmt = self.current_format()
        crop = self._crop(shot)
        guide = self._shot_guide_ratios(shot, crop)
        shot.shift_x, shot.shift_y = contain_guides(
            shot.working.size,
            shot.face,
            fmt,
            shot.zoom,
            shot.shift_x,
            shot.shift_y,
            shot.angle if self.allow_rotate else 0.0,
            shot.eye_t,
            guide,
        )

    def _pan(self, dx: float, dy: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.shift_x += dx
        shot.shift_y += dy
        self._contain(shot)
        self._capture_ends(shot)
        self.refresh()

    def _zoom(self, factor: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.zoom = max(ZOOM_MIN, min(ZOOM_MAX, shot.zoom * factor))
        self._capture_ends(shot)
        self.refresh()

    def _nudge_guide(self, name: str, dx: float, dy: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        fmt = self.current_format()
        angle_now = shot.angle if self.allow_rotate else 0.0
        crop = self._crop(shot)
        guide = self._shot_guide_ratios(shot, crop)
        points = frame_points(crop, fmt, angle_now, shot.eye_t, guide)
        shot.guides_manual = True
        crown = points["crown"]
        chin = points["chin"]
        if name == "eyes":
            proposal = (points["eyes"][0] + dx, points["eyes"][1] + dy)
            _, _, shot.eye_t = move_middle(crown, chin, proposal, self.allow_rotate)
        elif name == "crown":
            crown = (crown[0] + dx, crown[1] + dy)
            if not self.allow_rotate:
                chin = (crown[0], chin[1])
        else:
            chin = (chin[0] + dx, chin[1] + dy)
            if not self.allow_rotate:
                crown = (chin[0], crown[1])
        left, top, crop_h, angle = pose_from_ends(fmt, crown, chin, self.allow_rotate, shot.eye_t)
        shot.zoom, shot.shift_x, shot.shift_y = zoom_shift_for_box(
            shot.working.size, shot.face, fmt, left, top, crop_h, shot.eye_t
        )
        shot.angle = angle
        if not fmt.two_points and fmt.face_height_mm > 0:
            shot.eye_line = shot.eye_t
        self._contain(shot)
        self._capture_ends(shot)
        self.refresh()

    def reset_frame(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        self._reset_shot_frame(shot)
        shot.brightness = shot.contrast = shot.gamma = shot.saturation = shot.sharpness = 0
        shot.black_point, shot.white_point = 0, 255
        self._load_tone(shot)
        self.refresh()

    def _to_ready(self, switch: bool = True) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        self._seq += 1
        self.ready.append(
            ReadyItem(
                f"r{self._seq}",
                str(shot.path),
                self.current_format().id,
                1,
                self._corner_name(),
                self._wants_gray(),
                float(self.corner_radius.value()),
            )
        )
        self._fill_ready()
        if switch:
            self._set_mode("ready")

    def _current_ready(self) -> ReadyItem | None:
        return self.ready[-1] if self.ready else None

    def _fill_ready(self) -> None:
        return

    def _ready_copies(self, value: int) -> None:
        item = self._current_ready()
        if item:
            item.copies = value
            self._fill_ready()

    def _ready_corner(self) -> None:
        item = self._current_ready()
        if item:
            item.corner = self._corner_name()
            item.corner_mm = float(self.corner_radius.value())

    def _ready_gray_changed(self, checked: bool) -> None:
        item = self._current_ready()
        if item:
            item.grayscale = not checked

    def _remove_ready(self) -> None:
        if self.ready:
            self.ready.pop()

    def _shot_for(self, source: str) -> Shot | None:
        return next((shot for shot in self.shots if str(shot.path) == source), None)

    def _saved_sheet_image(self, item: SheetItem, max_side: int | None = None) -> Image.Image | None:
        """Готовый файл PhotoLab уже является кадром. Свой jpg по-прежнему собирается из исходника."""
        path = Path(item.path)
        if not path.is_file():
            return None
        parsed = split_ready_name(path.name)
        if parsed is None or parsed[1] == "jpg":
            return None
        try:
            image = open_image(str(path))
        except OSError:
            return None
        return downscale(image, max_side) if max_side is not None else image

    def _render_ready(self, item: ReadyItem, dpi: int, preview: bool = False) -> Image.Image | None:
        shot = self._shot_for(item.source)
        fmt = next((fmt for fmt in self.formats if fmt.id == item.format_id), None)
        if shot is None or fmt is None:
            return None
        previous = self.format_box.currentIndex()
        target = self.format_box.findData(fmt.id)
        if target >= 0:
            self.format_box.blockSignals(True)
            self.format_box.setCurrentIndex(target)
            self.format_box.blockSignals(False)
        source = self._preview_source(shot) if preview else (shot.working if dpi < 200 else shot.image)
        used_dpi = self._preview_dpi(shot) if preview else dpi
        image = self._render(shot, source, used_dpi, item.corner, item.grayscale)
        self.format_box.blockSignals(True)
        self.format_box.setCurrentIndex(previous)
        self.format_box.blockSignals(False)
        return image

    def _send_to_sheet(self) -> None:
        photos: list[SheetItem] = []
        for item in self.ready:
            fmt = next((fmt for fmt in self.formats if fmt.id == item.format_id), None)
            if fmt is None:
                continue
            for copy in range(item.copies):
                self._seq += 1
                photos.append(SheetItem(f"p{self._seq}", "photo", 0, 0, fmt.width_mm, fmt.height_mm, item.id, 0, "", item.source))
                _ = copy
        self._lay_photos(photos)
        self._set_mode("print")

    def _fresh_doc(self) -> SheetDoc:
        settings = self.settings
        style = settings.cut_style or "corners"
        return SheetDoc(
            paper_id=settings.default_paper or "10x15",
            landscape=settings.default_landscape,
            margin_mm=settings.margin_left_mm,
            margin_top_mm=settings.margin_top_mm,
            margin_right_mm=settings.margin_right_mm,
            margin_bottom_mm=settings.margin_bottom_mm,
            margin_left_mm=settings.margin_left_mm,
            cut_style=style,
            cut_length_mm=settings.cut_length_mm or 3,
            cut_marks=style != "none",
            snap=settings.snap,
        )

    def _blank_page(self) -> SheetDoc:
        current = self.doc
        return SheetDoc(
            current.paper_id,
            current.custom_w,
            current.custom_h,
            current.landscape,
            current.margin_mm,
            current.gap_mm,
            current.snap,
            current.grid,
            current.grid_mm,
            current.cut_marks,
            margin_top_mm=current.margin_top_mm,
            margin_right_mm=current.margin_right_mm,
            margin_bottom_mm=current.margin_bottom_mm,
            margin_left_mm=current.margin_left_mm,
            cut_style=current.cut_style,
            cut_length_mm=current.cut_length_mm,
        )

    def _sheet_occupied(self, doc: SheetDoc) -> bool:
        return any(item.kind in ("photo", "logo") for item in doc.items)

    def _sync_page_nav(self) -> None:
        if not hasattr(self, "page_no"):
            return
        last = self.doc if self.page_index == len(self.pages) - 1 else self.pages[-1]
        self.page_no.setText(str(self.page_index + 1))
        self.page_no.setToolTip(f"Лист {self.page_index + 1} из {len(self.pages)}")
        self.page_prev.setEnabled(self.page_index > 0)
        can_forward = self.page_index < len(self.pages) - 1 or (self._sheet_occupied(last) and len(self.pages) < 30)
        self.page_next.setEnabled(can_forward)

    def _show_page(self, index: int) -> None:
        self.pages[self.page_index] = self.doc
        if index == len(self.pages):
            if not self._sheet_occupied(self.pages[-1]):
                return
            if len(self.pages) >= 30:
                self.statusBar().showMessage("Достигнут предел количества печатных листов.")
                return
            self.pages.append(self._blank_page())
        if index < 0 or index >= len(self.pages):
            return
        self.page_index = index
        self.doc = self.pages[index]
        self.printer.selected = set()
        self._refresh_sheet()

    def _same_sheet_photo(self, source: str, format_id: str, rotation: int) -> ReadyItem | None:
        """Та же фотография, тот же формат и та же ориентация уже лежат на листах."""
        turn = rotation % 180
        for ready in self.ready:
            if ready.source != source or ready.format_id != format_id:
                continue
            for page in self.pages:
                if any(item.kind == "photo" and item.ready_id == ready.id and item.rotation % 180 == turn for item in page.items):
                    return ready
        return None

    def _lay_photos(self, photos: list[SheetItem]) -> None:
        """Ставит новые кадры в свободные места. Уже стоящие не двигает."""
        queue = photos
        while queue:
            placed, rest = append_photos(self.doc, queue)
            self.doc.items = placed
            if self.settings.align_center:
                center_photos(self.doc)
            self._maybe_auto_logo()
            self.pages[self.page_index] = self.doc
            if not rest:
                break
            if photo_too_big(self.doc, rest[0]):
                QMessageBox.warning(self, "Лист", "Фотография не умещается на листе выбранного формата!")
                break
            answer = QMessageBox.question(
                self,
                "Лист",
                "Фотография не умещается на лист! Разместить её на следующем листе?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                break
            if not self._sheet_occupied(self.pages[-1]):
                self.page_index = len(self.pages) - 1
                self.doc = self.pages[self.page_index]
            else:
                if len(self.pages) >= 30:
                    QMessageBox.warning(self, "Лист", "Достигнут предел количества печатных листов.")
                    break
                self.pages.append(self._blank_page())
                self.page_index = len(self.pages) - 1
                self.doc = self.pages[self.page_index]
            queue = rest
        self._sync_page_nav()

    def _paper_changed(self, _value=None) -> None:
        previous_size = paper_mm(self.doc)
        photos = any(item.kind == "photo" for item in self.doc.items)
        if photos and self.landscape.isChecked() != self.doc.landscape:
            self.landscape.blockSignals(True)
            self.portrait.blockSignals(True)
            self.landscape.setChecked(self.doc.landscape)
            self.portrait.setChecked(not self.doc.landscape)
            self.landscape.blockSignals(False)
            self.portrait.blockSignals(False)
            self.statusBar().showMessage("Ориентацию листа меняют, пока на нём нет фотографий")
        else:
            self.doc.landscape = self.landscape.isChecked()
        self.doc.paper_id = str(self.paper.currentData())
        known = PAPER_MM.get(self.doc.paper_id)
        custom = known is None
        self.paper_w.setEnabled(custom)
        self.paper_h.setEnabled(custom)
        if known is not None:
            self.paper_w.blockSignals(True)
            self.paper_h.blockSignals(True)
            self.paper_w.setValue(int(known[0]))
            self.paper_h.setValue(int(known[1]))
            self.paper_w.blockSignals(False)
            self.paper_h.blockSignals(False)
        else:
            self.doc.custom_w = float(self.paper_w.value())
            self.doc.custom_h = float(self.paper_h.value())
        self.doc.margin_top_mm = float(self.margin_top.value())
        self.doc.margin_right_mm = float(self.margin_right.value())
        self.doc.margin_bottom_mm = float(self.margin_bottom.value())
        self.doc.margin_left_mm = float(self.margin_left.value())
        self.doc.margin_mm = self.doc.margin_left_mm
        self.doc.cut_style = str(self.cut_style.currentData() or "corners")
        self.doc.cut_length_mm = self.settings.cut_length_mm
        self.doc.cut_marks = self.doc.cut_style != "none"
        self.doc.snap = self.snap_box.isChecked()
        for page in self.pages:
            page.snap = self.doc.snap
        self.printer.keep_inside = self.inside_box.isChecked()
        self.printer.block_overlap = self.overlap_box.isChecked()
        self._refresh_sheet()
        if paper_mm(self.doc) != previous_size:
            self.printer.fit()
        self._remember()

    def _footer_text(self) -> str:
        shot = self.current_shot()
        name = shot.path.name if shot else ""
        if not self.footer_box.isChecked():
            return ""
        custom = self.footer.text().strip()
        studio = self.settings.studio or "Фото"
        from datetime import date

        base = f"{studio}  {date.today().isoformat()}  {name}"
        return f"{custom}  {base}" if custom else base

    def _refresh_sheet(self) -> None:
        pixmaps = {}
        for item in self.doc.items:
            if item.kind != "photo" or item.ready_id in pixmaps:
                continue
            ready = next((ready for ready in self.ready if ready.id == item.ready_id), None)
            if ready is None:
                continue
            pixmap = self._sheet_pixmap(item, ready)
            if pixmap is not None:
                pixmaps[item.ready_id] = pixmap
        live = {
            photo.ready_id
            for page in self.pages
            for photo in page.items
            if photo.kind == "photo"
        }
        self._sheet_pixmaps = {ready_id: stored for ready_id, stored in self._sheet_pixmaps.items() if ready_id in live}
        self.printer.set_document(self.doc, pixmaps, self._footer_text())
        self._fill_sheet_table()
        self._sync_page_nav()

    def _sheet_pixmap_key(self, item: SheetItem, ready: ReadyItem, shot: Shot | None, fmt: Format | None) -> tuple:
        path = Path(item.path)
        try:
            stat = path.stat()
            file_mark = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            file_mark = None
        if shot is None or fmt is None:
            return ("file", str(path), file_mark)
        balance = tuple(round(float(value), 2) for row in self._balance() for value in row)
        return (
            "shot",
            str(shot.path),
            id(shot.working),
            round(shot.zoom, 3),
            round(shot.shift_x, 2),
            round(shot.shift_y, 2),
            round(shot.angle, 2),
            round(shot.brightness, 2),
            round(shot.contrast, 2),
            round(shot.gamma, 2),
            round(shot.saturation, 2),
            round(shot.sharpness, 2),
            round(shot.black_point, 1),
            round(shot.white_point, 1),
            round(shot.tolerance, 2),
            bool(shot.replace),
            self._background_method(),
            self._paint_background(),
            shot.clothes,
            round(shot.eye_t, 3),
            ready.format_id,
            ready.corner,
            ready.grayscale,
            round(ready.corner_mm, 2),
            round(fmt.width_mm, 2),
            round(fmt.height_mm, 2),
            round(fmt.head_height_mm, 2),
            round(fmt.chin_bottom_mm, 2),
            fmt.background,
            self._background(),
            int(self.settings.brush_px),
            balance,
            file_mark,
        )

    def _sheet_pixmap(self, item: SheetItem, ready: ReadyItem) -> QPixmap | None:
        """Картинка для листа. Повторное открытие вкладки берёт уже собранный кадр."""
        shot = self._shot_for(ready.source)
        fmt = next((fmt for fmt in self.formats if fmt.id == ready.format_id), None)
        if fmt is not None and ready.corner_mm > 0:
            fmt = replace(fmt, corner_rx_mm=ready.corner_mm, corner_ry_mm=ready.corner_mm)
        key = self._sheet_pixmap_key(item, ready, shot, fmt)
        stored = self._sheet_pixmaps.get(ready.id)
        if stored is not None and stored[0] == key:
            return stored[1]
        image = self._saved_sheet_image(item, 640)
        if image is None and shot is not None and fmt is not None:
            long_mm = max(fmt.width_mm, fmt.height_mm, 1.0)
            dpi = max(72, int(round(640 * 25.4 / long_mm)))
            image = self._render(shot, shot.working, dpi, ready.corner, ready.grayscale, fmt=fmt)
        if image is None:
            return None
        pixmap = pil_to_pixmap(image)
        self._sheet_pixmaps[ready.id] = (key, pixmap)
        return pixmap

    def _sheet_changed(self) -> None:
        self.doc = self.printer.doc
        self.pages[self.page_index] = self.doc

    def _arrange(self) -> None:
        photos = [item for item in self.doc.items if item.kind == "photo"]
        self.doc.items = [item for item in self.doc.items if item.kind != "photo"]
        self._lay_photos(photos)
        self._refresh_sheet()

    def _delete_from_sheet(self) -> None:
        self.doc.items = [item for item in self.doc.items if item.id not in self.printer.selected]
        self.printer.selected = set()
        self._refresh_sheet()

    def _rotate(self) -> None:
        if not self.allow_rotate:
            self.statusBar().showMessage("Включите «Разрешить поворот» на панели формата")
            return
        for item in self.doc.items:
            if item.id in self.printer.selected and item.kind == "photo":
                item.rotation = (item.rotation + 90) % 360
                item.w_mm, item.h_mm = item.h_mm, item.w_mm
        self._refresh_sheet()

    def _choose_logo(self) -> None:
        current = getattr(self, "_logo_path", "") or ""
        start = str(Path(current).parent) if current else ""
        path, _selected = QFileDialog.getOpenFileName(self, "Выбрать логотип", start, "Изображение (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        self._logo_path = path
        self.settings.logo_path = path
        self.statusBar().showMessage(f"Логотип: {Path(path).name}")

    def _logo_place_key(self) -> str:
        button = self.logo_place.checkedButton()
        if button is None:
            return "among"
        return str(button.property("place") or "among")

    def _logo_point(self) -> tuple[float, float]:
        width, height = paper_mm(self.doc)
        top, right, bottom, left = sheet_edges(self.doc)
        size = 18.0
        place = self._logo_place_key()
        if place == "among":
            occupied = [(item.x_mm, item.y_mm, item.w_mm, item.h_mm) for item in self.doc.items if item.kind != "footer"]
            slot = _slot(self.doc, size, size, occupied)
            if slot is not None:
                return slot
            return left, top
        x = {
            "top-left": left,
            "top-center": left + max(0.0, (width - left - right - size) / 2),
            "top-right": width - right - size,
            "bottom-left": left,
            "bottom-center": left + max(0.0, (width - left - right - size) / 2),
            "bottom-right": width - right - size,
        }[place]
        y = top if place.startswith("top") else max(top, height - bottom - size)
        return x, y

    def _put_logo(self, path: str) -> None:
        x, y = self._logo_point()
        self._seq += 1
        self.doc.items.append(SheetItem(f"logo{self._seq}", "logo", x, y, 18, 18, path=path))
        self.pages[self.page_index] = self.doc
        self._refresh_sheet()

    def _maybe_auto_logo(self) -> None:
        if not self.logo_auto_box.isChecked():
            return
        path = getattr(self, "_logo_path", "") or self.settings.logo_path
        if not path or not Path(path).exists():
            return
        if not any(item.kind == "photo" for item in self.doc.items):
            return
        if any(item.kind == "logo" for item in self.doc.items):
            return
        x, y = self._logo_point()
        self._seq += 1
        self.doc.items.append(SheetItem(f"logo{self._seq}", "logo", x, y, 18, 18, path=path))

    def _add_logo(self) -> None:
        path = getattr(self, "_logo_path", "") or self.settings.logo_path
        if not path or not Path(path).exists():
            self._choose_logo()
            path = getattr(self, "_logo_path", "")
            if not path:
                return
        self._put_logo(path)

    def _remove_logo(self) -> None:
        self.doc.items = [item for item in self.doc.items if item.kind != "logo"]
        self._refresh_sheet()

    def _save_scheme(self) -> None:
        name, ok = QInputDialog.getText(self, "Схема", "Название схемы")
        if ok and name.strip():
            save_scheme(name.strip(), self.doc)
            self._reload_blocks()
            self.statusBar().showMessage(f"Схема «{name.strip()}» сохранена")

    def _open_scheme(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Открыть схему", str(Path.home() / ".config" / "photofordocs" / "schemes"), "Схема (*.yaml)")
        if path:
            self.doc = load_scheme(Path(path), self.doc)
            self.pages[self.page_index] = self.doc
            self._refresh_sheet()

    def _histogram(self) -> None:
        shot = self.current_shot()
        if not shot:
            return
        image = self._finished_image if self._mode == "ready" and self._finished_image is not None else shot.working
        HistogramDialog(image, self).exec()

    def _about_program(self) -> None:
        from photofordocs import __version__

        QMessageBox.about(
            self,
            "О программе",
            "Фото на документы\n"
            f"Версия {__version__}\n\n"
            "Бесплатная программа для подготовки фотографий на документы.\n"
            "Снимок обрабатывается на этом компьютере и никуда не отправляется.",
        )

    def _about_format(self) -> None:
        fmt = self.current_format()
        QMessageBox.information(self, fmt.name, f"{fmt.summary()}\n\n{fmt.note}")

    def _open_format_catalog(self) -> None:
        FormatCatalog(self.formats, self).exec()

    def _tolerance_value(self) -> float:
        threshold = self.settings.ground_threshold
        if 0 < threshold <= 20:
            return min(90.0, threshold * 10)
        return float(self.tolerance.value())

    def _apply_workplace(self) -> None:
        """Подставляет в окно лист, поля, тон и палитру из сохранённых настроек."""
        paper_index = self.paper.findData(self.settings.default_paper)
        if paper_index >= 0:
            self.paper.setCurrentIndex(paper_index)
        self.landscape.setChecked(self.settings.default_landscape)
        self.portrait.setChecked(not self.settings.default_landscape)
        self.margin_top.setValue(int(round(self.settings.margin_top_mm)))
        self.margin_right.setValue(int(round(self.settings.margin_right_mm)))
        self.margin_bottom.setValue(int(round(self.settings.margin_bottom_mm)))
        self.margin_left.setValue(int(round(self.settings.margin_left_mm)))
        cut = self.cut_style.findData(self.settings.cut_style)
        if cut >= 0:
            self.cut_style.setCurrentIndex(cut)
        self.inside_box.setChecked(self.settings.check_margins)
        self.overlap_box.setChecked(self.settings.check_overlap)
        self.ask_printer_box.setChecked(self.settings.show_print_dialog)
        self.clear_after_box.setChecked(self.settings.clear_after_print)
        self.center_box.setChecked(self.settings.align_center)
        self.logo_auto_box.setChecked(self.settings.logo_auto)
        self.footer.setText(self.settings.advert_text)
        self._logo_path = self.settings.logo_path
        dpi = int(self.settings.export_dpi)
        if self.dpi_box.minimum() <= dpi <= self.dpi_box.maximum():
            self.dpi_box.setValue(dpi)
        known = {self.color_box.itemData(index) for index in range(self.color_box.count())}
        for item in self.settings.colors:
            color = str(item.get("color", ""))
            name = str(item.get("name", "")).strip()
            if name and color and color not in known:
                self.color_box.addItem(name, color)
                known.add(color)
        pairs = (
            (self.bright, self.settings.tone_brightness),
            (self.contrast, self.settings.tone_contrast),
            (self.gamma, self.settings.tone_gamma),
            (self.saturation, self.settings.tone_saturation),
            (self.sharpness, self.settings.tone_sharpness),
            (self.tolerance, self._tolerance_value()),
        )
        for slider, value in pairs:
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
        flat = self.settings.tone_balance
        for row_index, row in enumerate(self.balance):
            for column, box in enumerate(row):
                box.blockSignals(True)
                box.setValue(int(flat[row_index * 3 + column]))
                box.blockSignals(False)
        self._sync_color_sliders()
        self._sync_tone_numbers()
        if hasattr(self, "replace_box"):
            self.replace_box.blockSignals(True)
            self.replace_box.setChecked(bool(self.settings.replace_background))
            self.replace_box.blockSignals(False)
        if hasattr(self, "fill_figure"):
            figure = getattr(self.settings, "background_method", "edge") == "figure"
            self.fill_figure.blockSignals(True)
            self.fill_edge.blockSignals(True)
            self.fill_figure.setChecked(figure)
            self.fill_edge.setChecked(not figure)
            self.fill_figure.blockSignals(False)
            self.fill_edge.blockSignals(False)
        if hasattr(self, "paint_box"):
            self.paint_box.blockSignals(True)
            self.paint_box.setChecked(bool(getattr(self.settings, "paint_background", False)))
            self.paint_box.blockSignals(False)
        if self.settings.file_panel_width >= 180:
            sizes = self.splitter.sizes()
            if len(sizes) == 3:
                self.splitter.setSizes([int(self.settings.file_panel_width), max(200, sizes[1]), sizes[2]])
        self._apply_saved_format()
        self._paper_changed()

    def _reload_clothes(self) -> None:
        self.clothes = all_clothes()

    def import_photolab_settings(self) -> None:
        suggested = Path(__file__).resolve().parents[2] / "оригинал" / "18.11.phs"
        start = str(suggested if suggested.is_file() else suggested.parent)
        path, _selected = QFileDialog.getOpenFileName(self, "Перенос настроек PhotoLab", start, "Перенос настроек (*.phs)")
        if not path:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Перенос настроек программы")
        text = QLabel(
            "Файл подставляется в эту программу: пути, лист, поля, принтер, тон, цены и форматы.\n"
            "Лицензия не переносится."
        )
        text.setWordWrap(True)
        take_settings = QCheckBox("Настройки программы")
        take_formats = QCheckBox("Форматы")
        take_settings.setChecked(True)
        take_formats.setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Начать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(QLabel(path))
        layout.addWidget(take_settings)
        layout.addWidget(take_formats)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not take_settings.isChecked() and not take_formats.isChecked():
            return
        try:
            report = import_phs(Path(path), settings=take_settings.isChecked(), formats=take_formats.isChecked())
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(self, "Перенос настроек", str(error))
            return
        self.settings = load_settings()
        if take_formats.isChecked():
            self.formats = load_formats()
            self._fill_format_combo(self.format_box)
            self._load_format_fields()
        if take_settings.isChecked():
            self._apply_workplace()
            self._reload_clothes()
            self._file_order = (self.settings.sort_key, self.settings.sort_reverse)
            folder = Path(self.settings.archive_root)
            if folder.is_dir():
                self._show_folder(folder)
        QMessageBox.information(self, "Перенос настроек", report.text() or "Файл прочитан.")

    def import_original_formats(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Справочник оригинальной программы",
            str(Path.home()),
            "Справочник (formats.ini *.ini);;Все файлы (*)",
        )
        if not path:
            return
        try:
            loaded = import_photolab_file(Path(path))
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(self, "Импорт форматов", str(error))
            return
        self.formats = load_formats()
        self._fill_format_combo(self.format_box)
        self._load_format_fields()
        QMessageBox.information(
            self,
            "Импорт форматов",
            f"Перенесено форматов: {len(loaded)}. Они лежат в справочнике программы и открываются из любой папки со снимками.",
        )

    def add_format(self) -> None:
        self._open_format_catalog()

    def _external_editor(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        editor = self.settings.editor
        if not editor:
            self.statusBar().showMessage("Укажите внешний редактор в настройках")
            return
        path = str(shot.path.resolve())
        if path not in self._source_watch.files():
            self._source_watch.addPath(path)
        subprocess.Popen([editor, path])
        self.statusBar().showMessage(f"{shot.path.name} в редакторе — после сохранения вернитесь в окно программы")

    def _watch(self) -> None:
        folder = self.settings.watch_folder or QFileDialog.getExistingDirectory(self, "Следить за папкой")
        if not folder:
            return
        from PySide6.QtCore import QFileSystemWatcher

        self.settings.watch_folder = folder
        self._watcher = QFileSystemWatcher([folder], self)
        self._watcher.directoryChanged.connect(lambda path: self._ingest(Path(path)))
        self.statusBar().showMessage(f"Слежу за {folder}")
        self._ingest(Path(folder))

    def _ingest(self, folder: Path) -> None:
        known = {str(shot.path) for shot in self.shots}
        minimum = self.settings.watch_min_kb * 1024
        fresh = [
            path
            for path in sorted(folder.iterdir())
            if path.suffix.lower() in IMAGE_SUFFIXES and str(path) not in known and path.stat().st_size >= minimum
        ]
        if not fresh:
            return
        self.open_paths(list({shot.path for shot in self.shots} | set(fresh)) if self.shots else fresh, reset_job=False)

    def _settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.formats, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            previous_advert = self.settings.advert_text
            self.settings = dialog.apply()
            save_settings(self.settings)
            if not self.footer.text().strip() or self.footer.text().strip() == previous_advert:
                self.footer.setText(self.settings.advert_text)
            if not any(item.kind == "photo" for item in self.doc.items):
                self._apply_workplace()

    def _set_paper_layout(self, horizontal: bool) -> None:
        self._paper_horizontal = horizontal
        self.view_vert.setChecked(not horizontal)
        self.view_horz.setChecked(horizontal)
        self._remember()
        if self._mode != "print":
            self._set_mode("print")
            return
        self._apply_paper_layout()

    def _apply_paper_layout(self) -> None:
        horizontal = self._paper_horizontal and self._mode == "print"
        host = self.print_host.layout()
        if horizontal:
            if self.print_panel.parent() is not self.print_host:
                host.addWidget(self.print_panel)
            sizes = self.splitter.sizes()
            if sizes[2] > 40:
                self._vert_right = sizes[2]
            self.right.hide()
            self.print_split.setSizes([max(self.height() - 420, 240), 280])
            return
        if self.right.indexOf(self.print_panel) < 0:
            self.right.addWidget(self.print_panel)
        self.right.setMinimumWidth(260)
        self.right.show()
        self.print_split.setSizes([max(self.height() - 160, 400), 0])
        sizes = self.splitter.sizes()
        if sizes[2] < 40:
            right = getattr(self, "_vert_right", 300)
            self.splitter.setSizes([sizes[0], max(200, sizes[1] - right), right])
        if self._mode == "print":
            self.right.setCurrentWidget(self.print_panel)

    def _search_file(self) -> None:
        text, ok = QInputDialog.getText(self, "Поиск файла", "Имя или часть имени")
        if not ok or not text.strip():
            return
        needle = text.strip().lower()
        roots = [self.folder_root or Path(self.settings.archive_root)]
        for extra in self.settings.search_folder.replace("|", "\n").splitlines():
            if extra.strip():
                roots.append(Path(extra.strip()))
        found: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and needle in path.name.lower() and str(path) not in seen:
                    seen.add(str(path))
                    found.append(path)
                    if len(found) >= 40:
                        break
            if len(found) >= 40:
                break
        if not found:
            self.statusBar().showMessage("Файл не найден")
            return
        if len(found) == 1:
            chosen = found[0]
        else:
            base = roots[0]
            labels = [str(path.relative_to(base)) if path.is_relative_to(base) else str(path) for path in found]
            name, picked = QInputDialog.getItem(self, "Поиск файла", "Найдено", labels, 0, False)
            if not picked:
                return
            chosen = found[labels.index(name)]
        self._show_folder(chosen.parent)
        self._show_preview(chosen)
        self._mark_tree(chosen)
        self.statusBar().showMessage(str(chosen))

    def _help(self) -> None:
        QMessageBox.information(
            self,
            "Справка",
            "Слева выбираются диск, папка и файл. Один щелчок показывает превью, двойной открывает снимок.\n\n"
            "«Размеры» задаёт рамку кадра. «Готовая фотография» показывает кадр после обрезки и коррекции. "
            "«Печать» раскладывает готовые кадры на листе.\n\n"
            "Гистограмма открывается из меню «Окна». Раскладка панели печати — из меню «Вид».",
        )

    def _tone_snapshot(self) -> dict:
        return {
            "brightness": self.bright.value(),
            "contrast": self.contrast.value(),
            "gamma": self.gamma.value(),
            "saturation": self.saturation.value(),
            "sharpness": self.sharpness.value(),
            "black": self.black.value(),
            "white": self.white.value(),
            "tolerance": self.tolerance.value(),
            "replace": self.replace_box.isChecked(),
            "balance": [[box.value() for box in row] for row in self.balance],
        }

    def _write_tone(self, tone: dict) -> None:
        pairs = (
            (self.bright, tone["brightness"]),
            (self.contrast, tone["contrast"]),
            (self.gamma, tone["gamma"]),
            (self.saturation, tone["saturation"]),
            (self.sharpness, tone["sharpness"]),
            (self.black, tone["black"]),
            (self.white, tone["white"]),
            (self.tolerance, tone["tolerance"]),
        )
        for slider, value in pairs:
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
        if "replace" in tone and hasattr(self, "replace_box"):
            self.replace_box.blockSignals(True)
            self.replace_box.setChecked(bool(tone["replace"]))
            self.replace_box.blockSignals(False)
        for row, values in zip(self.balance, tone["balance"]):
            for box, value in zip(row, values):
                box.blockSignals(True)
                box.setValue(int(value))
                box.blockSignals(False)
        self._sync_color_sliders()
        self._sync_tone_numbers()
        self._store_tone()

    def _tone_mark(self, tone: dict) -> tuple:
        keys = ("brightness", "contrast", "gamma", "saturation", "sharpness", "black", "white", "tolerance")
        numbers = tuple(int(tone.get(key, 0)) for key in keys)
        replace = bool(tone.get("replace", False))
        balance = tuple(tuple(int(value) for value in row) for row in tone.get("balance", []))
        return numbers + (replace, balance)

    def _matching_tune_name(self) -> str:
        if not hasattr(self, "bright"):
            return ""
        current = self._tone_mark(self._tone_snapshot())
        for item in self._tunes:
            if self._tone_mark(item["tone"]) == current:
                return item["name"]
        return ""

    def _fill_tune_menu(self) -> None:
        menu = getattr(self, "tune_menu", None)
        if menu is None:
            return
        menu.clear()
        current = self._matching_tune_name()
        for item in self._tunes:
            action = menu.addAction(item["name"])
            action.setCheckable(True)
            action.setChecked(item["name"] == current)
            action.triggered.connect(lambda _checked=False, tone=item["tone"]: self._apply_saved_tune(tone))
        menu.addSeparator()
        save = menu.addAction("Записать")
        save.setEnabled(bool(current))
        save.triggered.connect(self._update_quick_tune)
        menu.addAction("Добавить").triggered.connect(self._add_quick_tune)
        delete = menu.addAction("Удалить")
        delete.setEnabled(bool(current))
        delete.triggered.connect(self._delete_quick_tune)

    def _apply_saved_tune(self, tone: dict) -> None:
        if self.current_shot() is None:
            self.statusBar().showMessage("Сначала откройте снимок")
            return
        self._write_tone(tone)

    def _add_quick_tune(self) -> None:
        name, ok = QInputDialog.getText(self, "Быстрая настройка", "Название")
        if not ok or not name.strip():
            return
        self._tunes.append({"name": name.strip(), "tone": self._tone_snapshot()})
        self._fill_tune_menu()
        self._remember()
        self.statusBar().showMessage(f"Настройка «{name.strip()}» записана")

    def _update_quick_tune(self) -> None:
        name = self._matching_tune_name()
        for item in self._tunes:
            if item["name"] == name:
                item["tone"] = self._tone_snapshot()
                self._fill_tune_menu()
                self._remember()
                self.statusBar().showMessage("Настройка обновлена")
                return
        self.statusBar().showMessage("Сначала выберите настройку, которая совпадает с ползунками")

    def _delete_quick_tune(self) -> None:
        name = self._matching_tune_name()
        if not name:
            return
        self._tunes = [item for item in self._tunes if item["name"] != name]
        self._fill_tune_menu()
        self._remember()

    def _journal(self) -> None:
        dialog = JournalDialog(list(self.journal), self)
        dialog.exec()
        from photofordocs.session import save_journal

        self.journal = dialog.lines
        save_journal(self.journal, month_folder(Path(self.settings.archive_root)))

    def _archive_dir(self) -> Path:
        root = self.folder_root or Path(self.settings.archive_root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _with_profile(self, image: Image.Image) -> Image.Image:
        apply = self.settings.icc if self.settings.icc_apply is None else self.settings.icc_apply
        if not apply or not self.settings.icc or not Path(self.settings.icc).exists():
            return image
        try:
            return ImageCms.profileToProfile(image, ImageCms.createProfile("sRGB"), ImageCms.getOpenProfile(self.settings.icc), outputMode="RGB")
        except (OSError, ImageCms.PyCMSError):
            self.statusBar().showMessage("Профиль печати не прочитан, лист без него")
            return image

    def _record(self, number: int) -> None:
        names = []
        copies = 0
        amount = 0.0
        for item in self.ready:
            names.append(item.format_id)
            copies += item.copies
            amount += self.settings.price_for(item.format_id) * item.copies
        if not names:
            names = [self.current_format().id]
            copies = 1
            amount = self.settings.price_for(self.current_format().id)
        folder = self._archive_dir()
        self.journal = add_journal(self.journal, f"{number:03d}", ", ".join(names), copies, amount, folder)
        text = report_text(self.journal)
        (folder / "отчёт.txt").write_text(text, encoding="utf-8")
        try:
            note = send_report(self.settings, text)
        except (OSError, TimeoutError) as error:
            note = f"письмо не ушло: {error}"
        self.statusBar().showMessage(f"Журнал {number:03d}. {note}")

    def save_photo(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        dpi = int(self.dpi_box.value())
        fmt = self.current_format()
        image = self._with_profile(self._render(shot, shot.image, dpi, self._corner_name(), self._wants_gray()))
        pad = int(round(self.settings.white_frame_mm / 25.4 * dpi))
        if pad > 0:
            framed = Image.new("RGB", (image.width + pad * 2, image.height + pad * 2), (255, 255, 255))
            framed.paste(image, (pad, pad))
            image = framed
        path = ready_photo_path(shot.path, fmt.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and self.settings.confirm_overwrite:
            answer = QMessageBox.question(self, "Сохранить фото", f"Заменить {path.name}?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._write_jpeg(image, path, dpi, self.settings.jpeg_photo, fmt.max_file_kb)
        write_ready_info(
            path,
            {
                "source": shot.path.name,
                "format_id": fmt.id,
                "name": fmt.name,
                "printed": False,
                "dpi": dpi,
                "width_mm": fmt.width_mm,
                "height_mm": fmt.height_mm,
                "head_mm": fmt.head_height_mm,
                "chin_mm": fmt.chin_bottom_mm,
                "zoom": shot.zoom,
                "shift_x": shot.shift_x,
                "shift_y": shot.shift_y,
                "angle": shot.angle,
                "brightness": shot.brightness,
                "contrast": shot.contrast,
                "gamma": shot.gamma,
                "saturation": shot.saturation,
                "sharpness": shot.sharpness,
            },
        )
        self._fill_files()
        self._last_saved = str(path)
        number = next_number(self._archive_dir())
        self._record(number)
        self.statusBar().showMessage(f"Сохранено {path}")

    def save_photo_as(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        fmt = self.current_format()
        dpi = int(self.dpi_box.value()) or int(self.settings.export_dpi) or 300
        preview = self.finished.pixmap if self.finished.pixmap is not None else QPixmap()
        dialog = SaveFileDialog(
            self,
            fmt.width_mm,
            fmt.height_mm,
            dpi,
            int(self.settings.save_max_kb) or 48,
            self.settings.save_limit_size,
            self.settings.save_limit_bytes,
            self.settings.save_open_folder,
            preview,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        spec = dialog.spec()
        self.settings.save_limit_size = spec.limit_size
        self.settings.save_limit_bytes = spec.limit_bytes
        self.settings.save_open_folder = spec.open_folder
        self.settings.save_max_kb = spec.max_kb
        self._remember()
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Сохранить в файл",
            str(shot.path.with_suffix(".jpg")),
            "JPEG (*.jpg *.jpeg)",
        )
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() not in {".jpg", ".jpeg"}:
            destination = destination.with_suffix(".jpg")
        image = self._with_profile(self._render(shot, shot.image, spec.dpi, self._corner_name(), self._wants_gray()))
        if spec.limit_size and image.size != (spec.width_px, spec.height_px):
            image = image.resize((spec.width_px, spec.height_px), Image.Resampling.LANCZOS)
        self._write_jpeg(
            image,
            destination,
            spec.dpi,
            self.settings.jpeg_photo,
            spec.max_kb if spec.limit_bytes else 0,
        )
        if spec.open_folder and os.name == "nt":
            try:
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(destination)}"])
            except OSError:
                pass
        if self.settings.external_save_price:
            folder = self._archive_dir()
            number = next_number(folder)
            self.journal = add_journal(
                self.journal,
                f"{number:03d}",
                "внешний файл",
                1,
                self.settings.external_save_price,
                folder,
            )
        self.statusBar().showMessage(f"Сохранено {destination}")

    def _write_jpeg(self, image: Image.Image, path: Path, dpi: int, quality: int, max_kb: int) -> None:
        write_jpeg(image, Path(path), int(dpi), int(quality), int(max_kb or 0))

    def _compose_sheet(self, dpi: int, doc: SheetDoc | None = None) -> Image.Image:
        doc = self.doc if doc is None else doc
        photos = {}
        for item in doc.items:
            if item.kind != "photo" or item.ready_id in photos:
                continue
            ready = next((ready for ready in self.ready if ready.id == item.ready_id), None)
            if ready is None:
                continue
            image = self._saved_sheet_image(item)
            if image is None:
                image = self._render_ready(ready, dpi)
            if image is not None:
                photos[item.ready_id] = image
        logo = None
        for item in doc.items:
            if item.kind == "logo" and item.path:
                logo = Image.open(item.path)
        return self._with_profile(render_document(doc, photos, dpi, self._footer_text(), logo))

    def save_sheet(self) -> None:
        if not any(item.kind == "photo" for item in self.doc.items):
            QMessageBox.information(self, "Лист", "На листе нет кадров. Нажмите «Печать», чтобы поставить готовую фотографию.")
            return
        folder = Path(self.settings.sheets_folder) if self.settings.sheets_folder.strip() else self._archive_dir()
        folder.mkdir(parents=True, exist_ok=True)
        number = next_number(folder)
        _photo_name, sheet_name = archive_names(number, "лист")
        path = folder / sheet_name
        dpi = self.settings.printer_dpi or EXPORT_DPI
        self._write_jpeg(self._compose_sheet(dpi), path, dpi, self.settings.jpeg_sheet, 0)
        self._record(number)
        self.statusBar().showMessage(f"Лист {path}")

    def print_sheet(self) -> None:
        self.pages[self.page_index] = self.doc
        sheets = [page for page in self.pages if any(item.kind == "photo" for item in page.items)]
        if not sheets:
            QMessageBox.information(self, "Печать", "На листе нет кадров.")
            return
        if self.ask_printer_box.isChecked():
            choice = self._printer_dialog()
            if choice is None:
                return
            printer_name, copies = choice
        else:
            printer_name = self.settings.printer_name or winprint.default_printer()
            copies = 1
            if not printer_name:
                QMessageBox.warning(self, "Печать", "Принтер не выбран. Включите диалог печати и укажите его.")
                return
        dpi = self.settings.printer_dpi or EXPORT_DPI
        pages = []
        for sheet in sheets:
            image = pil_to_pixmap(self._compose_sheet(dpi, sheet)).toImage().convertToFormat(QImage.Format.Format_RGB32)
            bits = image.constBits()
            raw = bits.tobytes() if isinstance(bits, memoryview) else bytes(bits)
            pages.append((image.width(), image.height(), image.bytesPerLine(), raw))
        error = winprint.print_images(printer_name, pages, copies)
        if error:
            QMessageBox.warning(self, "Печать", error)
            return
        folder = self._archive_dir()
        self._record(next_number(folder))
        if self.clear_after_box.isChecked():
            self._clear_sheet()

    def _printer_dialog(self) -> tuple[str, int] | None:
        """Короткое окно: принтер, параметры драйвера и число копий."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Печать")
        names = winprint.printer_names()
        saved = self.settings.printer_name.strip()
        if saved and saved not in names:
            names.insert(0, saved)
        if not names:
            QMessageBox.warning(self, "Печать", "В системе нет принтеров.")
            return None
        combo = QComboBox()
        combo.addItems(names)
        preferred = saved if saved in names else winprint.default_printer()
        found = combo.findText(preferred)
        if found >= 0:
            combo.setCurrentIndex(found)
        setup = QPushButton("Настроить...")
        copies = QSpinBox()
        copies.setRange(1, 99)
        copies.setValue(1)
        copies.setFixedWidth(70)

        def on_setup() -> None:
            name = combo.currentText().strip()
            data = winprint.configure(name, int(dialog.winId()))
            if data:
                winprint.save_devmode(name, data)

        setup.clicked.connect(on_setup)
        buttons = QDialogButtonBox()
        buttons.addButton("Ок", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        row = QHBoxLayout()
        row.addWidget(QLabel("Принтер:"))
        row.addWidget(combo, 1)
        row.addWidget(setup)
        copy_row = QHBoxLayout()
        copy_row.addWidget(QLabel("Число копий:"))
        copy_row.addWidget(copies)
        copy_row.addStretch(1)
        layout = QVBoxLayout(dialog)
        layout.addLayout(row)
        layout.addLayout(copy_row)
        layout.addWidget(buttons)
        dialog.setMinimumWidth(420)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = combo.currentText().strip()
        self.settings.printer_name = name
        save_settings(self.settings)
        return name, int(copies.value())
