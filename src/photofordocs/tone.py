"""Готовая фотография: тон, фон, превью и сохранение кадра."""

from __future__ import annotations

import os
import subprocess
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
    ready_copy_path,
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


def _groove(*stops: tuple[str, str]) -> str:
    listed = ", ".join(f"stop:{place} {color}" for place, color in stops)
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {listed})"


# Слева меньшее значение, справа большее. Это подпись инструмента, не цвет окна.
_GROOVE_BRIGHT = _groove(("0", "#2a2a2a"), ("1", "#f5f5f5"))
_GROOVE_CONTRAST = _groove(("0", "#b5b5b5"), ("0.45", "#8d8d8d"), ("0.72", "#f4f4f4"), ("1", "#161616"))
_GROOVE_GAMMA = _groove(("0", "#141414"), ("0.4", "#2e2e2e"), ("0.75", "#e6e6e6"), ("1", "#fafafa"))
_GROOVE_SATURATION = _groove(("0", "#9a9a9a"), ("1", "#d23b2f"))
_GROOVE_SHARP = _groove(("0", "#d5d5d5"), ("0.55", "#c4c4c4"), ("0.72", "#222222"), ("0.8", "#f2f2f2"), ("0.88", "#222222"), ("1", "#f7f7f7"))
_GROOVE_BLACK = _groove(("0", "#111111"), ("1", "#7a7a7a"))
_GROOVE_WHITE = _groove(("0", "#9a9a9a"), ("1", "#ffffff"))
_GROOVE_SENSITIVITY = _groove(("0", "#f4f4f4"), ("1", "#7aa7d4"))
_GROOVE_BRUSH = _groove(("0", "#e8e8e8"), ("1", "#5c6b7a"))


