"""Архив дня, журнал, схемы листа и положение кадров. Без окна."""

from __future__ import annotations

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
    gap_mm: float = 2.0
    snap: bool = True
    grid: bool = False
    grid_mm: float = 5.0
    cut_marks: bool = True
    items: list[SheetItem] = field(default_factory=list)
    margin_top_mm: float = 4.0
    margin_right_mm: float = 4.0
    margin_bottom_mm: float = 4.0
    margin_left_mm: float = 4.0


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
        snap_point(item.x_mm, xs, 1.2),
        snap_point(item.y_mm, ys, 1.2),
        item.w_mm,
        item.h_mm,
        item.ready_id,
        item.rotation,
        item.text,
        item.path,
    )


def move_selected(doc: SheetDoc, selected: set[str], dx: float, dy: float) -> SheetDoc:
    moved = []
    for item in doc.items:
        if item.id in selected:
            shifted = item.moved(dx, dy)
            shifted = snap_item(shifted, doc, doc.items)
            moved.append(shifted)
        else:
            moved.append(item)
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
    )


def day_folder(root: Path, when: date | None = None) -> Path:
    stamp = (when or date.today()).isoformat()
    folder = root / stamp
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
    """Папка готовых кадров рядом с исходной фотографией, как Ready в оригинале."""
    return source.parent / "Ready"


def format_file_piece(format_name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else " " for ch in format_name).strip()
    return cleaned or "формат"


def ready_photo_path(source: Path, format_name: str) -> Path:
    """Имя как у смены расширения: «снимок.Название формата.jpg» внутри Ready."""
    return ready_folder(source) / f"{source.stem}.{format_file_piece(format_name)}.jpg"


def ready_info_path(photo: Path) -> Path:
    return photo.with_name(photo.name + ".info")


def ready_belongs(source: Path, filename: str, stems: set[str]) -> bool:
    """Файл Ready относится к этому снимку, а не к соседнему с более длинным именем."""
    if not filename.lower().endswith(".jpg"):
        return False
    prefix = source.stem + "."
    if not filename.startswith(prefix):
        return False
    for stem in stems:
        if stem != source.stem and filename.startswith(stem + "."):
            return False
    return True


def read_ready(source: Path, stems: set[str] | None = None) -> list[dict]:
    folder = ready_folder(source)
    if not folder.is_dir():
        return []
    known = stems if stems is not None else {source.stem}
    records = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or not ready_belongs(source, path.name, known):
            continue
        piece = path.name[len(source.stem) + 1 : -4]
        info: dict = {}
        sidecar = ready_info_path(path)
        if sidecar.is_file():
            loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                info = loaded
        records.append(
            {
                "name": str(info.get("name") or piece),
                "path": str(path),
                "format_id": str(info.get("format_id") or piece),
                "printed": bool(info.get("printed")),
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
    search_folder: str = ""

    def price_for(self, format_id: str) -> float:
        return float(self.prices.get(format_id, 0))


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    data = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    settings = Settings()
    for key in (
        "archive_root",
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
    return settings


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "archive_root": settings.archive_root,
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
        "search_folder": settings.search_folder,
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
    )


def send_report(settings: Settings, text: str) -> str:
    """Письмо уходит только если заданы адрес и сервер. Иначе остаётся файл отчёта."""
    if not settings.email or not settings.smtp_host:
        return "письмо не отправлено: нет адреса или сервера"
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["To"] = settings.email
    message["From"] = settings.smtp_user or settings.email
    message["Subject"] = "Отчёт фото на документы"
    message.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
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
