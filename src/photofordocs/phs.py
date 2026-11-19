"""Перенос настроек в архиве (*.phs): свой бандл и совместимые файлы переноса.

Ключи лицензии из чужого архива не читаются. Каналы проверки обновлений сторонних программ не используются.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from photofordocs.clothes import install_clothes
from photofordocs import formats as formats_mod
from photofordocs.formats import parse_photolab_formats, save_format_order
from photofordocs import session
from photofordocs.session import PAPER_MM, Settings, load_settings, save_settings

_BUNDLE_ROOT = "photofordocs-bundle"
_NATIVE_MARK = f"{_BUNDLE_ROOT}/settings.yaml"

_SKIP_NAMES = ("license", "licence", "лиценз")


@dataclass
class ImportReport:
    formats: int = 0
    prices: int = 0
    clothes: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = []
        if self.formats:
            lines.append(f"Форматов: {self.formats}.")
        if self.prices:
            lines.append(f"Цен: {self.prices}. Цена в файле хранится в тысячных долях рубля.")
        if self.clothes:
            lines.append(f"Комплектов одежды: {self.clothes}.")
        lines.extend(self.notes)
        return "\n".join(lines)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def _sections(text: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith(";"):
            continue
        if item.startswith("[") and item.endswith("]"):
            current = item[1:-1]
            found.setdefault(current, {})
            continue
        if current and "=" in item:
            key, value = item.split("=", 1)
            found[current][key.strip()] = value.strip()
    return found


def _number(value: str, default: float = 0.0) -> float:
    try:
        return float(value.replace(",", "."))
    except (TypeError, ValueError):
        return default


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip() not in {"", "0", "false", "False"}


def _colorref(value: str) -> str | None:
    try:
        number = int(value)
    except ValueError:
        return None
    if number <= 0:
        return None
    number &= 0xFFFFFF
    red = number & 255
    green = (number >> 8) & 255
    blue = (number >> 16) & 255
    return f"#{red:02X}{green:02X}{blue:02X}"


def _printer_name(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe"):
        raw = raw[2:]
    chars: list[str] = []
    for index in range(0, min(len(raw), 400), 2):
        pair = raw[index : index + 2]
        if pair == b"\x00\x00":
            break
        chars.append(pair.decode("utf-16le", errors="ignore"))
    return "".join(chars).strip()


def _price_rubles(record: dict[str, str]) -> float:
    raw = _number(record.get("th", "0"))
    if raw <= 0:
        raw = _number(record.get("th1", "0"))
    if raw <= 0:
        return 0.0
    return round(raw / 1000.0, 2)


def read_members(path: Path) -> tuple[dict[str, dict[str, str]], str, list[tuple[str, bytes]]]:
    """Секции ini из архива переноса, текст форматов и слоты принтеров."""
    sections: dict[str, dict[str, str]] = {}
    format_chunks: list[str] = []
    slots: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(archive.namelist(), key=lambda item: (not item.startswith("formats/"), item))
        for name in names:
            lowered = name.lower()
            if any(mark in lowered for mark in _SKIP_NAMES):
                continue
            raw = archive.read(name)
            if lowered.startswith("prnslot") and lowered.endswith(".cfg"):
                slots.append((_printer_name(raw) or name, raw))
                continue
            if lowered.endswith(".log") or lowered.endswith("update.ini"):
                continue
            text = _decode(raw)
            if lowered.startswith("formats/"):
                format_chunks.append(text)
                continue
            for title, values in _sections(text).items():
                sections.setdefault(title, {}).update(values)
    return sections, "\n".join(format_chunks), slots


def _paper_id(width: float, height: float) -> str:
    for ident, size in PAPER_MM.items():
        if abs(size[0] - width) < 0.6 and abs(size[1] - height) < 0.6:
            return ident
    return "custom"


def _cut_style(work: dict[str, str]) -> str:
    if not _flag(work.get("DrawCuttingLine"), True):
        return "none"
    if _flag(work.get("CuttingLineOnlyCorners")):
        return "corners"
    if _flag(work.get("CuttingLinePatterned"), True):
        return "dash"
    return "box"


def _sort(value: str) -> tuple[str, bool]:
    kind = int(_number(value))
    return {
        0: ("name", False),
        1: ("name", True),
        2: ("date", True),
        3: ("date", False),
    }.get(kind, ("name", False))


def apply_settings(settings: Settings, sections: dict[str, dict[str, str]], slots: list[tuple[str, bytes]]) -> list[str]:
    work = sections.get("Workdata", {})
    common = sections.get("Common", {})
    paper = sections.get("Paper", {})
    mail = sections.get("LogToEmail", {})
    face = sections.get("DetectFace", {})
    save_file = sections.get("frmSaveToFile", {})
    notes: list[str] = []

    last_path = work.get("LastPath", "")
    if last_path:
        settings.archive_root = last_path
    settings.photo_base = common.get("Path to PhotoBase", settings.photo_base)
    settings.sheets_folder = common.get("Path to PapersBase", settings.sheets_folder)
    settings.temp_folder = common.get("Temp Path", settings.temp_folder)
    settings.editor = common.get("Path to PhotoEditor", settings.editor)
    settings.clothes_dir = common.get("clothesPath", settings.clothes_dir)
    settings.search_folder = common.get("CustomPapersBase", settings.search_folder)
    settings.watch_folder = work.get("AutoAddPath", settings.watch_folder)

    settings.icc = common.get("ICCOutputFName", settings.icc)
    settings.icc_apply = _flag(common.get("ICCCorrectMon2Out")) or _flag(common.get("ICCUseMon2Out"))
    settings.printer_name = common.get("PrinterName", settings.printer_name)
    names = []
    for key, value in sections.items():
        if key.startswith("Printer") and value.get("Name", "").strip():
            names.append(value["Name"].strip())
    for slot_name, _raw in slots:
        if slot_name and slot_name not in names:
            names.append(slot_name)
    settings.printers = names
    if slots:
        folder = session.CONFIG_DIR / "printers"
        folder.mkdir(parents=True, exist_ok=True)
        for index, (slot_name, raw) in enumerate(slots):
            safe = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in slot_name) or f"slot-{index}"
            (folder / f"{index}-{safe}.cfg").write_bytes(raw)
        notes.append("Имя принтера выбирается при печати. Память драйвера сохранена рядом с настройками, а размер листа берётся из файла.")

    width = _number(paper.get("PaperWidth", "100"), 100)
    height = _number(paper.get("PaperHeight", "150"), 150)
    settings.default_paper = _paper_id(width, height)
    if settings.default_paper == "custom":
        notes.append(f"Лист {width:g}×{height:g} мм не совпал с готовым размером, поля всё равно перенесены.")
    settings.default_landscape = _flag(paper.get("PaperOrientation"))
    settings.margin_top_mm = _number(paper.get("TopMargin", "4"), 4)
    settings.margin_right_mm = _number(paper.get("RightMargin", "4"), 4)
    settings.margin_bottom_mm = _number(paper.get("BottomMargin", "4"), 4)
    settings.margin_left_mm = _number(paper.get("LeftMargin", "4"), 4)
    settings.printer_dpi = max(72, int(_number(paper.get("DPI", "300"), 300)))
    settings.export_dpi = max(72, int(_number(save_file.get("DefaultDPI", "300"), 300)))
    settings.watch_min_kb = int(_number(work.get("minFileSizeWatching", "0")))
    settings.jpeg_photo = int(_number(common.get("JPEGComprPhoto", "92"), 92))
    settings.jpeg_sheet = int(_number(common.get("JPEGComprPaper", "92"), 92))

    settings.tone_brightness = _number(work.get("Brightness", "0"))
    settings.tone_contrast = _number(work.get("Contrast", "0"))
    settings.tone_gamma = _number(work.get("Gamma", "0"))
    settings.tone_saturation = _number(work.get("Saturation", "0"))
    settings.tone_sharpness = _number(work.get("Sharpness", "0"))
    balance = []
    for row in range(3):
        for column in range(1, 4):
            balance.append(_number(work.get(f"TB{row}_{column}", "0")))
    settings.tone_balance = balance
    settings.ground_threshold = _number(work.get("GrndThreshold", "0"))
    settings.brush_px = int(_number(work.get("seBrushGrndSize", "0")))
    if 0 < settings.ground_threshold <= 20:
        notes.append(
            f"Порог фона {settings.ground_threshold:g} переведён в чувствительность {min(90, settings.ground_threshold * 10):g}: "
            "у источника другая шкала."
        )

    settings.cut_style = _cut_style(work)
    settings.cut_length_mm = _number(work.get("LineCornerLengthMM", "3"), 3) or 3
    settings.white_frame_mm = _number(common.get("WhiteFrameWidth", "0"))
    advert = common.get("AdvertiseF", "").strip()
    if advert in {"", "#!@"}:
        advert = common.get("AdvertiseP", "").strip()
    if advert in {"", "#!@"}:
        advert = sections.get("LOG", {}).get("Text", "").strip()
    if advert == "{Текст колонтитула}":
        advert = ""
    settings.advert_text = advert
    settings.ask_before_delete = _flag(work.get("AskBeforeErase"), True)
    settings.ask_place = _flag(work.get("AskPhotoPlace"), True)
    settings.confirm_overwrite = _flag(work.get("CheckOverride"), True)
    settings.face_detect = _flag(face.get("Enabled"), True)
    settings.show_print_dialog = _flag(work.get("ShowPrnDlg"), True)
    settings.clear_after_print = _flag(paper.get("ClearPaperAfterPrint"))
    settings.check_margins = _flag(work.get("CheckMargins"), True)
    settings.align_center = _flag(work.get("AlignmentPhoto_Center"))
    settings.logo_path = work.get("LogoFileName", "")
    settings.logo_auto = _flag(work.get("LogoAutoAdd"))
    settings.currency = common.get("Valute", settings.currency) or "₽"
    settings.external_save_price = _number(common.get("PriceSaveToExtFile", "0"))
    settings.retouch_koeff = _number(common.get("retouchKoeff", "0"))
    settings.file_panel_width = int(_number(work.get("FilePanelWidth", "0")))
    settings.preview_height = int(_number(work.get("FilePreviewHeight", "0")))
    settings.sort_key, settings.sort_reverse = _sort(work.get("SortType", "0"))

    settings.email = mail.get("EmailTo", settings.email)
    settings.smtp_host = mail.get("EMailHost", settings.smtp_host)
    settings.smtp_port = int(_number(mail.get("EMailPort", str(settings.smtp_port)), settings.smtp_port))
    settings.smtp_user = mail.get("EMailUsername", settings.smtp_user)
    settings.smtp_password = mail.get("EMailPassword", settings.smtp_password)
    settings.email_ssl = _flag(mail.get("EmailUseSSL"), True)
    settings.email_subject = mail.get("EmailSubject", settings.email_subject)
    settings.send_log = _flag(mail.get("SendLogToEmail"))

    colors = []
    count = int(_number(sections.get("Colors", {}).get("Count", "0")))
    palette = sections.get("Colors", {})
    for index in range(1, count + 1):
        name = palette.get(f"Name{index}", "").strip()
        color = _colorref(palette.get(f"Value{index}", ""))
        if name and color and not name.startswith("CRPHL"):
            colors.append({"name": name, "color": color})
    settings.colors = colors

    # Кадры последней рамки — положение одного снимка, а не правило для всех.
    if any(work.get(key) for key in ("FrameLeft", "TopX", "BottomX")):
        notes.append("Координаты последней рамки не применяются ко всем снимкам: это след одного кадра.")
    notes.append(
        "Окна линейки, гистограммы и внешнего редактора из предыдущей установки не повторяются. "
        "Ширина списка файлов и высота превью переносятся."
    )
    notes.append("Проверка обновлений сторонней программы не переносится.")
    return notes


def _native_bundle(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        return _NATIVE_MARK in archive.namelist()


def export_transfer(path: Path, *, settings: bool, formats: bool) -> None:
    if not settings and not formats:
        raise ValueError("Отметьте, что именно сохранять.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_BUNDLE_ROOT}/README.txt", "Перенос настроек PhotoForDocs.\n")
        if settings and session.SETTINGS_PATH.is_file():
            archive.write(session.SETTINGS_PATH, _NATIVE_MARK)
            printers = session.CONFIG_DIR / "printers"
            if printers.is_dir():
                for item in sorted(printers.glob("*.cfg")):
                    archive.write(item, f"{_BUNDLE_ROOT}/printers/{item.name}")
        if formats:
            if formats_mod.ORDER_PATH.is_file():
                archive.write(formats_mod.ORDER_PATH, f"{_BUNDLE_ROOT}/format-order.yaml")
            if formats_mod.USER_DIR.is_dir():
                for item in sorted(formats_mod.USER_DIR.glob("*.yaml")):
                    archive.write(item, f"{_BUNDLE_ROOT}/formats/{item.name}")


def _import_native(path: Path, *, settings: bool, formats: bool) -> ImportReport:
    report = ImportReport()
    current = load_settings()
    with zipfile.ZipFile(path) as archive:
        if settings:
            try:
                raw = archive.read(_NATIVE_MARK)
            except KeyError as error:
                raise ValueError("В файле нет настроек программы.") from error
            session.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            session.SETTINGS_PATH.write_bytes(raw)
            current = load_settings()
            prefix = f"{_BUNDLE_ROOT}/printers/"
            printers = session.CONFIG_DIR / "printers"
            for name in archive.namelist():
                if name.startswith(prefix) and name.endswith(".cfg"):
                    printers.mkdir(parents=True, exist_ok=True)
                    (printers / Path(name).name).write_bytes(archive.read(name))
            report.notes.append("Настройки программы записаны.")
            report.notes.extend(_clothes_note(current))
        if formats:
            prefix = f"{_BUNDLE_ROOT}/formats/"
            names = [name for name in archive.namelist() if name.startswith(prefix) and name.endswith(".yaml")]
            if not names:
                raise ValueError("В файле нет пользовательских форматов.")
            formats_mod.USER_DIR.mkdir(parents=True, exist_ok=True)
            count = 0
            for name in names:
                target = formats_mod.USER_DIR / Path(name).name
                target.write_bytes(archive.read(name))
                count += 1
            order_name = f"{_BUNDLE_ROOT}/format-order.yaml"
            if order_name in archive.namelist():
                formats_mod.ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
                formats_mod.ORDER_PATH.write_bytes(archive.read(order_name))
            report.formats = count
    if settings:
        save_settings(current)
    return report


def import_transfer(path: Path, *, settings: bool = True, formats: bool = True) -> ImportReport:
    if not settings and not formats:
        raise ValueError("Отметьте, что именно восстанавливать.")
    if _native_bundle(path):
        return _import_native(path, settings=settings, formats=formats)
    return import_phs(path, settings=settings, formats=formats)


def import_phs(path: Path, *, settings: bool = True, formats: bool = True) -> ImportReport:
    if not zipfile.is_zipfile(path):
        raise ValueError("Файл переноса должен быть архивом (.phs).")
    sections, format_text, slots = read_members(path)
    report = ImportReport()
    current = load_settings()
    if formats:
        from photofordocs.formats import _ini_records, save_user_format

        records = [item for item in _ini_records(format_text) if item.get("Name") and item.get("Width") and item.get("Height")]
        records.sort(key=lambda item: _number(item.get("Index", "999"), 999))
        if not records:
            raise ValueError("В файле нет форматов с названием и размерами.")
        ordered = parse_photolab_formats("\n".join(_format_block(item) for item in records))
        prices = {}
        for record in records:
            ext = record.get("Ext", "").lstrip(".").lower()
            price = _price_rubles(record)
            if ext and price:
                prices[ext] = price
        priced = 0
        for fmt in ordered:
            save_user_format(fmt)
            price = prices.get(fmt.id)
            if price:
                current.prices[fmt.id] = price
                priced += 1
        save_format_order([fmt.id for fmt in ordered])
        report.formats = len(ordered)
        report.prices = priced
    if settings:
        report.notes.extend(apply_settings(current, sections, slots))
        report.notes.extend(_clothes_note(current))
    if settings or formats:
        save_settings(current)
    return report


def _format_block(record: dict[str, str]) -> str:
    lines = ["[Формат]"]
    lines.extend(f"{key}={value}" for key, value in record.items())
    return "\n".join(lines)


def _clothes_note(settings: Settings) -> list[str]:
    folder = Path(settings.clothes_dir) if settings.clothes_dir else None
    if folder is None or not folder.is_dir():
        return []
    count = install_clothes(folder)
    if count:
        return [f"Из папки одежды разобрано комплектов: {count}."]
    return ["Папка одежды указана, но готовых картинок в ней не нашлось."]
