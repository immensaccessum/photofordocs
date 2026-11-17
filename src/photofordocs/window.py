"""Окно стойки: размеры, готовая фотография, лист печати."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageCms
from PySide6.QtCore import QDir, QFileSystemWatcher, QMimeData, QPoint, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDrag, QGuiApplication, QImage, QKeySequence, QPageSize, QPainter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
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
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QSpinBox,
    QDoubleSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from photofordocs.canvas import READY_MIME, PrintCanvas, SourceCanvas
from photofordocs.clothes import all_clothes, save_user_png
from photofordocs.formats import Format, delete_format, import_photolab_file, load_formats, save_format_order, save_user_format
from photofordocs.imageops import (
    detect_face,
    downscale,
    contain_guides,
    frame_points,
    histogram,
    open_image,
    ZOOM_MAX,
    ZOOM_MIN,
    place_crop,
    move_middle,
    pose_from_ends,
    render_photo,
    zoom_shift_for_box,
)
from photofordocs.session import (
    PAPER_MM,
    ReadyItem,
    Settings,
    SheetDoc,
    SheetItem,
    add_journal,
    archive_names,
    day_folder,
    day_total,
    load_ready_info,
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
    append_photos,
    photo_too_big,
    report_text,
    save_scheme,
    save_settings,
    send_report,
    SCHEMES_DIR,
)
from photofordocs.sheet import PAPERS, render_document

PREVIEW_SIDE = 1400
EXPORT_DPI = 300
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


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
    replace: bool = True
    clothes: str = ""
    angle: float = 0.0
    eye_t: float = 0.55


class FinishedView(QWidget):
    """Закладка «Готовая фотография»: уже обрезанный кадр, как его увидит оператор."""

    def __init__(self) -> None:
        super().__init__()
        self._pix = QPixmap()

    def set_photo(self, pixmap: QPixmap) -> None:
        self._pix = pixmap
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#cfcfcf"))
        if self._pix.isNull():
            painter.setPen(QColor("#555"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Откройте снимок")
            painter.end()
            return
        target = self.size() - QSize(48, 48)
        fitted = self._pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((self.width() - fitted.width()) // 2, (self.height() - fitted.height()) // 2, fitted)
        painter.end()


class HistogramDialog(QDialog):
    def __init__(self, image: Image.Image, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Гистограмма")
        self.image = image
        self.channel = QComboBox()
        for key, title in (("luma", "Яркость"), ("red", "Красный"), ("green", "Зелёный"), ("blue", "Синий")):
            self.channel.addItem(title, key)
        self.clip = QCheckBox("Срезать пики")
        self.view = QLabel()
        self.channel.currentIndexChanged.connect(self._draw)
        self.clip.toggled.connect(self._draw)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Канал"))
        row.addWidget(self.channel)
        row.addWidget(self.clip)
        layout.addLayout(row)
        layout.addWidget(self.view)
        self._draw()

    def _draw(self) -> None:
        counts = histogram(self.image, str(self.channel.currentData()), self.clip.isChecked())
        peak = max(counts) or 1
        pixmap = QPixmap(512, 160)
        pixmap.fill(QColor("white"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#222"))
        for index, value in enumerate(counts):
            height = int(140 * value / peak)
            painter.drawLine(index * 2, 150, index * 2, 150 - height)
        painter.end()
        self.view.setPixmap(pixmap)


class FormatDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Свой формат")
        self.name = QLineEdit()
        self.width = QSpinBox()
        self.height = QSpinBox()
        self.head = QSpinBox()
        self.chin = QSpinBox()
        for box, low, high, value in ((self.width, 15, 120, 35), (self.height, 15, 160, 45), (self.head, 10, 120, 32), (self.chin, 1, 40, 7)):
            box.setRange(low, high)
            box.setValue(value)
            box.setSuffix(" мм")
        form = QFormLayout(self)
        form.addRow("Название", self.name)
        form.addRow("Ширина", self.width)
        form.addRow("Высота", self.height)
        form.addRow("Высота головы", self.head)
        form.addRow("Поле под подбородком", self.chin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def build(self) -> Format | None:
        name = self.name.text().strip()
        if not name or self.head.value() + self.chin.value() >= self.height.value():
            return None
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-") or "custom"
        return Format(f"user-{slug}", name, self.width.value(), self.height.value(), self.head.value(), self.chin.value(), "#FFFFFF", "Свой формат.", False)


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


class SettingsDialog(QDialog):
    """Страницы настроек: расположение, кадр, лист, поиск."""

    def __init__(self, settings: Settings, formats: list[Format], parent=None) -> None:
        super().__init__(parent)
        del formats
        self.setWindowTitle("Настройки программы")
        self.settings = settings
        tabs = QTabWidget()

        place = QWidget()
        place_form = QFormLayout(place)
        self.archive = QLineEdit(settings.archive_root)
        self.sheets = QLineEdit(settings.sheets_folder)
        self.temp = QLineEdit(settings.temp_folder)
        self.editor = QLineEdit(settings.editor)
        self.studio = QLineEdit(settings.studio)
        place_form.addRow("Путь к базе фотографий", self.archive)
        place_form.addRow("Каталог готовых листов", self.sheets)
        place_form.addRow("Каталог временных файлов", self.temp)
        place_form.addRow("Внешний редактор", self.editor)
        place_form.addRow("Название салона", self.studio)

        look = QWidget()
        look_form = QVBoxLayout(look)
        look_form.addWidget(QLabel("Цвет рамки и размер квадратов захвата заданы в окне размеров."))
        look_form.addStretch(1)

        frame = QWidget()
        frame_form = QFormLayout(frame)
        self.white = QDoubleSpinBox()
        self.white.setRange(0, 20)
        self.white.setDecimals(1)
        self.white.setSuffix(" мм")
        self.white.setValue(settings.white_frame_mm)
        self.advert = QLineEdit(settings.advert_text)
        frame_form.addRow("Ширина белой рамки", self.white)
        frame_form.addRow("Рекламный текст", self.advert)

        paper = QWidget()
        paper_form = QFormLayout(paper)
        self.paper = QComboBox()
        for item in PAPERS:
            self.paper.addItem(item.name, item.id)
        found = self.paper.findData(settings.default_paper)
        if found >= 0:
            self.paper.setCurrentIndex(found)
        self.landscape = QCheckBox("Альбомная ориентация листа по умолчанию")
        self.landscape.setChecked(settings.default_landscape)
        paper_form.addRow("Лист по умолчанию", self.paper)
        paper_form.addRow(self.landscape)

        intake = QWidget()
        intake_form = QFormLayout(intake)
        self.watch = QLineEdit(settings.watch_folder)
        intake_form.addRow("Следить за папкой", self.watch)

        search = QWidget()
        search_form = QFormLayout(search)
        self.search = QLineEdit(settings.search_folder)
        search_form.addRow("Дополнительная папка поиска", self.search)

        other = QWidget()
        other_form = QFormLayout(other)
        self.email = QLineEdit(settings.email)
        self.smtp = QLineEdit(settings.smtp_host)
        other_form.addRow("Почта отчёта", self.email)
        other_form.addRow("SMTP-сервер", self.smtp)

        icc_page = QWidget()
        icc_form = QFormLayout(icc_page)
        self.icc = QLineEdit(settings.icc)
        icc_form.addRow("Профиль принтера", self.icc)

        for title, page in (
            ("Расположение", place),
            ("Настройки интерфейса", look),
            ("Кадр", frame),
            ("Размеры листа / Качество", paper),
            ("Добавление фотографий", intake),
            ("Поиск", search),
            ("Другие", other),
            ("ICC", icc_page),
        ):
            tabs.addTab(page, title)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)
        screen = QGuiApplication.primaryScreen()
        height = 440
        if screen is not None:
            height = min(440, int(screen.availableGeometry().height() * 0.7))
        self.resize(560, height)
        self.setMaximumHeight(height)

    def apply(self) -> Settings:
        self.settings.archive_root = self.archive.text().strip() or self.settings.archive_root
        self.settings.sheets_folder = self.sheets.text().strip()
        self.settings.temp_folder = self.temp.text().strip()
        self.settings.editor = self.editor.text().strip()
        self.settings.studio = self.studio.text().strip()
        self.settings.watch_folder = self.watch.text().strip()
        self.settings.white_frame_mm = float(self.white.value())
        self.settings.advert_text = self.advert.text().strip()
        self.settings.default_paper = str(self.paper.currentData())
        self.settings.default_landscape = self.landscape.isChecked()
        self.settings.search_folder = self.search.text().strip()
        self.settings.email = self.email.text().strip()
        self.settings.smtp_host = self.smtp.text().strip()
        self.settings.icc = self.icc.text().strip()
        return self.settings


class FormatCatalog(QDialog):
    """Справочник форматов: список слева, трафарет, уголок и цена справа."""

    def __init__(self, formats: list[Format], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Справочник форматов фотографий")
        self.formats = list(formats)
        self._loading = False
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.name = QLineEdit()
        self.extension = QLineEdit()
        self.hotkey = QComboBox()
        self.hotkey.addItem("", "")
        for key in ("F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"):
            self.hotkey.addItem(key, key)
        self.frame_width = self._mm(15, 200, 35)
        self.frame_height = self._mm(15, 250, 45)
        self.color = QComboBox()
        self.color.addItem("Ч/Б", False)
        self.color.addItem("Цветная", True)
        self.count = QSpinBox()
        self.count.setRange(1, 40)
        self.horizontal = QCheckBox("Располагать на листе горизонтально")
        self.paper = QComboBox()
        self.paper.addItems(["Матовая", "Глянцевая"])
        self.in_menu = QCheckBox("В контекстном меню")
        self.mode = QComboBox()
        self.mode.addItem("По двум точкам")
        self.mode.addItem("По трем точкам (по ГОСТу)")
        self.over = self._mm(0, 80, 4)
        self.under = self._mm(0.5, 80, 7)
        self.over2 = self._mm(0, 80, 4)
        self.face = self._mm(5, 80, 14)
        self.corner = QCheckBox("Уголок")
        self.corner_form = QComboBox()
        self.corner_form.addItems(["Прямой", "Круглый центр внутри", "Круглый центр снаружи", "Круг для печати"])
        self.rx = self._mm(1, 40, 18)
        self.ry = self._mm(1, 40, 18)
        self.left_top = QCheckBox("Сверху слева")
        self.left_bottom = QCheckBox("Снизу слева")
        self.right_top = QCheckBox("Сверху справа")
        self.right_bottom = QCheckBox("Снизу справа")
        self.retouch = QCheckBox("Растушевка")
        self.circle_x = self._mm(0, 80, 0)
        self.circle_y = self._mm(0, 80, 0)
        self.price = QSpinBox()
        self.price.setRange(0, 100000)
        self.price.setSuffix(" ₽")
        form = QWidget()
        fields = QFormLayout(form)
        fields.addRow("Название формата", self.name)
        fields.addRow("Расширение", self.extension)
        fields.addRow("Сочетание клавиш", self.hotkey)
        size = QGroupBox("Размер трафарета")
        size_form = QFormLayout(size)
        size_form.addRow("Ширина", self.frame_width)
        size_form.addRow("Высота", self.frame_height)
        fields.addRow(size)
        fields.addRow("Цветность", self.color)
        fields.addRow("Количество фотографий", self.count)
        fields.addRow(self.horizontal)
        fields.addRow("Тип бумаги", self.paper)
        fields.addRow(self.in_menu)
        fields.addRow("Использовать по умолчанию", self.mode)
        points = QTabWidget()
        two = QWidget()
        two_form = QFormLayout(two)
        two_form.addRow("Верхнее поле", self.over)
        two_form.addRow("Нижнее поле", self.under)
        three = QWidget()
        three_form = QFormLayout(three)
        three_form.addRow("Верхнее поле", self.over2)
        three_form.addRow("Лицевая часть", self.face)
        points.addTab(two, "по 2-м точкам")
        points.addTab(three, "По 3-м точкам")
        fields.addRow(points)
        corner_box = QGroupBox("Уголок")
        corner_form = QFormLayout(corner_box)
        corner_form.addRow(self.corner)
        corner_form.addRow("Форма уголка", self.corner_form)
        corner_form.addRow("Радиус X", self.rx)
        corner_form.addRow("Радиус Y", self.ry)
        place = QGroupBox("Положение уголка")
        place_form = QVBoxLayout(place)
        for box in (self.left_top, self.left_bottom, self.right_top, self.right_bottom):
            place_form.addWidget(box)
        corner_form.addRow(place)
        corner_form.addRow(self.retouch)
        circle = QGroupBox("Координаты центра круга")
        circle_form = QFormLayout(circle)
        circle_form.addRow("Координата X", self.circle_x)
        circle_form.addRow("Координата Y", self.circle_y)
        corner_form.addRow(circle)
        fields.addRow(corner_box)
        fields.addRow("Стоимость фотографии", self.price)
        scroll = QScrollArea()
        scroll.setWidget(form)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addWidget(scroll, 2)
        fresh = QPushButton("Новый")
        fresh.setToolTip("Создать новый формат")
        copy = QPushButton("Копировать")
        copy.setToolTip("Создать новый формат с параметрами текущего")
        save = QPushButton("Внести изменения в формат")
        save.setToolTip("Сохранить изменения формата")
        remove = QPushButton("Удалить текущий")
        up = QPushButton("Вверх")
        down = QPushButton("Вниз")
        close = QPushButton("Закрыть")
        bring = QPushButton("Импорт из оригинала")
        bring.setToolTip("Прочитать formats.ini справочника «Фото на документы»")
        fresh.clicked.connect(self._new)
        copy.clicked.connect(self._copy)
        save.clicked.connect(self._save)
        remove.clicked.connect(self._delete)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        close.clicked.connect(self.accept)
        bring.clicked.connect(self._import_original)
        buttons = QHBoxLayout()
        for button in (fresh, copy, save, remove, up, down, bring):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)
        self._fill_list()
        screen = QGuiApplication.primaryScreen()
        height = 640
        if screen is not None:
            height = min(640, int(screen.availableGeometry().height() * 0.8))
        self.resize(860, height)
        self.setMaximumHeight(height)
        if self.formats:
            self.list.setCurrentRow(0)

    def _mm(self, low: float, high: float, value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(1)
        box.setSingleStep(0.5)
        box.setValue(value)
        box.setSuffix(" мм")
        return box

    def _fill_list(self) -> None:
        self.list.clear()
        for fmt in self.formats:
            self.list.addItem(fmt.name)

    def _current(self) -> Format | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.formats):
            return None
        return self.formats[row]

    def _show(self, row: int) -> None:
        if row < 0 or row >= len(self.formats):
            return
        fmt = self.formats[row]
        self._loading = True
        self.name.setText(fmt.name)
        self.extension.setText(fmt.extension)
        if fmt.hotkey and self.hotkey.findData(fmt.hotkey) < 0:
            self.hotkey.addItem(fmt.hotkey, fmt.hotkey)
        index = self.hotkey.findData(fmt.hotkey)
        self.hotkey.setCurrentIndex(index if index >= 0 else 0)
        self.frame_width.setValue(fmt.width_mm)
        self.frame_height.setValue(fmt.height_mm)
        self.color.setCurrentIndex(1 if fmt.colored else 0)
        self.count.setValue(max(1, fmt.default_count))
        self.horizontal.setChecked(fmt.place_horizontal)
        paper = self.paper.findText(fmt.paper_kind)
        self.paper.setCurrentIndex(paper if paper >= 0 else 0)
        self.in_menu.setChecked(fmt.show_in_menu)
        self.mode.setCurrentIndex(0 if fmt.two_points else 1)
        overhead = fmt.overhead_mm or max(0.0, fmt.height_mm - fmt.head_height_mm - fmt.chin_bottom_mm)
        self.over.setValue(overhead)
        self.under.setValue(fmt.chin_bottom_mm)
        self.over2.setValue(fmt.overhead2_mm or overhead)
        self.face.setValue(fmt.face_height_mm or fmt.head_height_mm * 0.45)
        self.corner.setChecked(fmt.have_corner)
        self.corner_form.setCurrentIndex(min(max(fmt.corner_kind, 0), 3))
        self.rx.setValue(fmt.corner_rx_mm)
        self.ry.setValue(fmt.corner_ry_mm)
        self.left_top.setChecked(bool(fmt.corner_placement & 1))
        self.left_bottom.setChecked(bool(fmt.corner_placement & 2))
        self.right_top.setChecked(bool(fmt.corner_placement & 4))
        self.right_bottom.setChecked(bool(fmt.corner_placement & 8))
        self.retouch.setChecked(fmt.retouch)
        self.circle_x.setValue(fmt.print_cx_mm)
        self.circle_y.setValue(fmt.print_cy_mm)
        parent = self.parent()
        price = int(parent.settings.price_for(fmt.id)) if parent is not None and hasattr(parent, "settings") else 0
        self.price.setValue(price)
        self._loading = False

    def _collect(self, ident: str, note: str, background: str, builtin: bool) -> Format | None:
        width = float(self.frame_width.value())
        height = float(self.frame_height.value())
        under = float(self.under.value())
        overhead = float(self.over.value())
        overhead2 = float(self.over2.value())
        face = float(self.face.value())
        two_points = self.mode.currentIndex() == 0
        top = overhead if two_points else overhead2
        head = height - top - under
        if width <= 0 or height <= 0 or head <= 1 or under <= 0 or head + under >= height:
            QMessageBox.warning(self, "Формат", "Верхнее поле, нижнее поле и высота не сходятся в один кадр.")
            return None
        if face >= head:
            face = round(head * 0.45, 1)
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Формат", "Нужно название формата.")
            return None
        placement = 0
        if self.left_top.isChecked():
            placement |= 1
        if self.left_bottom.isChecked():
            placement |= 2
        if self.right_top.isChecked():
            placement |= 4
        if self.right_bottom.isChecked():
            placement |= 8
        return Format(
            id=ident,
            name=name,
            width_mm=width,
            height_mm=height,
            head_height_mm=round(head, 2),
            chin_bottom_mm=under,
            background=background,
            note=note,
            builtin=builtin,
            have_corner=self.corner.isChecked(),
            corner_kind=self.corner_form.currentIndex(),
            corner_placement=placement,
            corner_rx_mm=float(self.rx.value()),
            corner_ry_mm=float(self.ry.value()),
            show_in_menu=self.in_menu.isChecked(),
            extension=self.extension.text().strip() or "jpg",
            hotkey=str(self.hotkey.currentData() or ""),
            colored=bool(self.color.currentData()),
            default_count=self.count.value(),
            place_horizontal=self.horizontal.isChecked(),
            paper_kind=self.paper.currentText(),
            two_points=two_points,
            overhead_mm=overhead,
            overhead2_mm=overhead2,
            face_height_mm=face,
            retouch=self.retouch.isChecked(),
            print_cx_mm=float(self.circle_x.value()),
            print_cy_mm=float(self.circle_y.value()),
        )

    def _save(self) -> None:
        current = self._current()
        if current is None:
            return
        fmt = self._collect(current.id, current.note, current.background, False)
        if fmt is None:
            return
        save_user_format(fmt)
        row = self.list.currentRow()
        self.formats[row] = fmt
        self.list.item(row).setText(fmt.name)
        self._store_price(fmt.id)
        self._publish()

    def _store_price(self, ident: str) -> None:
        parent = self.parent()
        if parent is None or not hasattr(parent, "settings"):
            return
        parent.settings.prices[ident] = float(self.price.value())
        save_settings(parent.settings)

    def _publish(self) -> None:
        save_format_order([fmt.id for fmt in self.formats])
        parent = self.parent()
        if parent is None or not hasattr(parent, "formats"):
            return
        parent.formats = list(self.formats)
        parent._fill_format_combo(parent.format_box)
        parent._load_format_fields()

    def _new(self) -> None:
        ident = f"user-{len(self.formats) + 1}"
        while any(fmt.id == ident for fmt in self.formats):
            ident += "x"
        self.name.setText("Новый формат")
        fmt = self._collect(ident, "Свой формат.", "#FFFFFF", False)
        if fmt is None:
            return
        save_user_format(fmt)
        self.formats.append(fmt)
        self._fill_list()
        self.list.setCurrentRow(len(self.formats) - 1)
        self._store_price(fmt.id)
        self._publish()

    def _copy(self) -> None:
        current = self._current()
        if current is None:
            return
        ident = f"user-{current.id}"
        while any(fmt.id == ident for fmt in self.formats):
            ident += "-2"
        self.name.setText(current.name + " копия")
        fmt = self._collect(ident, current.note, current.background, False)
        if fmt is None:
            return
        save_user_format(fmt)
        self.formats.append(fmt)
        self._fill_list()
        self.list.setCurrentRow(len(self.formats) - 1)
        self._store_price(fmt.id)
        self._publish()

    def _delete(self) -> None:
        current = self._current()
        row = self.list.currentRow()
        if current is None:
            return
        answer = QMessageBox.question(self, "Удалить формат", f"Удалить «{current.name}»?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_format(current)
        del self.formats[row]
        self._fill_list()
        if self.formats:
            self.list.setCurrentRow(min(row, len(self.formats) - 1))
        self._publish()

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or target < 0 or target >= len(self.formats):
            return
        self.formats[row], self.formats[target] = self.formats[target], self.formats[row]
        self._fill_list()
        self.list.setCurrentRow(target)
        self._publish()

    def _import_original(self) -> None:
        parent = self.parent()
        if parent is None or not hasattr(parent, "import_original_formats"):
            return
        parent.import_original_formats()
        self.formats = list(parent.formats)
        self._fill_list()
        if self.formats:
            self.list.setCurrentRow(0)


class JournalDialog(QDialog):
    def __init__(self, lines, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Журнал")
        self.lines = lines
        self.list = QListWidget()
        copies, amount = day_total(lines)
        self.summary = QLabel(f"Сегодня: {copies} комплектов, {amount:.0f} ₽")
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.list)
        self._fill()
        remove = QPushButton("Удалить выбранную строку")
        remove.clicked.connect(self._remove)
        layout.addWidget(remove)

    def _fill(self) -> None:
        self.list.clear()
        for index, line in enumerate(self.lines):
            item = QListWidgetItem(f"{line.day} {line.at}  {line.number}  {line.formats}  {line.copies} шт  {line.amount:.0f} ₽")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list.addItem(item)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        del self.lines[row]
        self._fill()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Фото на документы")
        self.allow_rotate = False
        self.print_ad = True
        self.formats = load_formats()
        self.settings = load_settings()
        self.journal = load_journal()
        self.shots: list[Shot] = []
        self.folder_root: Path | None = None
        self.index = -1
        self.ready: list[ReadyItem] = []
        self.doc = SheetDoc()
        self.pages: list[SheetDoc] = [self.doc]
        self.page_index = 0
        self._seq = 0
        self._last_saved = ""
        self.clothes = all_clothes()
        self._watcher = None
        self.branches: dict[str, list[dict]] = {}
        self._file_order = ("name", False)
        self._pending_folder: Path | None = None
        self._paper_horizontal = False
        self.quick_tunes: list[dict] = []
        self._build()
        self._menu()
        self._set_mode("size")
        self.statusBar().showMessage("Откройте снимок или папку.")

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        left = QFrame()
        left.setFrameShape(QFrame.Shape.StyledPanel)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        self.drive_box = QComboBox()
        self.drive_box.setToolTip("Диск")
        self.dir_model = QFileSystemModel(self)
        self.dir_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.dir_model.setRootPath("/")
        self.dir_model.directoryLoaded.connect(self._directory_loaded)
        self.folders = QTreeView()
        self.folders.setModel(self.dir_model)
        self.folders.setHeaderHidden(True)
        self.folders.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.folders.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for column in range(1, 4):
            self.folders.hideColumn(column)
        self.folders.clicked.connect(self._folder_clicked)
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
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setMinimumHeight(110)
        self.thumb.setFrameShape(QFrame.Shape.Box)
        left_layout.addWidget(self.drive_box)
        left_layout.addWidget(self.folders, 2)
        left_layout.addWidget(self.files, 3)
        left_layout.addLayout(self._file_tools())
        left_layout.addWidget(self.thumb)
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
        self._finished_image: Image.Image | None = None
        self._mode = "size"
        self.printer = PrintCanvas()
        self.printer.changed.connect(self._sheet_changed)
        self.printer.ready_dropped.connect(self._drop_ready)
        self.printer.status.connect(lambda text: self.statusBar().showMessage(text))
        self.print_host = QWidget()
        host_layout = QVBoxLayout(self.print_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.page_label = QLabel("Лист 1 из 1")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_bar = QWidget()
        page_row = QHBoxLayout(page_bar)
        page_row.setContentsMargins(8, 2, 8, 4)
        previous = QPushButton("←")
        previous.setFixedWidth(36)
        previous.clicked.connect(lambda: self._show_page(self.page_index - 1))
        following = QPushButton("→")
        following.setFixedWidth(36)
        following.clicked.connect(lambda: self._show_page(self.page_index + 1))
        page_row.addStretch(1)
        page_row.addWidget(previous)
        page_row.addWidget(self.page_label)
        page_row.addWidget(following)
        page_row.addStretch(1)
        sheet_column = QWidget()
        sheet_layout = QVBoxLayout(sheet_column)
        sheet_layout.setContentsMargins(0, 0, 0, 0)
        sheet_layout.setSpacing(0)
        sheet_layout.addWidget(self.printer, 1)
        sheet_layout.addWidget(page_bar)
        self.print_split = QSplitter(Qt.Orientation.Vertical)
        self.print_split.setChildrenCollapsible(True)
        self.print_split.addWidget(sheet_column)
        self.print_split.addWidget(self.print_host)
        self.print_split.setStretchFactor(0, 1)
        self.print_split.setSizes([800, 0])
        self.stack = QStackedWidget()
        for widget in (self.source, self.finished, self.print_split):
            self.stack.addWidget(widget)
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
        self.right.setMinimumWidth(220)
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
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #ececec; color: #202020; font-size: 12px; }
            QPushButton { background: #f7f7f7; border: 1px solid #b5b5b5; padding: 3px 6px; }
            QPushButton#step { padding: 4px 10px; }
            QPushButton#step:checked { background: white; font-weight: 600; }
            QTreeWidget, QListWidget { background: white; }
            QFrame { background: #f3f3f3; }
            QSplitter::handle { background: #c4c4c4; }
            QSplitter::handle:horizontal { width: 6px; }
            QMenu { background: #f4f4f4; border: 1px solid #b0b0b0; }
            QMenu::item { padding: 5px 28px 5px 18px; background: transparent; }
            QMenu::item:selected { background: #c5ceda; color: #101010; }
            QMenu::separator { height: 1px; background: #d0d0d0; margin: 4px 8px; }
            """
        )

    def _slider(self, low: int, high: int, value: int, handler) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.valueChanged.connect(handler)
        return slider

    def _scroll(self, page: QWidget) -> QScrollArea:
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _action_grid(self) -> QGridLayout:
        save_photo = QPushButton("Сохранить фото")
        save_photo.clicked.connect(self.save_photo)
        all_formats = QPushButton("Все форматы")
        all_formats.clicked.connect(self._all_formats)
        save_file = QPushButton("Сохранить в файл")
        save_file.clicked.connect(self.save_photo_as)
        print_button = QPushButton("Печать")
        print_button.clicked.connect(self._print_from_panel)
        editor = QPushButton("Редактор")
        editor.clicked.connect(lambda: self._set_mode("ready"))
        external = QPushButton("Внешний редактор")
        external.clicked.connect(self._external_editor)
        grid = QGridLayout()
        for index, button in enumerate((save_photo, all_formats, save_file, print_button, editor, external)):
            button.setStyleSheet("font-size: 11px; padding: 4px 2px;")
            grid.addWidget(button, index // 2, index % 2)
        return grid

    def _fill_format_combo(self, box: QComboBox) -> None:
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(14)
        box.blockSignals(True)
        box.clear()
        for fmt in self.formats:
            box.addItem(f"{fmt.name}  {fmt.summary()}", fmt.id)
        box.blockSignals(False)

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
        self._sizes_layout = layout
        layout.addWidget(self._actions_box())
        layout.addWidget(self._format_params_box())
        layout.addStretch(1)
        return self._scroll(page)

    def _actions_box(self) -> QGroupBox:
        box = QGroupBox("Действия")
        inner = QVBoxLayout(box)
        inner.addLayout(self._action_grid())
        return box

    def _format_params_box(self) -> QGroupBox:
        box = QGroupBox("Параметры формата")
        self.format_params = box
        form = QFormLayout(box)
        self.format_box = QComboBox()
        self._fill_format_combo(self.format_box)
        about = QPushButton("Подробнее о формате")
        about.clicked.connect(self._about_format)
        own = QPushButton("Свой формат")
        own.clicked.connect(self.add_format)
        self.rotate_box = QCheckBox("Разрешить поворот")
        self.rotate_box.setToolTip("Точки рамки можно уводить в сторону, рамка наклоняется и остаётся прямой. Без галочки она вертикальна.")
        self.rotate_box.toggled.connect(self._allow_rotate)
        self.frame_box = QCheckBox("Показать формат")
        self.frame_box.setChecked(True)
        self.frame_box.toggled.connect(lambda _v: self.refresh())
        self.fmt_width = self._mm_box(20, 80, 35)
        self.fmt_height = self._mm_box(25, 90, 45)
        self.fmt_top = self._mm_box(0.5, 25, 3)
        self.fmt_bottom = self._mm_box(0.5, 30, 7)
        self.dpi_box = QSpinBox()
        self.dpi_box.setRange(150, 600)
        self.dpi_box.setValue(EXPORT_DPI)
        self.retouch = QCheckBox("Ретушь")
        self.ready_gray = QCheckBox("Чёрно-белое")
        self.corner = QComboBox()
        self.corner.addItem("Как в формате", "format")
        self.corner.addItem("Прямой слева", "straight-left")
        self.corner.addItem("Прямой справа", "straight-right")
        self.corner.addItem("Круглый слева", "round-left")
        self.corner.addItem("Круглый справа", "round-right")
        self.corner.setToolTip("Левый и правый уголок ставятся снизу. Прямой — треугольник, круглый — четверть круга из угла.")
        form.addRow("Формат", self.format_box)
        form.addRow(about)
        form.addRow(own)
        form.addRow(self.rotate_box)
        form.addRow(self.frame_box)
        form.addRow("Ширина", self.fmt_width)
        form.addRow("Высота", self.fmt_height)
        form.addRow("Разрешение", self.dpi_box)
        form.addRow("Поле сверху", self.fmt_top)
        form.addRow("Поле снизу", self.fmt_bottom)
        form.addRow(self.retouch)
        form.addRow(self.ready_gray)
        form.addRow("Уголок", self.corner)
        return box

    def _finished_side(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._ready_layout = layout
        layout.addWidget(self._actions_box())
        self.replace_box = QCheckBox("Залить фон")
        self.replace_box.setChecked(True)
        self.replace_box.toggled.connect(self._store_tone)
        self.color_box = QComboBox()
        self.color_box.addItem("Как в формате", "format")
        self.color_box.addItem("Белый", "#FFFFFF")
        self.color_box.addItem("Серый", "#E6E6E6")
        self.color_box.addItem("Голубой", "#D6E6F5")
        self.color_box.currentIndexChanged.connect(self._store_tone)
        self.tolerance = self._slider(12, 90, 42, self._store_tone)
        self.bright = self._slider(-100, 100, 0, self._store_tone)
        self.contrast = self._slider(-100, 100, 0, self._store_tone)
        self.gamma = self._slider(-100, 100, 0, self._store_tone)
        self.saturation = self._slider(-100, 100, 0, self._store_tone)
        self.sharpness = self._slider(0, 100, 0, self._store_tone)
        self.black = self._slider(0, 80, 0, self._store_tone)
        self.white = self._slider(175, 255, 255, self._store_tone)
        self.balance = []
        balance_box = QWidget()
        balance_layout = QFormLayout(balance_box)
        for title in ("Тени", "Центр", "Блики"):
            row = []
            line = QHBoxLayout()
            for _channel in ("Г", "П", "Ж"):
                box = QSpinBox()
                box.setRange(-100, 100)
                box.valueChanged.connect(self._store_tone)
                row.append(box)
                line.addWidget(box)
            self.balance.append(tuple(row))
            holder = QWidget()
            holder.setLayout(line)
            balance_layout.addRow(title, holder)
        color = QGroupBox("Цветокоррекция")
        color_layout = QVBoxLayout(color)
        color_layout.addWidget(QLabel("Голубой, пурпурный, жёлтый"))
        color_layout.addWidget(balance_box)
        background = QGroupBox("Замена фона")
        background_form = QFormLayout(background)
        background_form.addRow(self.replace_box)
        background_form.addRow("Цвет фона", self.color_box)
        background_form.addRow("Чувствительность", self.tolerance)
        tools = QGroupBox("Инструменты")
        tools_form = QFormLayout(tools)
        tools_form.addRow("Яркость", self.bright)
        tools_form.addRow("Контраст", self.contrast)
        tools_form.addRow("Гамма", self.gamma)
        tools_form.addRow("Насыщенность", self.saturation)
        tools_form.addRow("Резкость", self.sharpness)
        tools_form.addRow("Чёрная точка", self.black)
        tools_form.addRow("Белая точка", self.white)
        self.clothes_box = QComboBox()
        self.clothes_box.addItem("Без одежды", "")
        for name, _image in self.clothes:
            self.clothes_box.addItem(name, name)
        self.clothes_box.currentIndexChanged.connect(self._store_tone)
        add_clothes = QPushButton("Своя одежда PNG")
        add_clothes.clicked.connect(self._add_clothes)
        tools_form.addRow("Одежда", self.clothes_box)
        tools_form.addRow(add_clothes)
        reset = QPushButton("Сброс всех коррекций")
        reset.clicked.connect(self.reset_frame)
        tools_form.addRow(reset)
        layout.addWidget(color)
        layout.addWidget(background)
        layout.addWidget(tools)
        layout.addStretch(1)
        return self._scroll(page)

    def _print_side(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(page)
        buttons = QVBoxLayout()
        print_button = QPushButton("Печать")
        print_button.clicked.connect(self.print_sheet)
        clear_button = QPushButton("Очистить лист")
        clear_button.clicked.connect(self._clear_sheet)
        save_button = QPushButton("Сохранить лист")
        save_button.clicked.connect(self.save_sheet)
        logo_button = QPushButton("Добавить логотип")
        logo_button.clicked.connect(self._add_logo)
        for button in (print_button, save_button, clear_button, logo_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.sheet_table = ReadyDropTable()
        self.sheet_table.ready_dropped.connect(self._drop_ready)
        self.sheet_table.setHorizontalHeaderLabels(["Файл", "Формат", "Копий", "Сумма"])
        self.sheet_table.verticalHeader().setVisible(False)
        self.sheet_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sheet_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sheet_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sheet_table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.sheet_table, 1)
        self.sheet_total = QLabel("Итого 0 ₽")
        layout.addWidget(self.sheet_total)
        tabs = QTabWidget()
        tabs.addTab(self._print_size_tab(), "Размеры")
        tabs.addTab(self._print_options_tab(), "Параметры")
        tabs.addTab(self._print_blocks_tab(), "Блоки")
        layout.addWidget(tabs)
        self._wire_formats()
        return page

    def _print_size_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.paper = QComboBox()
        for item in PAPERS:
            self.paper.addItem(item.name, item.id)
        self.paper.addItem("Свой размер", "custom")
        paper_index = self.paper.findData(self.settings.default_paper)
        if paper_index >= 0:
            self.paper.setCurrentIndex(paper_index)
        self.paper_w = self._mm_box(50, 400, 100)
        self.paper_h = self._mm_box(50, 500, 150)
        self.portrait = QRadioButton("Книжная")
        self.landscape = QRadioButton("Альбомная")
        self.portrait.setChecked(not self.settings.default_landscape)
        self.landscape.setChecked(self.settings.default_landscape)
        orient = QButtonGroup(page)
        orient.addButton(self.portrait)
        orient.addButton(self.landscape)
        self.margin_top = self._edge_box()
        self.margin_right = self._edge_box()
        self.margin_bottom = self._edge_box()
        self.margin_left = self._edge_box()
        self.paper.currentIndexChanged.connect(self._paper_changed)
        self.paper_w.valueChanged.connect(self._paper_changed)
        self.paper_h.valueChanged.connect(self._paper_changed)
        self.landscape.toggled.connect(self._paper_changed)
        for box in (self.margin_top, self.margin_right, self.margin_bottom, self.margin_left):
            box.valueChanged.connect(self._paper_changed)
        form.addRow("Бумага", self.paper)
        form.addRow("Ширина", self.paper_w)
        form.addRow("Высота", self.paper_h)
        form.addRow("Ориентация", self.portrait)
        form.addRow("", self.landscape)
        form.addRow("Поле сверху", self.margin_top)
        form.addRow("Поле справа", self.margin_right)
        form.addRow("Поле снизу", self.margin_bottom)
        form.addRow("Поле слева", self.margin_left)
        return page

    def _edge_box(self) -> QSpinBox:
        box = QSpinBox()
        box.setRange(0, 40)
        box.setValue(4)
        box.setSuffix(" мм")
        return box

    def _print_options_tab(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)
        self.overlap_box = QCheckBox("Контроль наложений фото")
        self.overlap_box.setChecked(True)
        self.inside_box = QCheckBox("Контроль выхода за поля")
        self.inside_box.setChecked(True)
        self.ask_printer_box = QCheckBox("Показывать диалог принтера")
        self.ask_printer_box.setChecked(True)
        self.footer_box = QCheckBox("Печать рекламного колонтитула")
        self.footer_box.setChecked(False)
        self.footer = QLineEdit(self.settings.advert_text)
        self.footer.setPlaceholderText("Текст колонтитула, пусто — салон и дата")
        self.footer.textChanged.connect(lambda _text: self._refresh_sheet())
        self.clear_after_box = QCheckBox("Очищать после печати")
        self.grid_box = QCheckBox("Сетка")
        self.snap_box = QCheckBox("Прилипание")
        self.snap_box.setChecked(True)
        self.cut_style = QComboBox()
        self.cut_style.addItem("не рисовать", "none")
        self.cut_style.addItem("рамка", "box")
        self.cut_style.addItem("штрих-линии", "dash")
        self.cut_style.addItem("в углах", "corners")
        self.cut_style.setCurrentIndex(3)
        for box in (self.overlap_box, self.inside_box, self.footer_box, self.grid_box, self.snap_box):
            box.toggled.connect(self._paper_changed)
        self.cut_style.currentIndexChanged.connect(self._paper_changed)
        layout.addWidget(self.overlap_box)
        layout.addWidget(self.inside_box)
        layout.addWidget(self.ask_printer_box)
        layout.addWidget(self.footer_box)
        layout.addWidget(self.footer)
        self.logo_after_box = QCheckBox("Размещать логотип сразу за фотографиями")
        self.logo_after_box.toggled.connect(self._paper_changed)
        self.logo_name = QLabel("Файл не выбран")
        self.logo_name.setWordWrap(True)
        remove = QPushButton("Убрать логотип с листа")
        remove.clicked.connect(self._remove_logo)
        layout.addWidget(self.clear_after_box)
        layout.addWidget(self.logo_after_box)
        layout.addWidget(self.logo_name)
        layout.addWidget(remove)
        layout.addWidget(self.grid_box)
        layout.addWidget(self.snap_box)
        layout.addWidget(QLabel("Линии отреза"))
        layout.addWidget(self.cut_style)
        layout.addStretch(1)
        return page

    def _print_blocks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.block_list = QListWidget()
        self.block_list.itemDoubleClicked.connect(lambda _item: self._apply_block())
        buttons = QHBoxLayout()
        create = QPushButton("Новый")
        create.clicked.connect(self._save_scheme)
        apply_button = QPushButton("Применить")
        apply_button.clicked.connect(self._apply_block)
        delete = QPushButton("Удалить")
        delete.clicked.connect(self._delete_block)
        buttons.addWidget(create)
        buttons.addWidget(apply_button)
        buttons.addWidget(delete)
        layout.addWidget(QLabel("Блок — расположение кадров одного клиента."))
        layout.addWidget(self.block_list, 1)
        layout.addLayout(buttons)
        self._reload_blocks()
        return page

    def _wire_formats(self) -> None:
        self.format_box.currentIndexChanged.connect(self._on_format_picked)
        for box in (self.fmt_width, self.fmt_height, self.fmt_top, self.fmt_bottom):
            box.valueChanged.connect(self._format_fields_edited)
        self.corner.currentIndexChanged.connect(lambda _i: self.refresh())
        self.ready_gray.toggled.connect(lambda _v: self.refresh())
        self.retouch.toggled.connect(self._apply_retouch)
        self._load_format_fields()

    def _on_format_picked(self) -> None:
        self._load_format_fields()
        self.refresh()

    def _load_format_fields(self) -> None:
        base = next(item for item in self.formats if item.id == self.format_box.currentData())
        crown = max(0.5, base.height_mm - base.head_height_mm - base.chin_bottom_mm)
        self._format_lock = True
        self.fmt_width.setValue(base.width_mm)
        self.fmt_height.setValue(base.height_mm)
        self.fmt_top.setValue(crown)
        self.fmt_bottom.setValue(base.chin_bottom_mm)
        self._format_lock = False

    def _format_fields_edited(self, _value=None) -> None:
        if self._format_lock:
            return
        self.refresh()

    def _apply_retouch(self, checked: bool) -> None:
        if checked and self.sharpness.value() < 30:
            self.sharpness.setValue(35)
        self.refresh()

    def _corner_name(self) -> str:
        return str(self.corner.currentData() or "none")

    def _reload_blocks(self) -> None:
        if not hasattr(self, "block_list"):
            return
        self.block_list.clear()
        if not SCHEMES_DIR.exists():
            return
        for path in sorted(SCHEMES_DIR.glob("*.yaml")):
            self.block_list.addItem(path.stem)

    def _apply_block(self) -> None:
        item = self.block_list.currentItem()
        if item is None:
            return
        path = SCHEMES_DIR / f"{item.text()}.yaml"
        if path.exists():
            self.doc = load_scheme(path, self.doc)
            self.pages[self.page_index] = self.doc
            self._refresh_sheet()

    def _delete_block(self) -> None:
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
            self.sheet_table.setItem(row, 0, QTableWidgetItem(source))
            self.sheet_table.setItem(row, 1, QTableWidgetItem(fmt.name if fmt else ""))
            copies = QSpinBox()
            copies.setRange(1, 40)
            copies.setValue(count)
            copies.valueChanged.connect(lambda value, ident=ready_id: self._set_sheet_copies(ident, value))
            self.sheet_table.setCellWidget(row, 2, copies)
            amount = self.settings.price_for(ready.format_id) * count if ready else 0
            total += amount
            self.sheet_table.setItem(row, 3, QTableWidgetItem(f"{amount:.0f}"))
        self.sheet_total.setText(f"Итого {total:.0f} ₽")
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
        service.addAction(import_formats)
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
        self.view_vert.setChecked(True)

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
        self.tune_box = QComboBox()
        self.tune_box.setMinimumWidth(140)
        self.tune_box.addItem("Пустые", None)
        self.tune_box.currentIndexChanged.connect(self._apply_quick_tune)
        bar.addWidget(self.tune_box)
        bar.addAction(command("Записать настройку", "Записать текущие коррекции в выбранную настройку", pix.SP_DialogSaveButton, self._update_quick_tune))
        bar.addAction(command("Добавить настройку", "Добавить быструю настройку", pix.SP_DialogYesButton, self._add_quick_tune))
        bar.addAction(command("Удалить настройку", "Удалить выбранную быструю настройку", pix.SP_DialogCancelButton, self._delete_quick_tune))
        bar.addSeparator()
        bar.addAction(about)
        bar.addAction(help_action)
        tool = bar.widgetForAction(formats)
        if tool is not None:
            tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

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
            self._ready_layout.insertWidget(1, self.format_params)
        elif mode == "size":
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
        width = float(self.fmt_width.value())
        height = float(self.fmt_height.value())
        top = float(self.fmt_top.value())
        bottom = float(self.fmt_bottom.value())
        if top + bottom >= height:
            bottom = max(0.5, height - top - 1)
        return replace(
            base,
            width_mm=width,
            height_mm=height,
            head_height_mm=height - top - bottom,
            chin_bottom_mm=bottom,
        )

    def _append_shot(self, path: Path) -> None:
        image = open_image(str(path))
        working = downscale(image, PREVIEW_SIDE)
        self.shots.append(Shot(path, image, working, detect_face(working)))

    def _file_tools(self):
        style = self.style()
        specs = (
            (style.StandardPixmap.SP_TitleBarUnshadeButton, "Развернуть ветки", self._expand_files),
            (style.StandardPixmap.SP_TitleBarShadeButton, "Свернуть ветки", self._collapse_files),
            (style.StandardPixmap.SP_DirHomeIcon, "Главная папка базы", self._open_archive_root),
            (style.StandardPixmap.SP_FileDialogListView, "Папка сегодня", self._open_today),
            (style.StandardPixmap.SP_DirIcon, "Открыть папку в проводнике", self._reveal_folder),
            (style.StandardPixmap.SP_FileDialogDetailedView, "Внешний редактор", self._external_editor),
            (style.StandardPixmap.SP_ArrowBack, "Повернуть против часовой", lambda: self._rotate_shot(-90)),
            (style.StandardPixmap.SP_ArrowForward, "Повернуть по часовой", lambda: self._rotate_shot(90)),
            (style.StandardPixmap.SP_TrashIcon, "Удалить", self._delete_selected_file),
            (style.StandardPixmap.SP_BrowserReload, "Обновить", self._fill_files),
            (style.StandardPixmap.SP_ArrowUp, "По имени, по возрастанию", lambda: self._sort_files("name", False)),
            (style.StandardPixmap.SP_ArrowDown, "По имени, по убыванию", lambda: self._sort_files("name", True)),
            (style.StandardPixmap.SP_ArrowUp, "По дате, старые сверху", lambda: self._sort_files("date", False)),
            (style.StandardPixmap.SP_ArrowDown, "По дате, новые сверху", lambda: self._sort_files("date", True)),
        )
        grid = QGridLayout()
        grid.setSpacing(0)
        for index, (icon, tip, handler) in enumerate(specs):
            button = QToolButton()
            button.setAutoRaise(True)
            button.setIcon(style.standardIcon(icon))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.clicked.connect(handler)
            grid.addWidget(button, index // 7, index % 7)
        return grid

    def _mounts(self) -> list[str]:
        found = {"/"}
        allowed = {"ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk"}
        try:
            for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount, fstype = parts[1], parts[2]
                if fstype in allowed and mount.startswith("/") and "/snap/" not in mount:
                    found.add(mount)
        except OSError:
            pass
        return sorted(found, key=lambda item: (item != "/", item))

    def _mount_for(self, folder: Path) -> str:
        folder_text = str(folder)
        match = "/"
        for mount in self._mounts():
            if folder_text == mount or folder_text.startswith(mount.rstrip("/") + "/"):
                if len(mount) > len(match):
                    match = mount
        return match

    def _fill_drives(self) -> None:
        self.drive_box.blockSignals(True)
        self.drive_box.clear()
        for mount in self._mounts():
            title = "Корень /" if mount == "/" else mount
            self.drive_box.addItem(title, mount)
        self.drive_box.blockSignals(False)
        self.drive_box.currentIndexChanged.connect(self._drive_changed)
        start = Path(self.settings.archive_root)
        self._show_folder(start if start.is_dir() else Path.home())

    def _drive_changed(self) -> None:
        mount = Path(str(self.drive_box.currentData()))
        self.folders.setRootIndex(self.dir_model.index(str(mount)))
        self.folder_root = mount
        self._watch_open_folder()
        self._fill_files()

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

    def _directory_loaded(self, path: str) -> None:
        pending = self._pending_folder
        if pending is None:
            return
        loaded = Path(path)
        if loaded == pending or loaded in pending.parents:
            index = self.dir_model.index(str(loaded))
            self.folders.expand(index)
        if loaded == pending:
            index = self.dir_model.index(str(pending))
            self.folders.setCurrentIndex(index)
            self.folders.scrollTo(index)
            self._pending_folder = None

    def _folder_clicked(self, index) -> None:
        path = Path(self.dir_model.filePath(index))
        if path.is_dir():
            self.folder_root = path
            self._watch_open_folder()
            self._fill_files()

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
        folder = self.folder_root
        if folder is None or not folder.is_dir():
            return
        try:
            entries = [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith(".")]
        except OSError:
            return
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
            node.setIcon(0, source_icon)
            saved_rows = read_ready(path, stems)
            self.branches[str(path)] = saved_rows
            for saved in saved_rows:
                title = saved["name"] + (" · напечатано" if saved.get("printed") else "")
                child = QTreeWidgetItem([title])
                child.setData(0, Qt.ItemDataRole.UserRole, saved["path"])
                child.setData(0, Qt.ItemDataRole.UserRole + 1, "ready")
                child.setData(0, Qt.ItemDataRole.UserRole + 2, saved["format_id"])
                child.setIcon(0, printed_icon if saved.get("printed") else ready_icon)
                node.addChild(child)
            if node.childCount():
                node.setExpanded(True)
            self.files.addTopLevelItem(node)
            if selected and (str(path) == selected or any(saved["path"] == selected for saved in saved_rows)):
                chosen = node if str(path) == selected else next(node.child(i) for i in range(node.childCount()) if node.child(i).data(0, Qt.ItemDataRole.UserRole) == selected)
                self.files.setCurrentItem(chosen)

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
                self._place_saved_format(source, format_id)
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
        self.thumb.setPixmap(pil_to_pixmap(image).scaled(QSize(180, 110), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _load_source(self, path: Path) -> None:
        for index, shot in enumerate(self.shots):
            if shot.path == path:
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
        self.files.expandAll()

    def _collapse_files(self) -> None:
        self.files.collapseAll()

    def _open_archive_root(self) -> None:
        root = Path(self.settings.archive_root)
        root.mkdir(parents=True, exist_ok=True)
        self._show_folder(root)

    def _open_today(self) -> None:
        self._show_folder(day_folder(Path(self.settings.archive_root)))

    def _reveal_folder(self) -> None:
        if self.folder_root:
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
        shot.face = detect_face(shot.working)
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
        copies = QSpinBox()
        copies.setRange(1, 40)
        copies.setValue(1)
        orient = QComboBox()
        orient.addItem("Вертикально", False)
        orient.addItem("Горизонтально", True)
        form.addRow("Копий", copies)
        form.addRow("Ориентация кадра", orient)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return copies.value(), bool(orient.currentData())

    def _drop_ready(self, path: str, format_id: str) -> None:
        photo = Path(path)
        source = photo
        info = load_ready_info(photo)
        named = info.get("source")
        if named:
            candidate = photo.parent.parent / str(named)
            if candidate.is_file():
                source = candidate
        self._place_saved_format(source, format_id)

    def _place_saved_format(self, source: Path, format_id: str) -> None:
        asked = self._ask_place()
        if asked is None:
            return
        copies, horizontal = asked
        self._load_source(source)
        index = self.format_box.findData(format_id)
        if index >= 0:
            self.format_box.setCurrentIndex(index)
        fmt = self.current_format()
        width, height = fmt.width_mm, fmt.height_mm
        rotation = 0
        if horizontal:
            width, height = height, width
            rotation = 90
        ready = self._same_sheet_photo(str(source), format_id, rotation)
        if ready is None:
            self._seq += 1
            ready = ReadyItem(f"r{self._seq}", str(source), format_id, copies, self._corner_name(), self.ready_gray.isChecked())
            self.ready.append(ready)
        else:
            ready.copies += copies
        fresh = []
        for _copy in range(copies):
            self._seq += 1
            fresh.append(SheetItem(f"p{self._seq}", "photo", 0, 0, width, height, ready.id, rotation, "", str(source)))
        self._lay_photos(fresh)
        self._set_mode("print")

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= len(self.shots):
            return
        self.index = row
        shot = self.shots[row]
        self._load_tone(shot)
        found = "лицо найдено" if shot.face else "лицо не найдено, двигайте рамку"
        self.thumb.setPixmap(pil_to_pixmap(shot.working).scaled(QSize(180, 110), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.statusBar().showMessage(f"{shot.path.name} · {found} · Shift и два щелчка — линейка")
        self.refresh()

    def open_photo(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Открыть снимок", "", "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)")
        if path:
            self.open_paths([Path(path)], reset_job=False)

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Открыть папку")
        if not folder:
            return
        self.folder_root = Path(folder)
        self._show_folder(self.folder_root)

    def open_paths(self, paths: list[Path], reset_job: bool) -> None:
        if reset_job:
            self.ready = []
            self.doc = SheetDoc()
        self.shots = []
        for path in paths:
            self._append_shot(path)
        if paths:
            self._show_folder(paths[0].parent)
        if self.shots:
            self._select_row(0)
            self._mark_tree(self.shots[0].path)

    def _store_tone(self, _value=None) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.brightness = float(self.bright.value())
        shot.contrast = float(self.contrast.value())
        shot.gamma = float(self.gamma.value())
        shot.saturation = float(self.saturation.value())
        shot.sharpness = float(self.sharpness.value())
        shot.black_point = float(self.black.value())
        shot.white_point = float(self.white.value())
        shot.tolerance = float(self.tolerance.value())
        shot.replace = self.replace_box.isChecked()
        shot.clothes = str(self.clothes_box.currentData() or "")
        self.refresh()

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

    def _balance(self) -> tuple:
        return tuple(tuple(float(box.value()) for box in row) for row in self.balance)

    def _clothes_image(self, shot: Shot) -> Image.Image | None:
        for name, image in self.clothes:
            if name == shot.clothes:
                return image
        return None

    def _crop(self, shot: Shot):
        return place_crop(shot.working.size, shot.face, self.current_format(), shot.zoom, shot.shift_x, shot.shift_y)

    def _background(self) -> str:
        chosen = self.color_box.currentData()
        return self.current_format().background if chosen == "format" else str(chosen)

    def _render(self, shot: Shot, image: Image.Image, dpi: int, corner: str = "none", grayscale: bool = False) -> Image.Image:
        crop = self._crop(shot)
        scale_x = image.width / shot.working.width
        scale_y = image.height / shot.working.height
        crop = type(crop)(crop.left * scale_x, crop.top * scale_y, crop.width * scale_x, crop.height * scale_y)
        return render_photo(
            image,
            crop,
            self.current_format(),
            dpi,
            replace=shot.replace,
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
        )

    def refresh(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        crop = self._crop(shot)
        self.source.set_frame(
            pil_to_pixmap(shot.working),
            crop,
            self.current_format(),
            self.frame_box.isChecked(),
            shot.angle if self.allow_rotate else 0.0,
            shot.eye_t,
        )
        if self._mode == "ready":
            self._show_finished()

    def _show_finished(self) -> None:
        shot = self.current_shot()
        if shot is None:
            self._finished_image = None
            self.finished.set_photo(QPixmap())
            return
        image = self._render(shot, self._preview_source(shot), self._preview_dpi(shot), self._corner_name(), self.ready_gray.isChecked())
        self._finished_image = image
        self.finished.set_photo(pil_to_pixmap(image))

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
        shot.shift_x, shot.shift_y = contain_guides(
            shot.working.size,
            shot.face,
            fmt,
            shot.zoom,
            shot.shift_x,
            shot.shift_y,
            shot.angle if self.allow_rotate else 0.0,
            shot.eye_t,
        )

    def _pan(self, dx: float, dy: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.shift_x += dx
        shot.shift_y += dy
        self._contain(shot)
        self.refresh()

    def _zoom(self, factor: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.zoom = max(ZOOM_MIN, min(ZOOM_MAX, shot.zoom * factor))
        self.refresh()

    def _nudge_guide(self, name: str, dx: float, dy: float) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        fmt = self.current_format()
        angle_now = shot.angle if self.allow_rotate else 0.0
        points = frame_points(self._crop(shot), fmt, angle_now, shot.eye_t)
        crown = points["crown"]
        chin = points["chin"]
        if name == "eyes":
            proposal = (points["eyes"][0] + dx, points["eyes"][1] + dy)
            crown, chin, shot.eye_t = move_middle(crown, chin, proposal, self.allow_rotate)
        elif name == "crown":
            crown = (crown[0] + dx, crown[1] + dy)
            if not self.allow_rotate:
                chin = (crown[0], chin[1])
        else:
            chin = (chin[0] + dx, chin[1] + dy)
            if not self.allow_rotate:
                crown = (chin[0], crown[1])
        left, top, crop_h, angle = pose_from_ends(fmt, crown, chin, self.allow_rotate, shot.eye_t)
        shot.zoom, shot.shift_x, shot.shift_y = zoom_shift_for_box(shot.working.size, shot.face, fmt, left, top, crop_h)
        shot.angle = angle
        self._contain(shot)
        self.refresh()

    def reset_frame(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.zoom, shot.shift_x, shot.shift_y, shot.angle = 1.0, 0.0, 0.0, 0.0
        shot.eye_t = 0.55
        shot.brightness = shot.contrast = shot.gamma = shot.saturation = shot.sharpness = 0
        shot.black_point, shot.white_point = 0, 255
        self._load_tone(shot)
        self.refresh()

    def _to_ready(self, switch: bool = True) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        self._seq += 1
        self.ready.append(ReadyItem(f"r{self._seq}", str(shot.path), self.current_format().id, 1, self._corner_name(), self.ready_gray.isChecked()))
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
            item.corner = str(self.corner.currentData())

    def _ready_gray_changed(self, checked: bool) -> None:
        item = self._current_ready()
        if item:
            item.grayscale = checked

    def _remove_ready(self) -> None:
        if self.ready:
            self.ready.pop()

    def _shot_for(self, source: str) -> Shot | None:
        return next((shot for shot in self.shots if str(shot.path) == source), None)

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
        )

    def _show_page(self, index: int) -> None:
        if index < 0 or index >= len(self.pages):
            return
        self.pages[self.page_index] = self.doc
        self.page_index = index
        self.doc = self.pages[index]
        self.page_label.setText(f"Лист {index + 1} из {len(self.pages)}")
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
            if len(self.pages) >= 30:
                QMessageBox.warning(self, "Лист", "Достигнут предел количества печатных листов.")
                break
            self.pages.append(self._blank_page())
            self.page_index = len(self.pages) - 1
            self.doc = self.pages[self.page_index]
            queue = rest
        self.page_label.setText(f"Лист {self.page_index + 1} из {len(self.pages)}")

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
        self.doc.grid = self.grid_box.isChecked()
        self.doc.snap = self.snap_box.isChecked()
        self.doc.cut_marks = str(self.cut_style.currentData()) != "none"
        self.printer.keep_inside = self.inside_box.isChecked()
        self.printer.block_overlap = self.overlap_box.isChecked()
        self._refresh_sheet()
        if paper_mm(self.doc) != previous_size:
            self.printer.fit()

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
            image = self._render_ready(ready, 90, preview=True)
            if image is not None:
                pixmaps[item.ready_id] = pil_to_pixmap(image)
        self.printer.set_document(self.doc, pixmaps, self._footer_text())
        self._fill_sheet_table()

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

    def _add_logo(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Логотип", "", "PNG (*.png)")
        if not path:
            return
        self.settings.icc = self.settings.icc
        self._logo_path = path
        self.logo_name.setText(Path(path).name)
        width, height = paper_mm(self.doc)
        if self.logo_after_box.isChecked():
            photos = [item for item in self.doc.items if item.kind == "photo"]
            if photos:
                last = max(photos, key=lambda item: (item.y_mm, item.x_mm + item.w_mm))
                x = last.x_mm + last.w_mm + self.doc.gap_mm
                y = last.y_mm
            else:
                x = self.doc.margin_left_mm
                y = self.doc.margin_top_mm
        else:
            x = width - self.doc.margin_right_mm - 18
            y = self.doc.margin_top_mm
        self.doc.items.append(SheetItem("logo", "logo", x, y, 18, 18, path=path))
        self._refresh_sheet()

    def _remove_logo(self) -> None:
        self.doc.items = [item for item in self.doc.items if item.kind != "logo"]
        self.logo_name.setText("Файл не выбран")
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

    def _add_clothes(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Одежда", "", "PNG (*.png)")
        if not path:
            return
        save_user_png(Path(path))
        self.clothes = all_clothes()
        self.clothes_box.clear()
        self.clothes_box.addItem("Без одежды", "")
        for name, _image in self.clothes:
            self.clothes_box.addItem(name, name)

    def _external_editor(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        editor = self.settings.editor
        if not editor:
            self.statusBar().showMessage("Укажите внешний редактор в настройках")
            return
        subprocess.Popen([editor, str(shot.path)])

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
        fresh = [path for path in sorted(folder.iterdir()) if path.suffix.lower() in IMAGE_SUFFIXES and str(path) not in known]
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
                paper_index = self.paper.findData(self.settings.default_paper)
                if paper_index >= 0:
                    self.paper.setCurrentIndex(paper_index)
                self.landscape.setChecked(self.settings.default_landscape)

    def _set_paper_layout(self, horizontal: bool) -> None:
        self._paper_horizontal = horizontal
        self.view_vert.setChecked(not horizontal)
        self.view_horz.setChecked(horizontal)
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
        self.right.setMinimumWidth(220)
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
        extra = self.settings.search_folder.strip()
        if extra:
            roots.append(Path(extra))
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
        self.replace_box.blockSignals(True)
        self.replace_box.setChecked(bool(tone["replace"]))
        self.replace_box.blockSignals(False)
        for row, values in zip(self.balance, tone["balance"]):
            for box, value in zip(row, values):
                box.blockSignals(True)
                box.setValue(int(value))
                box.blockSignals(False)
        self._store_tone()

    def _apply_quick_tune(self) -> None:
        tone = self.tune_box.currentData()
        if not isinstance(tone, dict):
            return
        if self.current_shot() is None:
            self.statusBar().showMessage("Сначала откройте снимок")
            return
        self._write_tone(tone)

    def _add_quick_tune(self) -> None:
        name, ok = QInputDialog.getText(self, "Быстрая настройка", "Название")
        if not ok or not name.strip():
            return
        tone = self._tone_snapshot()
        self.quick_tunes.append(tone)
        self.tune_box.blockSignals(True)
        self.tune_box.addItem(name.strip(), tone)
        self.tune_box.setCurrentIndex(self.tune_box.count() - 1)
        self.tune_box.blockSignals(False)
        self.statusBar().showMessage(f"Настройка «{name.strip()}» записана")

    def _update_quick_tune(self) -> None:
        index = self.tune_box.currentIndex()
        tone = self.tune_box.currentData()
        if not isinstance(tone, dict):
            self.statusBar().showMessage("Выберите настройку в списке")
            return
        fresh = self._tone_snapshot()
        tone.clear()
        tone.update(fresh)
        self.tune_box.setItemData(index, tone)
        self.statusBar().showMessage("Настройка обновлена")

    def _delete_quick_tune(self) -> None:
        index = self.tune_box.currentIndex()
        if not isinstance(self.tune_box.currentData(), dict):
            return
        self.tune_box.removeItem(index)

    def _journal(self) -> None:
        dialog = JournalDialog(list(self.journal), self)
        dialog.exec()
        from photofordocs.session import save_journal

        self.journal = dialog.lines
        save_journal(self.journal, day_folder(Path(self.settings.archive_root)))

    def _archive_dir(self) -> Path:
        return day_folder(Path(self.settings.archive_root))

    def _with_profile(self, image: Image.Image) -> Image.Image:
        if not self.settings.icc or not Path(self.settings.icc).exists():
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
        image = self._with_profile(self._render(shot, shot.image, dpi, self._corner_name(), self.ready_gray.isChecked()))
        pad = int(round(self.settings.white_frame_mm / 25.4 * dpi))
        if pad > 0:
            framed = Image.new("RGB", (image.width + pad * 2, image.height + pad * 2), (255, 255, 255))
            framed.paste(image, (pad, pad))
            image = framed
        path = ready_photo_path(shot.path, fmt.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, quality=92, dpi=(dpi, dpi))
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
        path, _selected = QFileDialog.getSaveFileName(self, "Сохранить как", f"{shot.path.stem}.jpg", "JPEG (*.jpg)")
        if path:
            dpi = int(self.dpi_box.value())
            self._with_profile(self._render(shot, shot.image, dpi, self._corner_name(), self.ready_gray.isChecked())).save(path, quality=92, dpi=(dpi, dpi))

    def _compose_sheet(self, dpi: int, doc: SheetDoc | None = None) -> Image.Image:
        doc = self.doc if doc is None else doc
        photos = {}
        for item in doc.items:
            if item.kind != "photo" or item.ready_id in photos:
                continue
            ready = next((ready for ready in self.ready if ready.id == item.ready_id), None)
            if ready is None:
                continue
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
        self._compose_sheet(EXPORT_DPI).save(path, quality=92, dpi=(EXPORT_DPI, EXPORT_DPI))
        self._record(number)
        self.statusBar().showMessage(f"Лист {path}")

    def print_sheet(self) -> None:
        self.pages[self.page_index] = self.doc
        sheets = [page for page in self.pages if any(item.kind == "photo" for item in page.items)]
        if not sheets:
            QMessageBox.information(self, "Печать", "На листе нет кадров.")
            return
        width, height = paper_mm(sheets[0])
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Unit.Millimeter, "лист"))
        dialog = QPrintDialog(printer, self)
        if self.ask_printer_box.isChecked() and dialog.exec() != QDialog.DialogCode.Accepted:
            return
        painter = QPainter(printer)
        for index, sheet in enumerate(sheets):
            if index:
                printer.newPage()
            qimage = pil_to_pixmap(self._compose_sheet(EXPORT_DPI, sheet)).toImage()
            page = painter.viewport()
            scaled = qimage.scaled(page.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            painter.drawImage((page.width() - scaled.width()) // 2, (page.height() - scaled.height()) // 2, scaled)
        painter.end()
        folder = self._archive_dir()
        self._record(next_number(folder))
        if self.clear_after_box.isChecked():
            self._clear_sheet()