class ToneMixin:
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
        self.tolerance = self._slider(0, 100, sensitivity, self._store_tone, _GROOVE_SENSITIVITY)
        self.bright = self._slider(-100, 100, int(self.settings.tone_brightness), self._store_tone, _GROOVE_BRIGHT)
        self.contrast = self._slider(-100, 100, int(self.settings.tone_contrast), self._store_tone, _GROOVE_CONTRAST)
        self.gamma = self._slider(-100, 100, int(self.settings.tone_gamma), self._store_tone, _GROOVE_GAMMA)
        self.saturation = self._slider(-100, 100, int(self.settings.tone_saturation), self._store_tone, _GROOVE_SATURATION)
        self.sharpness = self._slider(0, 100, int(self.settings.tone_sharpness), self._store_tone, _GROOVE_SHARP)
        self.black = self._slider(0, 80, 0, self._store_tone, _GROOVE_BLACK)
        self.white = self._slider(175, 255, 255, self._store_tone, _GROOVE_WHITE)
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
        color.setObjectName("tonebox")
        color_layout = QVBoxLayout(color)
        color_layout.setSpacing(4)
        band_row = QHBoxLayout()
        band_row.setSpacing(6)
        for index, title in enumerate(("Тени", "Центр", "Блики")):
            radio = QRadioButton(title)
            self.balance_band.addButton(radio, index)
            band_row.addWidget(radio)
            if index == 1:
                radio.setChecked(True)
        self.balance_band.idClicked.connect(lambda _band: self._sync_color_sliders())
        color_layout.addLayout(band_row)
        axes = (
            ("Бирюза", "Бирюзовый слева, красный справа", "#26c6da", "#ef5350"),
            ("Пурпур", "Пурпурный слева, зелёный справа", "#e040fb", "#43a047"),
            ("Жёлтый", "Жёлтый слева, синий справа", "#fdd835", "#1e88e5"),
        )
        for channel, (title, tip, left, right) in enumerate(axes):
            color_layout.addWidget(self._color_axis(channel, title, tip, left, right))
        reset_balance = QPushButton("Сброс по всем каналам")
        reset_balance.clicked.connect(self._reset_balance)
        color_layout.addWidget(reset_balance)
        self.brush = self._slider(0, 200, int(self.settings.brush_px), self._store_tone, _GROOVE_BRUSH)
        other_color = QToolButton()
        other_color.setText("…")
        other_color.setFixedSize(22, 22)
        other_color.setToolTip("Другой цвет")
        other_color.clicked.connect(self._pick_background)
        background = QGroupBox("Фон")
        background.setObjectName("tonebox")
        background_layout = QVBoxLayout(background)
        background_layout.setSpacing(4)
        background_layout.addWidget(self.replace_box)
        methods = QHBoxLayout()
        methods.addWidget(self.fill_edge)
        methods.addWidget(self.fill_figure)
        methods.addStretch(1)
        background_layout.addLayout(methods)
        background_layout.addWidget(self._tone_line("Чувств.", self.tolerance, None, "Чувствительность замены фона"))
        background_layout.addWidget(self._tone_line("Кисть", self.brush, None, "Размер кисти"))
        paint = QHBoxLayout()
        paint.setSpacing(6)
        self.paint_box.setToolTip("Заменить цвет фона")
        self.color_box.setToolTip("Цвет фона")
        paint.addWidget(self.paint_box)
        paint.addWidget(self.color_box, 1)
        paint.addWidget(other_color)
        background_layout.addLayout(paint)
        self.sharpness_box = QComboBox()
        self.sharpness_box.addItem("Нет", 0)
        self.sharpness_box.addItem("Слабая", 30)
        self.sharpness_box.addItem("Средняя", 60)
        self.sharpness_box.addItem("Сильная", 90)
        self.sharpness_box.currentIndexChanged.connect(self._sharpness_picked)
        self.sharpness.valueChanged.connect(lambda _value: self._sync_sharpness_box())
        self._sync_sharpness_box()
        tools = QGroupBox("Инструменты")
        tools.setObjectName("tonebox")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setSpacing(4)
        tools_layout.addWidget(self._tone_line("Яркость", self.bright, 0))
        tools_layout.addWidget(self._tone_line("Контраст", self.contrast, 0))
        tools_layout.addWidget(self._tone_line("Гамма", self.gamma, 0))
        tools_layout.addWidget(self._tone_line("Насыщ.", self.saturation, 0))
        sharp = QHBoxLayout()
        sharp.setSpacing(6)
        sharp.addWidget(self._tone_caption("Резкость"))
        sharp.addWidget(self.sharpness_box, 1)
        sharp_reset = QToolButton()
        sharp_reset.setText("×")
        sharp_reset.setFixedSize(22, 22)
        sharp_reset.setToolTip("Сбросить")
        sharp_reset.clicked.connect(lambda: self.sharpness.setValue(0))
        sharp.addWidget(sharp_reset)
        tools_layout.addLayout(sharp)
        tools_layout.addWidget(self._tone_line("Чёрная", self.black, 0))
        tools_layout.addWidget(self._tone_line("Белая", self.white, 255))
        reset = QPushButton("Сброс всех коррекций")
        reset.clicked.connect(self.reset_corrections)
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

    def _tone_caption(self, title: str, tip: str = "") -> QLabel:
        label = QLabel(title)
        width = max(
            self.fontMetrics().horizontalAdvance(name)
            for name in ("Контраст", "Бирюза", "Пурпур", "Жёлтый", "Чувств.", "Резкость")
        )
        label.setFixedWidth(width + 6)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if tip:
            label.setToolTip(tip)
        return label

    def _tone_line(self, title: str, slider: QSlider, neutral: int | None, tip: str = "") -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._tone_caption(title, tip))
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
        return host

    def _color_axis(self, channel: int, title: str, tip: str, left: str, right: str) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._tone_caption(title, tip))
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
        slider.setToolTip(tip)
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

    def reset_corrections(self) -> None:
        """Ползунки тона в ноль. Рамка, фон и цветовые каналы остаются."""
        shot = self.current_shot()
        if shot is None:
            return
        shot.brightness = shot.contrast = shot.gamma = shot.saturation = shot.sharpness = 0
        shot.black_point, shot.white_point = 0, 255
        self._load_tone(shot)
        self._store_tone()

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

    def _background_for(self, fmt: Format) -> str:
        chosen = self.color_box.currentData()
        return fmt.background if chosen == "format" else str(chosen)

    def _background(self) -> str:
        return self._background_for(self.current_format())

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

    def _cached_matte(self, shot: Shot) -> np.ndarray | None:
        """Силуэт превью. Допуск и кисть его не сбрасывают."""
        token = id(shot.working)
        if shot.matte_of == token:
            return shot.matte
        shot.matte = person_matte(shot.working)
        shot.matte_of = token
        return shot.matte

    def _background_alpha(self, shot: Shot) -> np.ndarray | None:
        """Маска фона по превью. Цвет заливки и ползунки тона её не пересчитывают."""
        if not shot.replace or shot.tolerance <= 0:
            return None
        face = shot.face
        method = self._background_method()
        if method == "figure":
            matte = self._cached_matte(shot)
            if matte is not None:
                alpha = matte_to_alpha(matte, shot.tolerance, self.settings.brush_px, face)
                if alpha is not None:
                    return alpha
        face_key = None if face is None else (round(face.x, 3), round(face.y, 3), round(face.w, 3), round(face.h, 3))
        key = (id(shot.working), round(float(shot.tolerance)), int(self.settings.brush_px), face_key, "edge")
        cached = self._bg_cache.get(id(shot))
        if cached is not None and cached[0] == key:
            return cached[1]
        alpha = background_alpha(shot.working, shot.tolerance, self.settings.brush_px, face, "edge")
        self._bg_cache[id(shot)] = (key, alpha)
        return alpha

    def _alpha_for_image(self, shot: Shot, image: Image.Image) -> np.ndarray | None:
        """Маска превью, растянутая до исходника. Сеть по полному файлу не гоняется."""
        alpha = self._background_alpha(shot)
        if alpha is None:
            return None
        if alpha.shape[0] == image.height and alpha.shape[1] == image.width:
            return alpha
        mask = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), "L")
        mask = mask.resize((image.width, image.height), Image.Resampling.BILINEAR)
        return np.asarray(mask).astype(np.float32) / 255.0

    def _render(
        self,
        shot: Shot,
        image: Image.Image,
        dpi: int,
        corner: str = "none",
        grayscale: bool = False,
        prepared: bool = False,
        fmt: Format | None = None,
        pose: tuple[float, float, float, float, float] | None = None,
    ) -> Image.Image:
        fmt = self.current_format() if fmt is None else fmt
        zoom, shift_x, shift_y, angle, eye_t = (
            (shot.zoom, shot.shift_x, shot.shift_y, shot.angle, shot.eye_t) if pose is None else pose
        )
        crop = place_crop(shot.working.size, shot.face, fmt, zoom, shift_x, shift_y, eye_t=eye_t)
        scale_x = image.width / shot.working.width
        scale_y = image.height / shot.working.height
        crop = type(crop)(crop.left * scale_x, crop.top * scale_y, crop.width * scale_x, crop.height * scale_y)
        source_alpha = None if prepared or not shot.replace else self._alpha_for_image(shot, image)
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
            background=self._background_for(fmt),
            gamma=shot.gamma,
            saturation=shot.saturation,
            sharpness=shot.sharpness,
            black_point=shot.black_point,
            white_point=shot.white_point,
            balance=self._balance(),
            corner=corner,
            angle=angle,
            brush=self.settings.brush_px,
            resample=Image.Resampling.BILINEAR if prepared else Image.Resampling.BICUBIC,
            face=shot.face,
            method=self._background_method(),
            paint=self._paint_background(),
            source_alpha=source_alpha,
        )

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

    def _render_finished(self):
        """Готовый кадр в экранном размере: та же обрезка, тон и уголок, что на вкладке фотографии."""
        shot = self.current_shot()
        if shot is None:
            return None
        base = self._live_base(shot)
        preview = Image.fromarray(base, "RGB")
        return render_photo(
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
            prepared_alpha=self._live_alpha(shot),
            already_cropped=True,
            method=self._background_method(),
            paint=self._paint_background(),
        )

    def _show_finished(self, smooth: bool = True) -> None:
        image = self._render_finished()
        if image is None:
            self._finished_image = None
            self.finished.set_photo(QPixmap(), smooth)
            return
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

    def _tolerance_value(self) -> float:
        threshold = self.settings.ground_threshold
        if 0 < threshold <= 20:
            return min(90.0, threshold * 10)
        return float(self.tolerance.value())

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
        chosen = self._confirm_ready_path(path)
        if chosen is None:
            return
        path, copy_number = chosen
        label = fmt.name if copy_number == 0 else f"{fmt.name} {copy_number}"
        self._write_jpeg(image, path, dpi, self.settings.jpeg_photo, fmt.max_file_kb)
        write_ready_info(
            path,
            {
                "source": shot.path.name,
                "format_id": fmt.id,
                "name": label,
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
        self._reveal_saved(shot.path, path)
        self._last_saved = str(path)
        number = next_number(self._archive_dir())
        self._record(number)
        self.statusBar().showMessage(f"Сохранено {path}")

    def _confirm_ready_path(self, path: Path) -> tuple[Path, int] | None:
        """Куда писать кадр: тот же файл или сосед с номером. None — отмена.

        Второе число — 0, если имя формата остаётся как есть, иначе 2, 3 и дальше.
        """
        if not path.exists() or not self.settings.confirm_overwrite:
            return path, 0
        copy, number = ready_copy_path(path)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Сохранить фото")
        box.setText(f"Файл {path.name} уже есть.")
        box.setInformativeText(f"Рядом он сохранится как {copy.name}.")
        replace = box.addButton("Заменить", QMessageBox.ButtonRole.DestructiveRole)
        nearby = box.addButton("Сохранить рядом", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        nearby.setDefault(True)
        box.setEscapeButton(cancel)
        box.exec()
        chosen = box.clickedButton()
        if chosen is nearby:
            return copy, number
        if chosen is replace:
            return path, 0
        return None

    def save_photo_as(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        fmt = self.current_format()
        dpi = int(self.dpi_box.value()) or int(self.settings.export_dpi) or 300
        preview = self.finished.pixmap if self.finished.pixmap is not None else QPixmap()
        spec = self._ask_export(fmt.width_mm, fmt.height_mm, dpi, preview)
        if spec is None:
            return
        image = self._with_profile(self._render(shot, shot.image, spec.dpi, self._corner_name(), self._wants_gray()))
        self._export_image(image, spec, shot.path.with_suffix(".jpg"))

    def _ask_export(self, width_mm: float, height_mm: float, dpi: int, preview: QPixmap) -> object:
        dialog = SaveFileDialog(
            self,
            width_mm,
            height_mm,
            dpi,
            int(self.settings.save_max_kb) or 48,
            self.settings.save_limit_size,
            self.settings.save_limit_bytes,
            self.settings.save_open_folder,
            preview,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        spec = dialog.spec()
        self.settings.save_limit_size = spec.limit_size
        self.settings.save_limit_bytes = spec.limit_bytes
        self.settings.save_open_folder = spec.open_folder
        self.settings.save_max_kb = spec.max_kb
        self._remember()
        return spec

    def _export_image(self, image: Image.Image, spec, suggested: Path) -> None:
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Сохранить в файл",
            str(suggested),
            "JPEG (*.jpg *.jpeg)",
        )
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() not in {".jpg", ".jpeg"}:
            destination = destination.with_suffix(".jpg")
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
