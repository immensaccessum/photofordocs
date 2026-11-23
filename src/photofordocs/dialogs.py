"""Диалоги настроек, справочника форматов, журнала и сохранения файла."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photofordocs.formats import Format, delete_format, save_format_order, save_user_format
from photofordocs.imageops import histogram
from photofordocs.session import Settings, day_total, save_settings
from photofordocs.sheet import PAPERS

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


class SettingsDialog(QDialog):
    """Настройки по разделам, как в исходной программе."""

    def __init__(self, settings: Settings, formats: list[Format], parent=None) -> None:
        super().__init__(parent)
        del formats
        self.setWindowTitle("Настройки программы")
        self.settings = settings
        self.stack = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(210)
        self._add_page(None, "Пути", self._paths_page())
        interface = self._add_page(None, "Интерфейс", self._interface_page())
        self._add_page(interface, "Внешний вид", self._look_page())
        for title, page in (
            ("Кадр", self._frame_page()),
            ("Параметры листа/качество", self._paper_page()),
            ("Поиск", self._search_page()),
            ("ICC", self._icc_page()),
            ("Прочие настройки", self._other_page()),
        ):
            self._add_page(None, title, page)
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._show_page)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ок")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addWidget(self.tree)
        body.addWidget(self.stack, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        screen = QGuiApplication.primaryScreen()
        height = 560
        if screen is not None:
            height = min(640, int(screen.availableGeometry().height() * 0.85))
        self.resize(860, height)

    def _path_row(self, value: str, directory: bool) -> QLineEdit:
        line = QLineEdit(value)
        browse = QPushButton("Обзор...")
        clear = QPushButton("×")
        clear.setFixedWidth(28)

        def pick() -> None:
            current = line.text().strip()
            if directory:
                chosen = QFileDialog.getExistingDirectory(self, "Папка", current)
            else:
                chosen, _selected = QFileDialog.getOpenFileName(self, "Файл", current)
            if chosen:
                line.setText(chosen)

        browse.clicked.connect(pick)
        clear.clicked.connect(line.clear)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(line, 1)
        row_layout.addWidget(clear)
        row_layout.addWidget(browse)
        line._row = row  # type: ignore[attr-defined]
        return line

    def _add_page(self, parent: QTreeWidgetItem | None, title: str, page: QWidget) -> QTreeWidgetItem:
        self.stack.addWidget(page)
        item = QTreeWidgetItem([title])
        item.setData(0, Qt.ItemDataRole.UserRole, self.stack.count() - 1)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        return item

    def _show_page(self, current: QTreeWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        index = current.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(index, int):
            self.stack.setCurrentIndex(index)

    def _paths_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.photo_base = self._path_row(self.settings.photo_base, True)
        self.archive = self._path_row(self.settings.archive_root, True)
        self.editor = self._path_row(self.settings.editor, False)
        self.clothes = self._path_row(self.settings.clothes_dir, True)
        self.temp = self._path_row(self.settings.temp_folder, True)
        self.sheets = self._path_row(self.settings.sheets_folder, True)
        self.studio = QLineEdit(self.settings.studio)
        form.addRow("Путь к базе для хранения фотографий", self.photo_base._row)  # type: ignore[attr-defined]
        form.addRow("Последняя рабочая папка", self.archive._row)  # type: ignore[attr-defined]
        form.addRow("Программа внешнего графического редактора", self.editor._row)  # type: ignore[attr-defined]
        form.addRow("Каталог для хранения шаблонов одежды", self.clothes._row)  # type: ignore[attr-defined]
        form.addRow("Каталог для хранения временных файлов", self.temp._row)  # type: ignore[attr-defined]
        form.addRow("Каталог для хранения файлов сформированных листов", self.sheets._row)  # type: ignore[attr-defined]
        form.addRow("Название салона", self.studio)
        return page

    def _interface_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.face = QCheckBox("Использовать автоматическое распознавание лиц")
        self.face.setChecked(self.settings.face_detect)
        layout.addWidget(QLabel("Действия при открытии фотографии"))
        layout.addWidget(self.face)
        layout.addStretch(1)
        return page

    def _look_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.currency = QLineEdit(self.settings.currency)
        self.currency.setMaximumWidth(120)
        form.addRow("Валюта", self.currency)
        note = QLabel("Цвет панелей, камера и пакеты одежды ColorRiver здесь не повторяются.")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def _frame_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.white = QDoubleSpinBox()
        self.white.setRange(0, 20)
        self.white.setDecimals(1)
        self.white.setSuffix(" мм")
        self.white.setValue(self.settings.white_frame_mm)
        self.advert = QLineEdit(self.settings.advert_text)
        self.photo_dpi = QSpinBox()
        self.photo_dpi.setRange(72, 1200)
        self.photo_dpi.setValue(int(self.settings.printer_dpi or self.settings.export_dpi or 300))
        self.jpeg_photo = QSpinBox()
        self.jpeg_photo.setRange(10, 100)
        self.jpeg_photo.setValue(int(self.settings.jpeg_photo))
        self.cut = QComboBox()
        self.cut.addItem("не рисовать", "none")
        self.cut.addItem("рамка", "box")
        self.cut.addItem("штрих-линии", "dash")
        self.cut.addItem("в углах", "corners")
        found = self.cut.findData(self.settings.cut_style)
        if found >= 0:
            self.cut.setCurrentIndex(found)
        self.cut_length = QDoubleSpinBox()
        self.cut_length.setRange(0.5, 20)
        self.cut_length.setDecimals(1)
        self.cut_length.setSuffix(" мм")
        self.cut_length.setValue(float(self.settings.cut_length_mm or 3))
        form.addRow("Ширина белой рамки вокруг выходного кадра", self.white)
        form.addRow("Текст рекламы", self.advert)
        form.addRow("DPI готовой фотографии", self.photo_dpi)
        form.addRow("Качество JPEG фотографии", self.jpeg_photo)
        form.addRow("Линии отреза", self.cut)
        form.addRow("Длина уголка линии отреза", self.cut_length)
        return page

    def _paper_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.paper = QComboBox()
        for item in PAPERS:
            self.paper.addItem(f"{item.name} ({item.width_mm:g}×{item.height_mm:g})", item.id)
        found = self.paper.findData(self.settings.default_paper)
        if found >= 0:
            self.paper.setCurrentIndex(found)
        self.landscape = QCheckBox("Альбомная ориентация")
        self.landscape.setChecked(self.settings.default_landscape)
        self.margin_top = self._margin(self.settings.margin_top_mm)
        self.margin_left = self._margin(self.settings.margin_left_mm)
        self.margin_bottom = self._margin(self.settings.margin_bottom_mm)
        self.margin_right = self._margin(self.settings.margin_right_mm)
        self.jpeg_sheet = QSpinBox()
        self.jpeg_sheet.setRange(10, 100)
        self.jpeg_sheet.setValue(int(self.settings.jpeg_sheet))
        form.addRow("Лист по умолчанию", self.paper)
        form.addRow(self.landscape)
        form.addRow("Поле верхнее", self.margin_top)
        form.addRow("Поле левое", self.margin_left)
        form.addRow("Поле нижнее", self.margin_bottom)
        form.addRow("Поле правое", self.margin_right)
        form.addRow("Качество сохранения JPEG печатного листа", self.jpeg_sheet)
        return page

    def _margin(self, value: float) -> QSpinBox:
        box = QSpinBox()
        box.setRange(0, 40)
        box.setSuffix(" мм")
        box.setValue(int(round(value)))
        return box

    def _search_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Папки для поиска фотографий. Поиск идёт и по вложенным папкам."))
        self.search_list = QListWidget()
        for folder in self.settings.search_folder.replace("|", "\n").splitlines():
            if folder.strip():
                self.search_list.addItem(folder.strip())
        add = QPushButton("Добавить путь")
        remove = QPushButton("Удалить путь")
        add.clicked.connect(self._add_search)
        remove.clicked.connect(self._remove_search)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addWidget(self.search_list, 1)
        layout.addLayout(buttons)
        return page

    def _add_search(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка поиска")
        if folder:
            self.search_list.addItem(folder)

    def _remove_search(self) -> None:
        row = self.search_list.currentRow()
        if row >= 0:
            self.search_list.takeItem(row)

    def _icc_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.icc = self._path_row(self.settings.icc, False)
        self.icc_apply = QCheckBox("Производить преобразование при печати")
        self.icc_apply.setChecked(bool(self.settings.icc_apply))
        self.printer = QComboBox()
        self.printer.setEditable(True)
        for name in self.settings.printers:
            self.printer.addItem(name)
        if self.settings.printer_name:
            self.printer.setCurrentText(self.settings.printer_name)
        form.addRow("Профиль принтера", self.icc._row)  # type: ignore[attr-defined]
        form.addRow(self.icc_apply)
        form.addRow("Принтер", self.printer)
        return page

    def _other_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.ask_delete = QCheckBox("Запрашивать подтверждение при удалении файлов")
        self.ask_delete.setChecked(self.settings.ask_before_delete)
        self.skip_count = QCheckBox("Не выдавать запрос количества размещаемых на листе фото")
        self.skip_count.setChecked(not self.settings.ask_place)
        self.place_orient = QComboBox()
        self.place_orient.addItem("Вертикально", False)
        self.place_orient.addItem("Горизонтально", True)
        self.place_orient.setCurrentIndex(1 if self.settings.place_horizontal else 0)
        self.check_margins = QCheckBox("При печати проверять выход размеров листа за поля")
        self.check_margins.setChecked(self.settings.check_margins)
        self.hide_printer = QCheckBox("Не показывать диалог выбора принтера перед печатью")
        self.hide_printer.setChecked(not self.settings.show_print_dialog)
        self.align = QCheckBox("Выравнивать фотографии по центру у верхнего края")
        self.align.setChecked(self.settings.align_center)
        self.retouch = QSpinBox()
        self.retouch.setRange(0, 100)
        stored = self.settings.retouch_koeff
        self.retouch.setValue(int(round(stored * 100)) if stored <= 1 else int(round(stored)))
        row = QHBoxLayout()
        row.addWidget(QLabel("Коэффициент растушевки уголков (100 — резкая, 40 — плавная)"))
        row.addWidget(self.retouch)
        for box in (self.ask_delete, self.skip_count, self.check_margins, self.hide_printer, self.align):
            layout.addWidget(box)
        orient = QHBoxLayout()
        orient.addWidget(QLabel("Ориентация кадра"))
        orient.addWidget(self.place_orient)
        orient.addStretch(1)
        layout.addLayout(orient)
        layout.addLayout(row)
        note = QLabel("Проверка обновлений и пакетов одежды ColorRiver не выполняется.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def apply(self) -> Settings:
        self.settings.photo_base = self.photo_base.text().strip()
        self.settings.archive_root = self.archive.text().strip() or self.settings.archive_root
        self.settings.editor = self.editor.text().strip()
        self.settings.clothes_dir = self.clothes.text().strip()
        self.settings.temp_folder = self.temp.text().strip()
        self.settings.sheets_folder = self.sheets.text().strip()
        self.settings.studio = self.studio.text().strip()
        self.settings.face_detect = self.face.isChecked()
        self.settings.currency = self.currency.text().strip() or "₽"
        self.settings.white_frame_mm = float(self.white.value())
        self.settings.advert_text = self.advert.text().strip()
        self.settings.printer_dpi = int(self.photo_dpi.value())
        self.settings.jpeg_photo = int(self.jpeg_photo.value())
        self.settings.cut_style = str(self.cut.currentData())
        self.settings.cut_length_mm = float(self.cut_length.value())
        self.settings.default_paper = str(self.paper.currentData())
        self.settings.default_landscape = self.landscape.isChecked()
        self.settings.margin_top_mm = float(self.margin_top.value())
        self.settings.margin_left_mm = float(self.margin_left.value())
        self.settings.margin_bottom_mm = float(self.margin_bottom.value())
        self.settings.margin_right_mm = float(self.margin_right.value())
        self.settings.jpeg_sheet = int(self.jpeg_sheet.value())
        folders = [self.search_list.item(row).text().strip() for row in range(self.search_list.count())]
        self.settings.search_folder = "\n".join(folder for folder in folders if folder)
        self.settings.icc = self.icc.text().strip()
        self.settings.icc_apply = self.icc_apply.isChecked()
        self.settings.printer_name = self.printer.currentText().strip()
        self.settings.ask_before_delete = self.ask_delete.isChecked()
        self.settings.ask_place = not self.skip_count.isChecked()
        self.settings.place_horizontal = bool(self.place_orient.currentData())
        self.settings.check_margins = self.check_margins.isChecked()
        self.settings.show_print_dialog = not self.hide_printer.isChecked()
        self.settings.align_center = self.align.isChecked()
        self.settings.retouch_koeff = self.retouch.value() / 100
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


@dataclass
class SaveFileSpec:
    limit_size: bool
    width_px: int
    height_px: int
    dpi: int
    limit_bytes: bool
    max_kb: int
    open_folder: bool


class SaveFileDialog(QDialog):
    """Окно «Сохранить в файл»: размер в пикселях или миллиметрах, потолок JPEG и папка."""

    def __init__(
        self,
        parent: QWidget,
        width_mm: float,
        height_mm: float,
        dpi: int,
        max_kb: int,
        limit_size: bool,
        limit_bytes: bool,
        open_folder: bool,
        preview: QPixmap,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохранить в файл")
        self._mm_w = max(1.0, float(width_mm))
        self._mm_h = max(1.0, float(height_mm))
        self._dpi = max(36, int(dpi))
        self._syncing = False

        self.limit_size = QCheckBox("Контроль размеров изображения")
        self.limit_size.setChecked(limit_size)
        self.units = QTabBar()
        self.units.addTab("Пиксели")
        self.units.addTab("Миллиметры")
        self.units.setCurrentIndex(1)
        self.units.setExpanding(False)

        self.width_box = QDoubleSpinBox()
        self.height_box = QDoubleSpinBox()
        for box in (self.width_box, self.height_box):
            box.setDecimals(0)
            box.setRange(1, 1000)
            box.setMaximumWidth(90)
        self.dpi_box = QSpinBox()
        self.dpi_box.setRange(36, 2400)
        self.dpi_box.setValue(self._dpi)
        self.dpi_box.setMaximumWidth(90)

        self.size_frame = QFrame()
        self.size_frame.setFrameShape(QFrame.Shape.StyledPanel)
        size = self.size_frame
        grid = QGridLayout(size)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.addWidget(QLabel("Ширина"), 0, 0)
        grid.addWidget(self.width_box, 0, 1)
        grid.addWidget(QLabel("DPI"), 0, 2)
        grid.addWidget(self.dpi_box, 0, 3)
        grid.addWidget(QLabel("Высота"), 1, 0)
        grid.addWidget(self.height_box, 1, 1)
        grid.setColumnStretch(4, 1)

        self.limit_bytes = QCheckBox("Контроль размеров файла (только JPEG)")
        self.limit_bytes.setChecked(limit_bytes)
        self.kb_label = QLabel("Максимальный размер файла (КБ)")
        self.max_kb = QSpinBox()
        self.max_kb.setRange(1, 9999)
        self.max_kb.setValue(max(1, int(max_kb or 48)))
        self.max_kb.setMaximumWidth(90)
        kb_row = QHBoxLayout()
        kb_row.addWidget(self.kb_label)
        kb_row.addWidget(self.max_kb)
        kb_row.addStretch(1)

        self.open_folder = QCheckBox("Открыть папку содержащую файл после сохранения")
        self.open_folder.setChecked(open_folder)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ок")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        left = QVBoxLayout()
        left.addWidget(self.limit_size)
        left.addWidget(self.units)
        left.addWidget(size)
        left.addWidget(self.limit_bytes)
        left.addLayout(kb_row)
        left.addWidget(self.open_folder)
        left.addStretch(1)
        left.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

        photo = QLabel()
        photo.setObjectName("photopreview")
        photo.setFixedSize(210, 280)
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if preview is not None and not preview.isNull():
            photo.setPixmap(
                preview.scaled(
                    206,
                    276,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        row = QHBoxLayout()
        row.addLayout(left, 1)
        row.addWidget(photo, alignment=Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.addLayout(row)

        self.limit_size.toggled.connect(self._toggle_size)
        self.limit_bytes.toggled.connect(self._toggle_bytes)
        self.units.currentChanged.connect(self._show_unit)
        self.width_box.valueChanged.connect(self._width_edited)
        self.height_box.valueChanged.connect(self._height_edited)
        self.dpi_box.valueChanged.connect(self._dpi_edited)
        self._show_unit(1)
        self._toggle_size(self.limit_size.isChecked())
        self._toggle_bytes(self.limit_bytes.isChecked())

    def _pixels(self) -> tuple[int, int]:
        return (
            max(1, round(self._mm_w / 25.4 * self._dpi)),
            max(1, round(self._mm_h / 25.4 * self._dpi)),
        )

    def _show_unit(self, index: int) -> None:
        pixels = index == 0
        self._syncing = True
        if pixels:
            width, height = self._pixels()
            self.width_box.setRange(1, 20000)
            self.height_box.setRange(1, 20000)
            self.width_box.setValue(width)
            self.height_box.setValue(height)
        else:
            self._mm_w = float(round(self._mm_w))
            self._mm_h = float(round(self._mm_h))
            self.width_box.setRange(1, 1000)
            self.height_box.setRange(1, 1000)
            self.width_box.setValue(self._mm_w)
            self.height_box.setValue(self._mm_h)
        self._syncing = False

    def _width_edited(self, value: float) -> None:
        if self._syncing:
            return
        if self.units.currentIndex() == 0:
            self._mm_w = float(value) / self._dpi * 25.4
        else:
            self._mm_w = float(value)

    def _height_edited(self, value: float) -> None:
        if self._syncing:
            return
        if self.units.currentIndex() == 0:
            self._mm_h = float(value) / self._dpi * 25.4
        else:
            self._mm_h = float(value)

    def _dpi_edited(self, value: int) -> None:
        self._dpi = max(36, int(value))
        if self.units.currentIndex() == 0 and not self._syncing:
            self._show_unit(0)

    def _toggle_size(self, on: bool) -> None:
        for widget in (self.units, self.size_frame):
            widget.setEnabled(on)

    def _toggle_bytes(self, on: bool) -> None:
        self.kb_label.setEnabled(on)
        self.max_kb.setEnabled(on)

    def spec(self) -> SaveFileSpec:
        width, height = self._pixels()
        return SaveFileSpec(
            limit_size=self.limit_size.isChecked(),
            width_px=width,
            height_px=height,
            dpi=self._dpi,
            limit_bytes=self.limit_bytes.isChecked(),
            max_kb=int(self.max_kb.value()),
            open_folder=self.open_folder.isChecked(),
        )

