"""Лист печати: страницы, логотип и вывод на принтер."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
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
from photofordocs.formats import Format, import_photolab_file, load_formats, visible_formats
from photofordocs.imageops import (
    detect_face,
    detected_eye_t,
    downscale,
    contain_guides,
    format_eye_t,
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
    matte_to_alpha,
    move_middle,
    person_matte,
    pose_for_ends,
    pose_from_ends,
    render_crop,
    render_photo,
    write_jpeg,
    write_pdf,
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
from photofordocs.shot import Shot
from photofordocs.widgets import (
    EXPORT_DPI,
    IMAGE_SUFFIXES,
    PREVIEW_SIDE,
    THUMB_LIMIT,
    THUMB_SIDE,
    FileTree,
    FinishedView,
    ReadyDropTable,
    UnitSlider,
    pil_to_pixmap,
    reveal_opened_branch,
)

def _sheet_file_is_pdf(path: Path, selected: str) -> bool:
    """Расширение имени важнее фильтра. Без расширения берётся выбранный тип, иначе PDF."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return True
    if suffix in {".jpg", ".jpeg"}:
        return False
    return "jpeg" not in selected.lower()


class SheetMixin:
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

    def _print_side(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        printer_row = QHBoxLayout()
        printer_row.setSpacing(4)
        self.printer_box = QComboBox()
        self.printer_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.printer_box.setMinimumContentsLength(12)
        self.printer_box.currentIndexChanged.connect(self._printer_picked)
        self.printer_gear = QToolButton()
        self.printer_gear.setObjectName("filetool")
        self.printer_gear.setAutoRaise(False)
        self.printer_gear.setIcon(self._gear_icon())
        self.printer_gear.setIconSize(QSize(18, 18))
        self.printer_gear.setFixedSize(26, 26)
        self.printer_gear.setToolTip("Настройки принтера")
        self.printer_gear.clicked.connect(self._configure_printer)
        self.print_copies = QSpinBox()
        self.print_copies.setRange(1, 99)
        self.print_copies.setValue(max(1, min(99, int(self.settings.print_copies or 1))))
        self.print_copies.setSuffix(" шт")
        self.print_copies.setToolTip("Сколько копий листа отправить на принтер")
        self.print_copies.setFixedWidth(self._spin_width("99 шт"))
        self.print_copies.valueChanged.connect(self._print_copies_changed)
        printer_row.addWidget(self.printer_box, 1)
        printer_row.addWidget(self.print_copies)
        printer_row.addWidget(self.printer_gear)
        layout.addLayout(printer_row)
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.print_button = QPushButton("Печать")
        self.print_button.setObjectName("printaction")
        self.print_button.setMinimumHeight(40)
        self.print_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.print_button.setIcon(self._print_icon("print"))
        self.print_button.setIconSize(QSize(22, 22))
        self.print_button.clicked.connect(self._output_sheet)
        clear = QPushButton("Очистить лист")
        clear.setObjectName("printaction")
        clear.setMinimumHeight(40)
        clear.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        clear.setIcon(self._print_icon("clear"))
        clear.setIconSize(QSize(22, 22))
        clear.clicked.connect(self._clear_sheet)
        buttons.addWidget(self.print_button)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self._fill_printers()
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

    def _gear_icon(self) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#2a62c9"), 1.6))
        painter.translate(10, 10)
        for step in range(8):
            painter.drawLine(0, 6, 0, 9)
            painter.rotate(45)
        painter.setBrush(QColor("#d6e6f8"))
        painter.drawEllipse(-5, -5, 10, 10)
        painter.setBrush(QColor("#2a62c9"))
        painter.drawEllipse(-2, -2, 4, 4)
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
        for box in (self.overlap_box, self.inside_box, self.center_box, self.clear_after_box, self.footer_box):
            layout.addWidget(box)
        for box in (self.center_box, self.clear_after_box):
            box.toggled.connect(lambda _checked=False: self._remember())
        self.cut_width = QDoubleSpinBox()
        self.cut_width.setRange(0.05, 2.0)
        self.cut_width.setSingleStep(0.05)
        self.cut_width.setDecimals(2)
        self.cut_width.setSuffix(" мм")
        self.cut_width.setValue(float(self.settings.cut_width_mm or 0.1))
        self.cut_width.valueChanged.connect(self._paper_changed)
        cut_row = QHBoxLayout()
        cut_row.addWidget(QLabel("Линии отреза:"))
        cut_row.addWidget(self.cut_style, 1)
        layout.addLayout(cut_row)
        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("Толщина линии:"))
        width_row.addWidget(self.cut_width)
        width_row.addStretch(1)
        layout.addLayout(width_row)
        layout.addWidget(self.snap_box)
        layout.addStretch(1)
        return page

    def _print_logo_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 6)
        choose = QPushButton("Выбрать логотип")
        choose.clicked.connect(self._choose_logo)
        place_logo = QPushButton("Добавить на лист")
        place_logo.clicked.connect(self._add_logo)
        logo_row = QHBoxLayout()
        logo_row.setSpacing(4)
        logo_row.addWidget(choose)
        logo_row.addWidget(place_logo)
        layout.addLayout(logo_row)
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
        shot = self.current_shot()
        if shot is None:
            return
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
                zoom=shot.zoom,
                shift_x=shot.shift_x,
                shift_y=shot.shift_y,
                angle=shot.angle,
                eye_t=shot.eye_t,
            )
            self.ready.append(ready)
        else:
            ready.copies += copies
            ready.corner = self._corner_name()
            ready.corner_mm = float(self.corner_radius.value())
            ready.zoom = shot.zoom
            ready.shift_x = shot.shift_x
            ready.shift_y = shot.shift_y
            ready.angle = shot.angle
            ready.eye_t = shot.eye_t
        shown = str(picture) if picture is not None else str(source)
        fresh = []
        for _copy in range(copies):
            self._seq += 1
            fresh.append(SheetItem(f"p{self._seq}", "photo", 0, 0, width, height, ready.id, rotation, "", shown))
        self._lay_photos(fresh)
        self._set_mode("print")

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
        if item.corner_mm > 0:
            fmt = replace(fmt, corner_rx_mm=item.corner_mm, corner_ry_mm=item.corner_mm)
        source = self._preview_source(shot) if preview else (shot.working if dpi < 200 else shot.image)
        used_dpi = self._preview_dpi(shot) if preview else dpi
        pose = (item.zoom, item.shift_x, item.shift_y, item.angle, item.eye_t)
        return self._render(shot, source, used_dpi, item.corner, item.grayscale, fmt=fmt, pose=pose)

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
            cut_width_mm=settings.cut_width_mm or 0.1,
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
            cut_width_mm=current.cut_width_mm,
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
        self.doc.cut_width_mm = float(self.cut_width.value())
        self.settings.cut_width_mm = self.doc.cut_width_mm
        self.doc.cut_marks = self.doc.cut_style != "none"
        self.doc.snap = self.snap_box.isChecked()
        for page in self.pages:
            page.snap = self.doc.snap
            page.cut_style = self.doc.cut_style
            page.cut_length_mm = self.doc.cut_length_mm
            page.cut_width_mm = self.doc.cut_width_mm
            page.cut_marks = self.doc.cut_marks
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
            round(ready.zoom, 3),
            round(ready.shift_x, 2),
            round(ready.shift_y, 2),
            round(ready.angle, 2),
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
            round(ready.eye_t, 3),
            ready.format_id,
            ready.corner,
            ready.grayscale,
            round(ready.corner_mm, 2),
            round(fmt.width_mm, 2),
            round(fmt.height_mm, 2),
            round(fmt.head_height_mm, 2),
            round(fmt.chin_bottom_mm, 2),
            fmt.background,
            self._background_for(fmt),
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
            pose = (ready.zoom, ready.shift_x, ready.shift_y, ready.angle, ready.eye_t)
            image = self._render(shot, shot.working, dpi, ready.corner, ready.grayscale, fmt=fmt, pose=pose)
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
            QMessageBox.information(self, "Лист", "На листе нет кадров. Перетащите готовую фотографию из списка слева.")
            return
        folder = self._archive_dir()
        number = next_number(folder)
        _photo_name, sheet_name = archive_names(number, "лист")
        chosen, selected = QFileDialog.getSaveFileName(
            self,
            "Сохранить как",
            str(folder / Path(sheet_name).with_suffix(".pdf")),
            "PDF (*.pdf);;JPEG (*.jpg *.jpeg)",
        )
        if not chosen:
            return
        path = Path(chosen)
        pdf = _sheet_file_is_pdf(path, selected)
        if pdf and path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        elif not pdf and path.suffix.lower() not in {".jpg", ".jpeg"}:
            path = path.with_suffix(".jpg")
        dpi = self.settings.printer_dpi or EXPORT_DPI
        if pdf:
            self.pages[self.page_index] = self.doc
            pages = [page for page in self.pages if any(item.kind == "photo" for item in page.items)]
            write_pdf([self._compose_sheet(dpi, page) for page in pages], path, dpi, self.settings.jpeg_sheet)
        else:
            self._write_jpeg(self._compose_sheet(dpi), path, dpi, self.settings.jpeg_sheet, 0)
        self._record(number)
        self.statusBar().showMessage(f"Лист {path}")

    def _known_printers(self) -> list[str]:
        names = winprint.printer_names()
        if names:
            return names
        from PySide6.QtPrintSupport import QPrinterInfo

        return [name for name in QPrinterInfo.availablePrinterNames() if name]

    def _fill_printers(self) -> None:
        names = self._known_printers()
        saved = self.settings.printer_name.strip()
        preferred = saved if saved in names else (winprint.default_printer() or (names[0] if names else ""))
        self.printer_box.blockSignals(True)
        self.printer_box.clear()
        self.printer_box.addItem("В файл", "file")
        for name in names:
            self.printer_box.addItem(name, name)
        if not names or saved == "file":
            self.printer_box.setCurrentIndex(0)
        else:
            index = self.printer_box.findData(preferred)
            self.printer_box.setCurrentIndex(index if index >= 0 else 0)
        self.printer_box.setEnabled(True)
        self.printer_box.blockSignals(False)
        self._sync_output_button()

    def _sync_output_button(self) -> None:
        to_file = self.printer_box.currentData() == "file"
        self.printer_gear.setEnabled(not to_file)
        copies = getattr(self, "print_copies", None)
        if copies is not None:
            copies.setEnabled(not to_file)
        button = self.print_button
        if button is None:
            return
        button.setEnabled(True)
        button.setToolTip("")
        if to_file:
            button.setText("Сохранить лист")
            button.setIcon(self._print_icon("save"))
        else:
            button.setText("Печать")
            button.setIcon(self._print_icon("print"))

    def _output_sheet(self) -> None:
        if self.printer_box.currentData() == "file":
            self.save_sheet()
            return
        self.print_sheet()

    def _print_copies_changed(self, value: int) -> None:
        self.settings.print_copies = max(1, int(value))
        self._remember()

    def _sheet_copies(self) -> int:
        box = getattr(self, "print_copies", None)
        if box is None or not box.isEnabled():
            return 1
        return max(1, int(box.value()))

    def _printer_picked(self, _index: int) -> None:
        name = str(self.printer_box.currentData() or "")
        self.settings.printer_name = name
        self._sync_output_button()
        self._remember()

    def _configure_printer(self) -> None:
        name = str(self.printer_box.currentData() or "")
        if not name:
            return
        if sys.platform == "win32":
            data = winprint.configure(name, int(self.winId()))
            if data:
                winprint.save_devmode(name, data)
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(name)
        QPrintDialog(printer, self).exec()

    def _print_with_qt(self, name: str, sheets: list, dpi: int) -> str | None:
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(name)
        copies = self._sheet_copies()
        if printer.supportsMultipleCopies():
            printer.setCopyCount(copies)
            copies = 1
        painter = QPainter()
        if not painter.begin(printer):
            return "Принтер не принял задание"
        try:
            for _copy in range(copies):
                for index, sheet in enumerate(sheets):
                    if (_copy or index) and not printer.newPage():
                        return "Принтер не принял следующую страницу"
                    image = pil_to_pixmap(self._compose_sheet(dpi, sheet)).toImage()
                    rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                    painter.drawImage(rect, image)
        finally:
            painter.end()
        return None

    def print_sheet(self) -> None:
        self.pages[self.page_index] = self.doc
        sheets = [page for page in self.pages if any(item.kind == "photo" for item in page.items)]
        if not sheets:
            QMessageBox.information(self, "Печать", "На листе нет кадров.")
            return
        printer_name = str(self.printer_box.currentData() or "")
        if printer_name == "file":
            self.save_sheet()
            return
        if not printer_name:
            self.statusBar().showMessage("В системе нет принтеров")
            return
        dpi = self.settings.printer_dpi or EXPORT_DPI
        if sys.platform == "win32":
            pages = []
            for sheet in sheets:
                image = pil_to_pixmap(self._compose_sheet(dpi, sheet)).toImage().convertToFormat(QImage.Format.Format_RGB32)
                bits = image.constBits()
                raw = bits.tobytes() if isinstance(bits, memoryview) else bytes(bits)
                pages.append((image.width(), image.height(), image.bytesPerLine(), raw))
            error = winprint.print_images(printer_name, pages, self._sheet_copies())
        else:
            error = self._print_with_qt(printer_name, sheets, dpi)
        if error:
            QMessageBox.warning(self, "Печать", error)
            return
        folder = self._archive_dir()
        self._record(next_number(folder))
        if self.clear_after_box.isChecked():
            self._clear_sheet()
