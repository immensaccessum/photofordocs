"""Архив дня, журнал, схемы листа и положение кадров. Без окна."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".config" / "photofordocs"
JOURNAL_PATH = CONFIG_DIR / "journal.yaml"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SCHEMES_DIR = CONFIG_DIR / "schemes"


@dataclass
class ReadyItem:
    id: str
    source: str
    format_id: str
    copies: int = 1
    corner: str = "none"
    grayscale: bool = False
    corner_mm: float = 0.0
    zoom: float = 1.0
    shift_x: float = 0.0
    shift_y: float = 0.0
    angle: float = 0.0
    eye_t: float = 0.55


@dataclass
class SheetItem:
    id: str
    kind: str
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    ready_id: str = ""
    rotation: int = 0
    text: str = ""
    path: str = ""

    def moved(self, dx: float, dy: float) -> SheetItem:
        return SheetItem(
            self.id,
            self.kind,
            self.x_mm + dx,
            self.y_mm + dy,
            self.w_mm,
            self.h_mm,
            self.ready_id,
            self.rotation,
            self.text,
            self.path,
        )


@dataclass
class SheetDoc:
    paper_id: str = "10x15"
    custom_w: float = 100.0
    custom_h: float = 150.0
    landscape: bool = False
    margin_mm: float = 4.0
    gap_mm: float = 0.0
    snap: bool = True
    grid: bool = False
    grid_mm: float = 5.0
    cut_marks: bool = True
    items: list[SheetItem] = field(default_factory=list)
    margin_top_mm: float = 4.0
    margin_right_mm: float = 4.0
    margin_bottom_mm: float = 4.0
    margin_left_mm: float = 4.0
    cut_style: str = "corners"
    cut_length_mm: float = 3.0
    cut_width_mm: float = 0.1


@dataclass
class JournalLine:
    at: str
    number: str
    formats: str
    copies: int
    amount: float
    day: str


PAPER_MM = {
    "10x15": (100.0, 150.0),
    "15x20": (150.0, 200.0),
    "15x21": (150.0, 210.0),
    "20x30": (200.0, 300.0),
    "a6": (105.0, 148.0),
    "a5": (148.0, 210.0),
    "a4": (210.0, 297.0),
    "a3": (297.0, 420.0),
}


def sheet_edges(doc: SheetDoc) -> tuple[float, float, float, float]:
    """Поля листа: верх, право, низ, лево."""
    return doc.margin_top_mm, doc.margin_right_mm, doc.margin_bottom_mm, doc.margin_left_mm


def paper_mm(doc: SheetDoc) -> tuple[float, float]:
    width, height = PAPER_MM.get(doc.paper_id, (doc.custom_w, doc.custom_h))
    if doc.landscape:
        return height, width
    return width, height


def overflows(item: SheetItem, doc: SheetDoc) -> bool:
    width, height = paper_mm(doc)
    return item.x_mm < -0.2 or item.y_mm < -0.2 or item.x_mm + item.w_mm > width + 0.2 or item.y_mm + item.h_mm > height + 0.2


def split_place(doc: SheetDoc, photos: list[SheetItem]) -> tuple[list[SheetItem], list[SheetItem]]:
    """Кладёт кадры, пока они входят в лист. Хвост возвращает отдельно."""
    width, height = paper_mm(doc)
    top, right, bottom, left = sheet_edges(doc)
    limit = height - bottom
    x = left
    y = top
    row_h = 0.0
    placed: list[SheetItem] = []
    rest: list[SheetItem] = []
    stopped = False
    for item in photos:
        if stopped:
            rest.append(item)
            continue
        nx, ny, nrow = x, y, row_h
        if nx > left and nx + item.w_mm > width - right + 0.2:
            nx = left
            ny = y + row_h + doc.gap_mm
            nrow = 0.0
        if ny + item.h_mm > limit + 0.2:
            rest.append(item)
            stopped = True
            continue
        placed.append(SheetItem(item.id, item.kind, nx, ny, item.w_mm, item.h_mm, item.ready_id, item.rotation, item.text, item.path))
        x = nx + item.w_mm + doc.gap_mm
        y = ny
        row_h = max(nrow, item.h_mm)
    keep = [item for item in doc.items if item.kind != "photo"]
    if not any(item.kind == "footer" for item in keep):
        keep.append(
            SheetItem(
                "footer",
                "footer",
                left,
                max(top, height - bottom - 8),
                max(20.0, width - left - right),
                8,
                text="",
            )
        )
    return placed + keep, rest


def center_photos(doc: SheetDoc) -> None:
    """Ставит блок кадров по центру ширины листа, верх блока — к верхнему полю."""
    photos = [item for item in doc.items if item.kind == "photo"]
    if not photos:
        return
    width, _height = paper_mm(doc)
    top, right, _bottom, left = sheet_edges(doc)
    min_x = min(item.x_mm for item in photos)
    min_y = min(item.y_mm for item in photos)
    max_x = max(item.x_mm + item.w_mm for item in photos)
    inner_w = width - left - right
    dx = left + (inner_w - (max_x - min_x)) / 2 - min_x
    dy = top - min_y
    for item in photos:
        item.x_mm += dx
        item.y_mm += dy


def _blocked(x: float, y: float, w: float, h: float, occupied: list[tuple[float, float, float, float]], gap: float) -> bool:
    for ox, oy, ow, oh in occupied:
        if x + w + gap > ox and ox + ow + gap > x and y + h + gap > oy and oy + oh + gap > y:
            return True
    return False


def _slot(doc: SheetDoc, w: float, h: float, occupied: list[tuple[float, float, float, float]]) -> tuple[float, float] | None:
    """Первое свободное место: слева направо, затем строкой ниже."""
    width, height = paper_mm(doc)
    top, right, bottom, left = sheet_edges(doc)
    if w > width - left - right + 0.2 or h > height - top - bottom + 0.2:
        return None
    xs = {left}
    ys = {top}
    for ox, oy, ow, oh in occupied:
        xs.add(ox + ow + doc.gap_mm)
        ys.add(oy + oh + doc.gap_mm)
    for y in sorted(ys):
        if y + h > height - bottom + 0.2:
            continue
        for x in sorted(xs):
            if x + w > width - right + 0.2:
                continue
            if not _blocked(x, y, w, h, occupied, doc.gap_mm):
                return x, y
    return None


def append_photos(doc: SheetDoc, photos: list[SheetItem]) -> tuple[list[SheetItem], list[SheetItem]]:
    """Добавляет кадры в свободные места. Уже стоящие фотографии не сдвигает."""
    width, height = paper_mm(doc)
    top, right, bottom, left = sheet_edges(doc)
    stayed = [item for item in doc.items if item.kind == "photo"]
    kept = [item for item in doc.items if item.kind != "photo"]
    if not any(item.kind == "footer" for item in kept):
        kept.append(
            SheetItem(
                "footer",
                "footer",
                left,
                max(top, height - bottom - 8),
                max(20.0, width - left - right),
                8,
                text="",
            )
        )
    occupied = [(item.x_mm, item.y_mm, item.w_mm, item.h_mm) for item in stayed]
    occupied.extend((item.x_mm, item.y_mm, item.w_mm, item.h_mm) for item in kept if item.kind == "logo")
    fresh: list[SheetItem] = []
    rest: list[SheetItem] = []
    for item in photos:
        slot = _slot(doc, item.w_mm, item.h_mm, occupied)
        if slot is None:
            rest.append(item)
            continue
        x, y = slot
        fresh.append(SheetItem(item.id, item.kind, x, y, item.w_mm, item.h_mm, item.ready_id, item.rotation, item.text, item.path))
        occupied.append((x, y, item.w_mm, item.h_mm))
    return stayed + fresh + kept, rest


def photo_too_big(doc: SheetDoc, item: SheetItem) -> bool:
    width, height = paper_mm(doc)
    top, right, bottom, left = sheet_edges(doc)
    room_w = width - left - right
    room_h = height - top - bottom
    return item.w_mm > room_w + 0.2 or item.h_mm > room_h + 0.2


def autoplace(doc: SheetDoc, photos: list[SheetItem]) -> list[SheetItem]:
    """Раскладывает кадры слева направо. Уже стоящие колонтитул и логотип не трогает."""
    width, height = paper_mm(doc)
    top, right, _bottom, left = sheet_edges(doc)
    x = left
    y = top
    row_h = 0.0
    placed: list[SheetItem] = []
    for item in photos:
        if x > left and x + item.w_mm > width - right:
            x = left
            y += row_h + doc.gap_mm
            row_h = 0.0
        placed.append(SheetItem(item.id, item.kind, x, y, item.w_mm, item.h_mm, item.ready_id, item.rotation, item.text, item.path))
        x += item.w_mm + doc.gap_mm
        row_h = max(row_h, item.h_mm)
    keep = [item for item in doc.items if item.kind != "photo"]
    if not any(item.kind == "footer" for item in keep):
        keep.append(
            SheetItem(
                "footer",
                "footer",
                left,
                max(top, height - _bottom - 8),
                max(20.0, width - left - right),
                8,
                text="",
            )
        )
    return placed + keep


# Зона захвата маленькая: кадр цепляется только когда край уже почти совпал.
SNAP_MM = 0.4


def snap_point(value: float, targets: list[float], limit: float) -> float:
    if not targets:
        return value
    best = min(targets, key=lambda item: abs(item - value))
    if abs(best - value) <= limit:
        return best
    return value


def snap_item(item: SheetItem, doc: SheetDoc, others: list[SheetItem]) -> SheetItem:
    if not doc.snap:
        return item
    width, height = paper_mm(doc)
    top, right, bottom, left = sheet_edges(doc)
    xs = [left, width - right - item.w_mm]
    ys = [top, height - bottom - item.h_mm]
    if doc.grid and doc.grid_mm > 0:
        xs.append(round(item.x_mm / doc.grid_mm) * doc.grid_mm)
        ys.append(round(item.y_mm / doc.grid_mm) * doc.grid_mm)
    for other in others:
        if other.id == item.id:
            continue
        xs.extend([other.x_mm, other.x_mm + other.w_mm, other.x_mm + other.w_mm + doc.gap_mm, other.x_mm - item.w_mm - doc.gap_mm])
        ys.extend([other.y_mm, other.y_mm + other.h_mm, other.y_mm + other.h_mm + doc.gap_mm, other.y_mm - item.h_mm - doc.gap_mm])
    return SheetItem(
        item.id,
        item.kind,
        snap_point(item.x_mm, xs, SNAP_MM),
        snap_point(item.y_mm, ys, SNAP_MM),
        item.w_mm,
        item.h_mm,
        item.ready_id,
        item.rotation,
        item.text,
        item.path,
    )


def move_selected(doc: SheetDoc, selected: set[str], dx: float, dy: float) -> SheetDoc:
    """Сдвигает выбранные кадры одним сдвигом. Прилипает якорь, остальные едут вместе с ним."""
    anchor = next((item for item in doc.items if item.id in selected), None)
    if anchor is None:
        return doc
    still = [item for item in doc.items if item.id not in selected]
    snapped = snap_item(anchor.moved(dx, dy), doc, still)
    adx = snapped.x_mm - anchor.x_mm
    ady = snapped.y_mm - anchor.y_mm
    moved = []
    for item in doc.items:
        moved.append(item.moved(adx, ady) if item.id in selected else item)
    return SheetDoc(
        doc.paper_id,
        doc.custom_w,
        doc.custom_h,
        doc.landscape,
        doc.margin_mm,
        doc.gap_mm,
        doc.snap,
        doc.grid,
        doc.grid_mm,
        doc.cut_marks,
        moved,
        doc.margin_top_mm,
        doc.margin_right_mm,
        doc.margin_bottom_mm,
        doc.margin_left_mm,
        doc.cut_style,
        doc.cut_length_mm,
        doc.cut_width_mm,
    )


def startup_folder(settings: Settings) -> Path:
    """Папка проводника при запуске: та, что была открыта при выходе, иначе корень архива."""
    for raw in (settings.last_folder, settings.archive_root):
        text = str(raw or "").strip()
        if not text:
            continue
        folder = Path(text)
        if folder.is_dir():
            return folder
    return Path.home()


def month_folder(root: Path) -> Path:
    """Корень текущего месяца в архиве (archive_root в настройках)."""
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def next_number(folder: Path) -> int:
    numbers = []
    for path in folder.glob("*.jpg"):
        head = path.name.split("-", 1)[0]
        if head.isdigit():
            numbers.append(int(head))
    return max(numbers, default=0) + 1


def archive_names(number: int, format_slug: str) -> tuple[str, str]:
    return f"{number:03d}-{format_slug}.jpg", f"{number:03d}-лист.jpg"


def ready_folder(source: Path) -> Path:
    """Папка готовых кадров рядом с исходником.

    Обычно это Ready в той же папке. Если снимки убраны во вложенную папку,
    а Ready остался на уровень выше, берётся он.
    """
    local = source.parent / "Ready"
    if local.is_dir():
        return local
    parent = source.parent.parent / "Ready"
    if source.parent.parent != source.parent and parent.is_dir():
        return parent
    return local


_PHOTOLAB_EXT = re.compile(r"\.(jp_[a-z0-9_]+|10x12)$", re.IGNORECASE)
_FORMAT_SPECS: dict[str, tuple[str, float, float]] | None = None


def _decode_format(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def photolab_format_specs() -> dict[str, tuple[str, float, float]]:
    """Расширение формата оригинала: название и размер в миллиметрах."""
    global _FORMAT_SPECS
    if _FORMAT_SPECS is not None:
        return _FORMAT_SPECS
    found: dict[str, tuple[str, float, float]] = {}
    root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ColorRiver" / "Photolab" / "formats"
    if root.is_dir():
        for path in root.glob("format.*"):
            fields: dict[str, str] = {}
            try:
                lines = _decode_format(path.read_bytes()).splitlines()
            except OSError:
                continue
            for line in lines:
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                fields[key.strip()] = value.strip()
            ext = fields.get("Ext", "").lstrip(".").lower()
            name = fields.get("Name", "")
            try:
                width = float(fields.get("Width", "0"))
                height = float(fields.get("Height", "0"))
                dpi = float(fields.get("DPI", "300") or 300)
            except ValueError:
                continue
            if not ext or not name or width <= 0 or height <= 0:
                continue
            if width > 200 or height > 200:
                dpi = dpi or 300
                width = width / dpi * 25.4
                height = height / dpi * 25.4
            found[ext] = (name, width, height)
    _FORMAT_SPECS = found
    return found


def photolab_extension_names() -> dict[str, str]:
    """Расширение формата оригинала, например jp_41, и его название."""
    return {ext: spec[0] for ext, spec in photolab_format_specs().items()}


def split_ready_name(filename: str) -> tuple[str, str, bool] | None:
    """Имя в Ready: своё «снимок.Формат.jpg» или оригинал «снимок.jp_41~p».

    Возвращает тело имени, ключ формата и признак «напечатано». Хвост ~p у оригинала
    означает, что кадр уже печатали.
    """
    printed = filename.lower().endswith("~p")
    base = filename[:-2] if printed else filename
    lower = base.lower()
    if lower.endswith(".jpg"):
        return base[:-4], "jpg", printed
    match = _PHOTOLAB_EXT.search(base)
    if match and match.end() == len(base):
        return base[: match.start()], match.group(1).lower(), printed
    return None


def format_file_piece(format_name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else " " for ch in format_name).strip()
    return cleaned or "формат"


def ready_photo_path(source: Path, format_id: str) -> Path:
    """Имя готового кадра: «снимок.format_id.jpg» внутри Ready."""
    ident = "".join(ch if ch not in '\\/:*?"<>|' else "-" for ch in format_id).strip() or "format"
    return ready_folder(source) / f"{source.stem}.{ident}.jpg"


def ready_copy_path(path: Path) -> tuple[Path, int]:
    """Свободное имя рядом с уже сохранённым кадром: «… 2.jpg», затем 3 и дальше."""
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem} {number}{path.suffix}")
        if not candidate.exists():
            return candidate, number
        number += 1


def ready_info_path(photo: Path) -> Path:
    return photo.with_name(photo.name + ".info")


def _best_stem(filename: str, stems: set[str]) -> str | None:
    """Самое длинное имя снимка, которым начинается файл в Ready."""
    lowered = filename.lower()
    best: str | None = None
    for stem in stems:
        prefix = stem.lower() + "."
        if lowered.startswith(prefix) and (best is None or len(stem) > len(best)):
            best = stem
    return best


def ready_belongs(source: Path, filename: str, stems: set[str]) -> bool:
    """Файл Ready относится к этому снимку, а не к соседнему с более длинным именем."""
    if split_ready_name(filename) is None:
        return False
    match = _best_stem(filename, stems)
    return match is not None and match.lower() == source.stem.lower()


def read_ready(
    source: Path,
    stems: set[str] | None = None,
    names: dict[str, str] | None = None,
    *,
    alias_map: dict[str, str] | None = None,
    formats: list | None = None,
) -> list[dict]:
    folder = ready_folder(source)
    if not folder.is_dir():
        return []
    from photofordocs.formats import build_alias_map, load_formats, resolve_format_id

    if formats is None:
        formats = load_formats()
    if alias_map is None:
        alias_map = build_alias_map(formats)
    known = stems if stems is not None else {source.stem}
    catalog = photolab_extension_names() if names is None else names
    records = []
    for path in sorted(folder.iterdir()):
        parsed = None if not path.is_file() else split_ready_name(path.name)
        if parsed is None or not ready_belongs(source, path.name, known):
            continue
        body, key, printed = parsed
        info: dict = {}
        sidecar = ready_info_path(path)
        if sidecar.is_file():
            loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                info = loaded
        if key == "jpg":
            piece = body[len(source.stem) + 1 :] if body.lower().startswith(source.stem.lower() + ".") else body
            title = str(info.get("name") or piece)
            raw_id = str(info.get("format_id") or piece)
        else:
            title = str(info.get("name") or catalog.get(key) or key)
            raw_id = str(info.get("format_id") or key)
        format_id = resolve_format_id(raw_id, alias_map, formats)
        records.append(
            {
                "name": title,
                "path": str(path),
                "format_id": format_id,
                "printed": printed or bool(info.get("printed")),
            }
        )
    return records


def write_ready_info(photo: Path, info: dict) -> None:
    ready_info_path(photo).write_text(yaml.safe_dump(info, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_ready_info(photo: Path) -> dict:
    sidecar = ready_info_path(photo)
    if not sidecar.is_file():
        return {}
    loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass
class Settings:
    archive_root: str = str(Path.home() / "Фото на документы")
    last_folder: str = ""
    prices: dict[str, float] = field(default_factory=dict)
    watch_folder: str = ""
    icc: str = ""
    email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    studio: str = ""
    editor: str = ""
    sheets_folder: str = ""
    temp_folder: str = ""
    white_frame_mm: float = 0.0
    advert_text: str = ""
    default_paper: str = "10x15"
    default_landscape: bool = False
    default_format: str = ""
    search_folder: str = ""
    photo_base: str = ""
    printer_name: str = ""
    printers: list[str] = field(default_factory=list)
    printer_dpi: int = 300
    export_dpi: int = 300
    watch_min_kb: int = 0
    jpeg_photo: int = 92
    jpeg_sheet: int = 92
    margin_top_mm: float = 4.0
    margin_right_mm: float = 4.0
    margin_bottom_mm: float = 4.0
    margin_left_mm: float = 4.0
    tone_brightness: float = 0.0
    tone_contrast: float = 0.0
    tone_gamma: float = 0.0
    tone_saturation: float = 0.0
    tone_sharpness: float = 0.0
    tone_balance: list[float] = field(default_factory=lambda: [0.0] * 9)
    ground_threshold: float = 0.0
    replace_background: bool = False
    background_method: str = "edge"
    paint_background: bool = False
    brush_px: int = 0
    cut_style: str = "corners"
    cut_length_mm: float = 3.0
    cut_width_mm: float = 0.1
    ask_before_delete: bool = True
    ask_place: bool = True
    place_horizontal: bool = True
    confirm_overwrite: bool = True
    face_detect: bool = True
    show_print_dialog: bool = True
    clear_after_print: bool = False
    check_margins: bool = True
    check_overlap: bool = True
    align_center: bool = False
    snap: bool = True
    show_footer: bool = False
    show_quick_bar: bool = True
    print_horizontal: bool = False
    allow_rotate: bool = False
    save_limit_size: bool = True
    save_limit_bytes: bool = False
    save_open_folder: bool = True
    save_max_kb: int = 48
    logo_place: str = "among"
    quick_tunes: list[dict] = field(default_factory=list)
    logo_path: str = ""
    logo_auto: bool = False
    email_subject: str = "Отчёт фото на документы"
    email_ssl: bool = True
    send_log: bool = True
    external_save_price: float = 0.0
    icc_apply: bool | None = None
    sort_key: str = "name"
    sort_reverse: bool = False
    file_panel_width: int = 0
    preview_height: int = 0
    left_sizes: list[int] = field(default_factory=list)
    currency: str = "₽"
    theme: str = "auto"
    auto_date_folder: bool = False
    colors: list[dict] = field(default_factory=list)
    retouch_koeff: float = 0.0

    def price_for(self, format_id: str) -> float:
        return float(self.prices.get(format_id, 0))


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    data = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    settings = Settings()
    for key in (
        "archive_root",
        "last_folder",
        "watch_folder",
        "icc",
        "email",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "studio",
        "editor",
        "sheets_folder",
        "temp_folder",
        "advert_text",
        "default_paper",
        "default_format",
        "search_folder",
    ):
        if key in data and data[key] is not None:
            setattr(settings, key, str(data[key]))
    if data.get("smtp_port"):
        settings.smtp_port = int(data["smtp_port"])
    if data.get("white_frame_mm") is not None:
        settings.white_frame_mm = float(data["white_frame_mm"])
    settings.default_landscape = bool(data.get("default_landscape", False))
    settings.prices = {str(key): float(value) for key, value in (data.get("prices") or {}).items()}
    for key, cast in (
        ("photo_base", str),
        ("printer_name", str),
        ("logo_path", str),
        ("logo_place", str),
        ("email_subject", str),
        ("currency", str),
        ("cut_style", str),
        ("sort_key", str),
        ("background_method", str),
    ):
        if data.get(key):
            setattr(settings, key, cast(data[key]))
    theme = str(data.get("theme") or "auto")
    settings.theme = theme if theme in ("auto", "light", "dark") else "auto"
    for key, cast in (
        ("printer_dpi", int),
        ("export_dpi", int),
        ("watch_min_kb", int),
        ("jpeg_photo", int),
        ("jpeg_sheet", int),
        ("brush_px", int),
        ("file_panel_width", int),
        ("preview_height", int),
        ("save_max_kb", int),
    ):
        if data.get(key) is not None and data.get(key) != "":
            setattr(settings, key, cast(data[key]))
    for key in (
        "margin_top_mm",
        "margin_right_mm",
        "margin_bottom_mm",
        "margin_left_mm",
        "tone_brightness",
        "tone_contrast",
        "tone_gamma",
        "tone_saturation",
        "tone_sharpness",
        "ground_threshold",
        "cut_length_mm",
        "cut_width_mm",
        "external_save_price",
        "retouch_koeff",
    ):
        if data.get(key) is not None and data.get(key) != "":
            setattr(settings, key, float(data[key]))
    for key in (
        "ask_before_delete",
        "ask_place",
        "place_horizontal",
        "confirm_overwrite",
        "face_detect",
        "show_print_dialog",
        "clear_after_print",
        "check_margins",
        "check_overlap",
        "align_center",
        "snap",
        "show_footer",
        "show_quick_bar",
        "print_horizontal",
        "allow_rotate",
        "save_limit_size",
        "save_limit_bytes",
        "save_open_folder",
        "replace_background",
        "paint_background",
        "logo_auto",
        "email_ssl",
        "send_log",
        "sort_reverse",
    ):
        if key in data:
            setattr(settings, key, bool(data[key]))
    settings.auto_date_folder = False
    if "icc_apply" in data and data["icc_apply"] is not None:
        settings.icc_apply = bool(data["icc_apply"])
    balance = data.get("tone_balance")
    if isinstance(balance, list) and balance:
        settings.tone_balance = [float(item) for item in balance[:9]]
        settings.tone_balance += [0.0] * (9 - len(settings.tone_balance))
    colors = data.get("colors")
    if isinstance(colors, list):
        settings.colors = [item for item in colors if isinstance(item, dict)]
    printers = data.get("printers")
    if isinstance(printers, list):
        settings.printers = [str(item) for item in printers if str(item).strip()]
    sizes = data.get("left_sizes")
    if isinstance(sizes, list) and len(sizes) == 3:
        settings.left_sizes = [int(item) for item in sizes]
    tunes = data.get("quick_tunes")
    if isinstance(tunes, list):
        settings.quick_tunes = [item for item in tunes if isinstance(item, dict) and isinstance(item.get("tone"), dict)]
    return settings


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "archive_root": settings.archive_root,
        "last_folder": settings.last_folder,
        "prices": settings.prices,
        "watch_folder": settings.watch_folder,
        "icc": settings.icc,
        "email": settings.email,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": settings.smtp_password,
        "studio": settings.studio,
        "editor": settings.editor,
        "sheets_folder": settings.sheets_folder,
        "temp_folder": settings.temp_folder,
        "white_frame_mm": settings.white_frame_mm,
        "advert_text": settings.advert_text,
        "default_paper": settings.default_paper,
        "default_landscape": settings.default_landscape,
        "default_format": settings.default_format,
        "search_folder": settings.search_folder,
        "photo_base": settings.photo_base,
        "printer_name": settings.printer_name,
        "printers": settings.printers,
        "printer_dpi": settings.printer_dpi,
        "export_dpi": settings.export_dpi,
        "watch_min_kb": settings.watch_min_kb,
        "jpeg_photo": settings.jpeg_photo,
        "jpeg_sheet": settings.jpeg_sheet,
        "margin_top_mm": settings.margin_top_mm,
        "margin_right_mm": settings.margin_right_mm,
        "margin_bottom_mm": settings.margin_bottom_mm,
        "margin_left_mm": settings.margin_left_mm,
        "tone_brightness": settings.tone_brightness,
        "tone_contrast": settings.tone_contrast,
        "tone_gamma": settings.tone_gamma,
        "tone_saturation": settings.tone_saturation,
        "tone_sharpness": settings.tone_sharpness,
        "tone_balance": settings.tone_balance,
        "ground_threshold": settings.ground_threshold,
        "replace_background": settings.replace_background,
        "background_method": settings.background_method if settings.background_method in ("edge", "figure") else "edge",
        "paint_background": settings.paint_background,
        "brush_px": settings.brush_px,
        "cut_style": settings.cut_style,
        "cut_length_mm": settings.cut_length_mm,
        "cut_width_mm": settings.cut_width_mm,
        "ask_before_delete": settings.ask_before_delete,
        "ask_place": settings.ask_place,
        "place_horizontal": settings.place_horizontal,
        "confirm_overwrite": settings.confirm_overwrite,
        "face_detect": settings.face_detect,
        "show_print_dialog": settings.show_print_dialog,
        "clear_after_print": settings.clear_after_print,
        "check_margins": settings.check_margins,
        "check_overlap": settings.check_overlap,
        "align_center": settings.align_center,
        "snap": settings.snap,
        "show_footer": settings.show_footer,
        "show_quick_bar": settings.show_quick_bar,
        "print_horizontal": settings.print_horizontal,
        "allow_rotate": settings.allow_rotate,
        "save_limit_size": settings.save_limit_size,
        "save_limit_bytes": settings.save_limit_bytes,
        "save_open_folder": settings.save_open_folder,
        "save_max_kb": settings.save_max_kb,
        "logo_place": settings.logo_place,
        "quick_tunes": settings.quick_tunes,
        "logo_path": settings.logo_path,
        "logo_auto": settings.logo_auto,
        "email_subject": settings.email_subject,
        "email_ssl": settings.email_ssl,
        "send_log": settings.send_log,
        "external_save_price": settings.external_save_price,
        "icc_apply": settings.icc_apply,
        "sort_key": settings.sort_key,
        "sort_reverse": settings.sort_reverse,
        "file_panel_width": settings.file_panel_width,
        "preview_height": settings.preview_height,
        "left_sizes": settings.left_sizes,
        "currency": settings.currency,
        "theme": settings.theme if settings.theme in ("auto", "light", "dark") else "auto",
        "colors": settings.colors,
        "retouch_koeff": settings.retouch_koeff,
    }
    SETTINGS_PATH.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_journal() -> list[JournalLine]:
    if not JOURNAL_PATH.exists():
        return []
    data = yaml.safe_load(JOURNAL_PATH.read_text(encoding="utf-8")) or []
    lines = []
    for item in data:
        lines.append(
            JournalLine(
                str(item.get("at", "")),
                str(item.get("number", "")),
                str(item.get("formats", "")),
                int(item.get("copies", 0)),
                float(item.get("amount", 0)),
                str(item.get("day", "")),
            )
        )
    return lines


def save_journal(lines: list[JournalLine], copy_to: Path | None = None) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = [line.__dict__ for line in lines]
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    JOURNAL_PATH.write_text(text, encoding="utf-8")
    if copy_to is not None:
        copy_to.mkdir(parents=True, exist_ok=True)
        (copy_to / "журнал.yaml").write_text(text, encoding="utf-8")


def add_journal(lines: list[JournalLine], number: str, formats: str, copies: int, amount: float, folder: Path | None) -> list[JournalLine]:
    today = date.today().isoformat()
    lines = list(lines)
    lines.append(JournalLine(datetime.now().strftime("%H:%M"), number, formats, copies, amount, today))
    save_journal(lines, folder)
    return lines


def day_total(lines: list[JournalLine], day: str | None = None) -> tuple[int, float]:
    stamp = day or date.today().isoformat()
    chosen = [line for line in lines if line.day == stamp]
    return sum(line.copies for line in chosen), sum(line.amount for line in chosen)


def save_scheme(name: str, doc: SheetDoc) -> Path:
    SCHEMES_DIR.mkdir(parents=True, exist_ok=True)
    slots = [
        {"x": item.x_mm, "y": item.y_mm, "w": item.w_mm, "h": item.h_mm, "rotation": item.rotation, "kind": item.kind}
        for item in doc.items
    ]
    path = SCHEMES_DIR / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "paper_id": doc.paper_id,
                "custom_w": doc.custom_w,
                "custom_h": doc.custom_h,
                "landscape": doc.landscape,
                "margin_mm": doc.margin_mm,
                "margin_top_mm": doc.margin_top_mm,
                "margin_right_mm": doc.margin_right_mm,
                "margin_bottom_mm": doc.margin_bottom_mm,
                "margin_left_mm": doc.margin_left_mm,
                "gap_mm": doc.gap_mm,
                "slots": slots,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def load_scheme(path: Path, doc: SheetDoc) -> SheetDoc:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slots = data.get("slots") or []
    items = []
    for index, item in enumerate(doc.items):
        if index < len(slots):
            slot = slots[index]
            items.append(
                SheetItem(
                    item.id,
                    item.kind,
                    float(slot.get("x", item.x_mm)),
                    float(slot.get("y", item.y_mm)),
                    float(slot.get("w", item.w_mm)),
                    float(slot.get("h", item.h_mm)),
                    item.ready_id,
                    int(slot.get("rotation", item.rotation)),
                    item.text,
                    item.path,
                )
            )
        else:
            items.append(item)
    return SheetDoc(
        str(data.get("paper_id", doc.paper_id)),
        float(data.get("custom_w", doc.custom_w)),
        float(data.get("custom_h", doc.custom_h)),
        bool(data.get("landscape", doc.landscape)),
        float(data.get("margin_mm", doc.margin_mm)),
        float(data.get("gap_mm", doc.gap_mm)),
        doc.snap,
        doc.grid,
        doc.grid_mm,
        doc.cut_marks,
        items,
        float(data.get("margin_top_mm", data.get("margin_mm", doc.margin_top_mm))),
        float(data.get("margin_right_mm", data.get("margin_mm", doc.margin_right_mm))),
        float(data.get("margin_bottom_mm", data.get("margin_mm", doc.margin_bottom_mm))),
        float(data.get("margin_left_mm", data.get("margin_mm", doc.margin_left_mm))),
        str(data.get("cut_style", doc.cut_style)),
        float(data.get("cut_length_mm", doc.cut_length_mm)),
        float(data.get("cut_width_mm", doc.cut_width_mm)),
    )


def send_report(settings: Settings, text: str) -> str:
    """Письмо уходит только если заданы адрес и сервер. Иначе остаётся файл отчёта."""
    if not settings.send_log:
        return "отчёт записан в файл"
    if not settings.email or not settings.smtp_host:
        return "письмо не отправлено: нет адреса или сервера"
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["To"] = settings.email
    message["From"] = settings.smtp_user or settings.email
    message["Subject"] = settings.email_subject or "Отчёт фото на документы"
    message.set_content(text)
    if settings.email_ssl and settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
    with smtp:
        if settings.email_ssl and settings.smtp_port != 465:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    return "отчёт отправлен"


def report_text(lines: list[JournalLine], day: str | None = None) -> str:
    stamp = day or date.today().isoformat()
    copies, amount = day_total(lines, stamp)
    rows = [f"Отчёт {stamp}", f"Комплектов: {copies}", f"Сумма: {amount:.0f}"]
    for line in lines:
        if line.day == stamp:
            rows.append(f"{line.at}  {line.number}  {line.formats}  {line.copies}  {line.amount:.0f}")
    return "\n".join(rows) + "\n"
