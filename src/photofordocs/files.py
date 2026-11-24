"""Дерево файлов, папки и миниатюры стойки."""

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

class FilesMixin:
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
        shot.matte = None
        shot.matte_of = 0
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
        folders = (
            ("expand", "Развернуть ветки", self._expand_files),
            ("collapse", "Свернуть ветки", self._collapse_files),
            ("home", "Главная папка базы", self._open_archive_root),
            ("today", "Папка месяца", self._open_month),
            ("explorer", "Открыть папку в проводнике", self._reveal_folder),
        )
        grid = QGridLayout()
        grid.setSpacing(2)

        def place(kind: str, tip: str, handler, row: int, column: int) -> QToolButton:
            button = QToolButton()
            button.setObjectName("filetool")
            button.setAutoRaise(False)
            button.setIcon(self._file_icon(kind))
            button.setIconSize(QSize(20, 20))
            button.setFixedSize(26, 26)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            grid.addWidget(button, row, column)
            return button

        for index, spec in enumerate(folders):
            place(*spec, 0, index)
        key, reverse = getattr(self, "_file_order", ("name", False))
        self._sort_held = {
            "name": reverse if key == "name" else False,
            "date": reverse if key == "date" else True,
        }
        self._sort_name_button = place("name-up", "", lambda: self._toggle_sort("name"), 0, 6)
        self._sort_date_button = place("date-down", "", lambda: self._toggle_sort("date"), 0, 7)
        for button in (self._sort_name_button, self._sort_date_button):
            button.setCheckable(True)
        self._sync_sort_buttons()
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        grid.addWidget(line, 0, 5)
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
        splitter = getattr(self, "splitter", None)
        right = getattr(self, "right", None)
        if splitter is not None and right is not None and right.isVisible():
            sizes = [int(item) for item in splitter.sizes()]
            if len(sizes) == 3 and sizes[0] >= 180 and sizes[2] >= 260:
                self.settings.file_panel_width = sizes[0]
                self.settings.right_panel_width = sizes[2]
        self._capture_workplace()
        save_settings(self.settings)

    def _restore_panel_widths(self) -> None:
        """Ширины ставятся после показа: до этого разделитель ещё не знает размер окна."""
        splitter = getattr(self, "splitter", None)
        if splitter is None or not self.right.isVisible():
            return
        sizes = [int(item) for item in splitter.sizes()]
        if len(sizes) != 3:
            return
        left = int(self.settings.file_panel_width)
        right = int(self.settings.right_panel_width)
        if left < 180:
            left = sizes[0]
        if right < 260:
            right = sizes[2]
        if left == sizes[0] and right == sizes[2]:
            return
        center = max(200, sum(sizes) - left - right)
        splitter.setSizes([left, center, right])

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
            missing = [shot.path for shot in self.shots if shot.path.parent == self.folder_root and not shot.path.is_file()]
            if missing:
                self._forget_sources(set(missing))

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
                self.files.addTopLevelItem(node)
                if node.childCount():
                    node.setExpanded(True)
                if selected and (str(path) == selected or any(saved["path"] == selected for saved in saved_rows)):
                    chosen = node if str(path) == selected else next(node.child(i) for i in range(node.childCount()) if node.child(i).data(0, Qt.ItemDataRole.UserRole) == selected)
                    self.files.setCurrentItem(chosen)
        finally:
            self._reveal_expand = True
        self._schedule_thumbs()

    def _reveal_saved(self, source: Path, saved: Path) -> None:
        """После сохранения открыть ветку снимка и показать новую строку кадра."""
        node = self._thumb_items.get(str(source))
        child = self._thumb_items.get(str(saved))
        self._reveal_expand = False
        try:
            if node is not None and node.childCount() and not node.isExpanded():
                node.setExpanded(True)
        finally:
            self._reveal_expand = True
        target = child or node
        if target is not None:
            self.files.scrollToItem(target, QAbstractItemView.ScrollHint.EnsureVisible)

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
        self._load_source(Path(str(raw)))
        self._set_mode("size")

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

    def _toggle_sort(self, key: str) -> None:
        """Тот же столбец меняет направление, другой столбец включает свой последний порядок."""
        current, _reverse = self._file_order
        direction = not self._sort_held[key] if current == key else self._sort_held[key]
        self._sort_files(key, direction)

    def _sync_sort_buttons(self) -> None:
        name_button = getattr(self, "_sort_name_button", None)
        date_button = getattr(self, "_sort_date_button", None)
        if name_button is None or date_button is None:
            return
        key, _reverse = self._file_order
        tips = {
            ("name", False): "По имени, по возрастанию",
            ("name", True): "По имени, по убыванию",
            ("date", False): "По дате, старые сверху",
            ("date", True): "По дате, новые сверху",
        }
        for button, column in ((name_button, "name"), (date_button, "date")):
            reverse = self._sort_held[column]
            button.setIcon(self._file_icon(f"{column}-{'down' if reverse else 'up'}"))
            button.setToolTip(tips[(column, reverse)])
            button.setChecked(key == column)

    def _sort_files(self, key: str, reverse: bool) -> None:
        self._file_order = (key, reverse)
        if hasattr(self, "_sort_held"):
            self._sort_held[key] = reverse
        self.settings.sort_key = key
        self.settings.sort_reverse = bool(reverse)
        self._sync_sort_buttons()
        self._fill_files()
        self._remember()

    def _rotate_tree_source(self, item: QTreeWidgetItem, degrees: int) -> None:
        """Крутит оригинал из меню, а не тот снимок, который случайно открыт."""
        path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
        shot = next((candidate for candidate in self.shots if candidate.path == path), None)
        if shot is None:
            self._load_source(path)
        elif self.current_shot() is not shot:
            self._select_row(self.shots.index(shot))
            self._mark_tree(path)
        self._rotate_shot(degrees)

    def _edit_tree_source(self, item: QTreeWidgetItem) -> None:
        """Внешний редактор открывает оригинал из строки меню."""
        path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
        editor = self.settings.editor
        if not editor:
            self.statusBar().showMessage("Укажите внешний редактор в настройках")
            return
        if not path.is_file():
            self.statusBar().showMessage("Файл не найден")
            return
        resolved = str(path.resolve())
        if any(shot.path == path for shot in self.shots) and resolved not in self._source_watch.files():
            self._source_watch.addPath(resolved)
        subprocess.Popen([editor, resolved])
        self.statusBar().showMessage(f"{path.name} открыт во внешнем редакторе")

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
        self._forget_sources(gone)
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
            turn.addAction("90°", lambda: self._rotate_tree_source(item, 90))
            turn.addAction("180°", lambda: self._rotate_tree_source(item, 180))
            turn.addAction("270°", lambda: self._rotate_tree_source(item, -90))
            menu.addAction("Внешний редактор", lambda: self._edit_tree_source(item))
            menu.addAction("Переименовать", lambda: self._rename_file(item))
        else:
            menu.addAction("Сохранить в файл…", lambda: self._save_tree_file(item))
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

    def _forget_sources(self, gone: set[Path]) -> None:
        """Снять удалённые оригиналы с холста, с готового кадра и с листов."""
        if not gone:
            return
        names = {str(path) for path in gone}
        removed = [shot for shot in self.shots if shot.path in gone]
        for shot in removed:
            self._bg_cache.pop(id(shot), None)
            self._alpha_cache.pop(id(shot), None)
            self._live_cache.pop(id(shot), None)
            self._shot_stamps.pop(str(shot.path), None)
        self.shots = [shot for shot in self.shots if shot.path not in gone]
        removed_ready = {item.id for item in self.ready if item.source in names}
        self.ready = [item for item in self.ready if item.id not in removed_ready]
        documents = list(self.pages)
        if self.doc not in documents:
            documents.append(self.doc)
        for page in documents:
            page.items = [
                item
                for item in page.items
                if item.kind != "photo" or (item.ready_id not in removed_ready and item.path not in names)
            ]
        for ident in removed_ready:
            self._sheet_pixmaps.pop(ident, None)
        if self.shots:
            self._select_row(min(max(self.index, 0), len(self.shots) - 1))
        else:
            self.index = -1
            self._thumb_source = QPixmap()
            self._thumb_fitted = None
            self.thumb.clear()
            self.refresh()
            self.statusBar().showMessage("Снимок удалён")
        self._refresh_sheet()

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

    def _save_tree_file(self, item: QTreeWidgetItem) -> None:
        """Копия уже сохранённого кадра. Оригинал этим пунктом не сохраняется."""
        self._save_ready_as(
            Path(str(item.data(0, Qt.ItemDataRole.UserRole))),
            str(item.data(0, Qt.ItemDataRole.UserRole + 2) or ""),
        )

    def _save_ready_as(self, path: Path, format_id: str) -> None:
        if not path.is_file():
            self.statusBar().showMessage("Файл кадра не найден")
            return
        try:
            image = open_image(str(path))
        except OSError:
            self.statusBar().showMessage("Кадр не прочитан")
            return
        info = load_ready_info(path)
        fmt = next((item for item in self.formats if item.id == format_id), None)
        if info.get("width_mm") and info.get("height_mm"):
            width, height = float(info["width_mm"]), float(info["height_mm"])
        elif fmt is not None:
            width, height = fmt.width_mm, fmt.height_mm
        else:
            dpi_guess = int(info.get("dpi") or self.settings.export_dpi or 300)
            width = image.width / dpi_guess * 25.4
            height = image.height / dpi_guess * 25.4
        dpi = int(info.get("dpi") or self.dpi_box.value() or self.settings.export_dpi or 300)
        preview = pil_to_pixmap(downscale(image, 600))
        spec = self._ask_export(width, height, dpi, preview)
        if spec is None:
            return
        self._export_image(image, spec, path.with_suffix(".jpg"))
