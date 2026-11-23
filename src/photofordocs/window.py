"""Окно стойки: сборка закладок, меню и рабочее место."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import QDir, QEvent, QFileSystemWatcher, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photofordocs.canvas import PrintCanvas, SourceCanvas, scroll_by
from photofordocs.dialogs import FormatCatalog, HistogramDialog, JournalDialog, SettingsDialog
from photofordocs.formats import Format, import_photolab_file, load_formats
from photofordocs.frame import FrameMixin
from photofordocs.files import FilesMixin
from photofordocs.phs import import_phs
from photofordocs.session import (
    ReadyItem,
    add_journal,
    load_journal,
    load_settings,
    month_folder,
    next_number,
    report_text,
    save_settings,
    send_report,
)
from photofordocs.sheetui import SheetMixin
from photofordocs.shot import Shot
from photofordocs.tone import ToneMixin
from photofordocs.widgets import (
    THUMB_SIDE,
    FileTree,
    FinishedView,
    IndicatorStyle,
    ReadyDropTable,
    UnitSlider,
    install_indicator_style,
    pil_to_pixmap,
    reveal_opened_branch,
)

__all__ = [
    "FinishedView",
    "FileTree",
    "IndicatorStyle",
    "MainWindow",
    "ReadyDropTable",
    "Shot",
    "UnitSlider",
    "install_indicator_style",
    "reveal_opened_branch",
]


class MainWindow(FilesMixin, FrameMixin, ToneMixin, SheetMixin, QMainWindow):
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
            button.setProperty("zoom_kind", kind)
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

    def _zoom_icon(self, kind: str) -> QIcon:
        pix = QPixmap(18, 18)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        from photofordocs.theme import tokens

        blue = QColor(tokens()["accent"])
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
        self.files.setExpandsOnDoubleClick(False)
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
        from photofordocs.theme import bind_theme, set_theme_choice

        set_theme_choice(self.settings.theme)
        bind_theme(QApplication.instance())
        self.refresh_theme()

    def _slider(self, low: int, high: int, value: int, handler, groove: str | None = None) -> QSlider:
        slider = UnitSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setSingleStep(1)
        slider.setValue(value)
        slider.valueChanged.connect(handler)
        self._paint_slider(slider, groove)
        return slider

    def _paint_slider(self, slider: QSlider, groove: str | None = None, *, remember: bool = True) -> None:
        """Одна и та же круглая ручка. Пустая дорожка берётся из темы, заданная остаётся подписью."""
        from photofordocs.theme import tokens

        if remember:
            slider.setProperty("ink_groove", groove or "")
        color = tokens()
        track = slider.property("ink_groove") or color["slider"]
        slider.setStyleSheet(
            "QSlider::groove:horizontal {"
            f" height: 8px; border-radius: 4px; border: 1px solid {color['slider_border']}; background: {track};"
            "}"
            "QSlider::handle:horizontal {"
            " width: 14px; height: 14px; margin: -4px 0; border-radius: 7px;"
            f" background: {color['slider_handle']}; border: 1px solid {color['slider_edge']};"
            "}"
        )

    def refresh_theme(self) -> None:
        """Ползунки и значки масштаба держат цвет отдельно от общей таблицы."""
        for slider in self.findChildren(UnitSlider):
            self._paint_slider(slider, remember=False)
        for button in self.findChildren(QToolButton):
            kind = button.property("zoom_kind")
            if kind:
                button.setIcon(self._zoom_icon(str(kind)))

    def _spin_width(self, sample: str) -> int:
        return self.fontMetrics().horizontalAdvance(sample) + 36

    def _scroll(self, page: QWidget) -> QScrollArea:
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

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
            self._fill_printers()
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
            if hasattr(self, "ask_printer_box"):
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
        if hasattr(self, "ask_printer_box"):
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
            self._file_order = (self.settings.sort_key, self.settings.sort_reverse)
            if hasattr(self, "_sort_held"):
                self._sort_held[self.settings.sort_key] = self.settings.sort_reverse
                self._sync_sort_buttons()
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

    def _settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.formats, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            previous_advert = self.settings.advert_text
            self.settings = dialog.apply()
            save_settings(self.settings)
            from photofordocs.theme import apply_chrome, set_theme_choice

            set_theme_choice(self.settings.theme)
            apply_chrome(QApplication.instance())
            self.refresh_theme()
            if not self.footer.text().strip() or self.footer.text().strip() == previous_advert:
                self.footer.setText(self.settings.advert_text)
            if not any(item.kind == "photo" for item in self.doc.items):
                self._apply_workplace()

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
            "Слева выбираются диск, папка и файл. Один щелчок показывает превью, двойной по оригиналу открывает «Размеры».\n\n"
            "«Размеры» задаёт рамку кадра. «Готовая фотография» показывает кадр после обрезки и коррекции. "
            "«Печать» раскладывает готовые кадры на листе.\n\n"
            "Гистограмма открывается из меню «Окна». Раскладка панели печати — из меню «Вид».",
        )

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
