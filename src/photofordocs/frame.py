"""Размеры кадра: формат, лапки и рамка."""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import QDir, QEvent, QFileSystemWatcher, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QDrag, QGuiApplication, QIcon, QImage, QImageReader, QKeySequence, QPalette, QPixmap, QPolygon, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
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
from photofordocs.formats import Format, import_photolab_file, load_formats
from photofordocs.imageops import (
    detect_face,
    detected_eye_t,
    downscale,
    contain_guides,
    corner_mode_from_format,
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
    place_crop,
    matte_to_alpha,
    move_middle,
    person_matte,
    pose_for_ends,
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
from photofordocs.sheet import PAPERS, render_document
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

class FrameMixin:
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
        layout.addWidget(self._frame_preview_box())
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

    def _format_row(self, grid: QGridLayout, row: int, title: str, spin: QDoubleSpinBox) -> QLabel:
        spin.setSuffix("")
        spin.setFixedWidth(self._spin_width("260.0"))
        spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label = QLabel(title)
        grid.addWidget(label, row, 0)
        grid.addWidget(spin, row, 1, 1, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

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
        self.corner = QComboBox()
        self.corner.addItem("Без уголка", "none")
        self.corner.addItem("Круглый справа", "round-right")
        self.corner.addItem("Круглый слева", "round-left")
        self.corner.addItem("Прямой справа", "straight-right")
        self.corner.addItem("Прямой слева", "straight-left")
        self.corner.setToolTip("Уголок ставится снизу. Прямой — треугольник, круглый — четверть круга из угла.")
        self.corner_radius = self._mm_box(0.5, 40, 18)
        self.corner_radius.setFixedWidth(self._spin_width("260.0"))
        self.corner_radius.setToolTip("Радиус уголка, размер среза от угла кадра, в миллиметрах.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        size = QHBoxLayout()
        size.setContentsMargins(0, 0, 0, 0)
        size.setSpacing(6)
        for title, spin in (("Ширина", self.fmt_width), ("Высота", self.fmt_height)):
            spin.setSuffix("")
            spin.setFixedWidth(self._spin_width("260.0"))
            spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            size.addWidget(QLabel(title))
            size.addWidget(spin)
            if title == "Ширина":
                size.addSpacing(8)
        size.addStretch(1)
        grid.addLayout(size, 0, 0, 1, 2)
        self._format_row(grid, 1, "Верх. поле", self.fmt_top)
        self.fmt_bottom_label = self._format_row(grid, 2, "Нижн. поле", self.fmt_bottom)
        self.fmt_head_label = self._format_row(grid, 3, "Голова", self.fmt_head)
        corner_row = QHBoxLayout()
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(6)
        corner_row.addWidget(self.corner, 1)
        corner_row.addWidget(self.corner_radius)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(grid)
        layout.addLayout(corner_row)
        layout.addWidget(self.ready_gray)
        box.setMinimumWidth(220)
        return box

    def _frame_preview_box(self) -> QGroupBox:
        box = QGroupBox("Готовый кадр")
        layout = QVBoxLayout(box)
        self.format_preview = QLabel("Нет кадра")
        self.format_preview.setObjectName("framepreview")
        self.format_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.format_preview.setMinimumHeight(180)
        self.format_preview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.format_preview)
        self.head_share = QLabel("Голова —")
        self.head_share.setWordWrap(True)
        self.head_share.setToolTip(
            "Доля высоты кадра между верхней и нижней лапкой. "
            "Требование «не менее 70%» считают так же: голова занимает столько полос, "
            "если высоту кадра разделить на десять равных частей."
        )
        layout.addWidget(self.head_share)
        self.frame_overhang = QLabel("")
        self.frame_overhang.setWordWrap(True)
        layout.addWidget(self.frame_overhang)
        return box

    def _wire_formats(self) -> None:
        self.format_box.currentIndexChanged.connect(self._on_format_picked)
        for box in (self.fmt_width, self.fmt_height, self.fmt_top, self.fmt_bottom):
            box.valueChanged.connect(self._format_fields_edited)
        self.corner.currentIndexChanged.connect(self._corner_picked)
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
        shot.zoom, shot.shift_x, shot.shift_y, shot.angle, shot.eye_t = pose_for_ends(
            shot.working.size, shot.face, fmt, shot.crown, shot.chin, self.allow_rotate, shot.eye_line
        )
        shot.guides_manual = True
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
        # Рамка сразу стоит по лапкам. Сдвиг «чтобы влезла» прятал вылет и
        # показывал в превью другой кадр, пока лапку не толкнут вручную.
        shot.zoom, shot.shift_x, shot.shift_y = 1.0, 0.0, 0.0
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
        self.fmt_bottom_label.setVisible(show_bottom)
        self.fmt_head_label.setText("Голова" if base.two_points else "Глаза — подбородок")
        self.fmt_head.setToolTip(
            "Расстояние от макушки до подбородка."
            if base.two_points
            else "Расстояние от линии глаз до подбородка. От него берётся крупность кадра."
        )
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
        mode = corner_mode_from_format(base)
        index = self.corner.findData(mode)
        self.corner.setCurrentIndex(index if index >= 0 else 0)
        self.corner_radius.setValue(base.corner_rx_mm if base.corner_rx_mm > 0 else 18.0)
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

    def _corner_picked(self, _index: int) -> None:
        if self._format_lock:
            return
        self.refresh()

    def _corner_radius_edited(self, _value: float) -> None:
        if self._format_lock:
            return
        self.refresh()

    def _wants_gray(self) -> bool:
        return not self.ready_gray.isChecked()

    def _corner_name(self) -> str:
        return str(self.corner.currentData() or "none")

    def _allow_rotate(self, value: bool) -> None:
        self.allow_rotate = value
        self._remember()
        shot = self.current_shot()
        if shot is not None and not value:
            shot.angle = 0.0
            self.refresh()

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
            self._show_overhang(None, None)
            self.source.set_frame(QPixmap(), None, None)
            self._show_finished()
            return
        crop = self._crop(shot)
        guide = self._shot_guide_ratios(shot, crop)
        self._show_head_share(shot, crop, guide)
        self._show_overhang(shot, crop)
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
        label = getattr(self, "format_preview", None)
        if label is None:
            return
        image = self._render_finished() if hasattr(self, "_render_finished") else None
        if image is None:
            label.setPixmap(QPixmap())
            label.setText("Нет кадра")
            return
        label.setText("")
        label.setPixmap(
            pil_to_pixmap(image).scaled(
                200,
                260,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _show_overhang(self, shot: Shot | None, crop) -> None:
        """Под превью: строка только если рамка вышла за край снимка."""
        note = getattr(self, "frame_overhang", None)
        if note is None:
            return
        if shot is None or crop is None or crop.height < 1:
            note.setText("")
            note.setStyleSheet("")
            return
        width, height = shot.working.size
        angle = math.radians(shot.angle if self.allow_rotate else 0.0)
        cos, sin = math.cos(angle), math.sin(angle)
        cx = crop.left + crop.width / 2
        cy = crop.top + crop.height / 2
        xs: list[float] = []
        ys: list[float] = []
        for dx, dy in (
            (-crop.width / 2, -crop.height / 2),
            (crop.width / 2, -crop.height / 2),
            (crop.width / 2, crop.height / 2),
            (-crop.width / 2, crop.height / 2),
        ):
            xs.append(cx + dx * cos - dy * sin)
            ys.append(cy + dx * sin + dy * cos)
        over = max(0.0, -min(xs), max(xs) - width, -min(ys), max(ys) - height)
        if over < 0.5:
            note.setText("")
            note.setStyleSheet("")
            return
        note.setText("Рамка выходит за снимок")
        note.setStyleSheet("color: #b42318; font-weight: 600;")

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

    def _pose_for_format(self, shot: Shot, fmt: Format) -> tuple[float, float, float, float, float]:
        """Текущий формат берёт рамку с экрана. Остальные собираются вокруг тех же лапок."""
        if fmt.id == self.format_box.currentData():
            return shot.zoom, shot.shift_x, shot.shift_y, shot.angle, shot.eye_t
        if shot.crown is None or shot.chin is None:
            return shot.zoom, shot.shift_x, shot.shift_y, shot.angle, shot.eye_t
        return pose_for_ends(
            shot.working.size, shot.face, fmt, shot.crown, shot.chin, self.allow_rotate, shot.eye_line
        )

    def _add_format_ready(self, shot: Shot, fmt: Format) -> None:
        zoom, shift_x, shift_y, angle, eye_t = self._pose_for_format(shot, fmt)
        self._seq += 1
        self.ready.append(
            ReadyItem(
                f"r{self._seq}",
                str(shot.path),
                fmt.id,
                1,
                self._corner_name(),
                self._wants_gray(),
                float(self.corner_radius.value()),
                zoom=zoom,
                shift_x=shift_x,
                shift_y=shift_y,
                angle=angle,
                eye_t=eye_t,
            )
        )

    def _to_ready(self, switch: bool = True) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        self._add_format_ready(shot, self.current_format())
        self._fill_ready()
        if switch:
            self._set_mode("ready")
