"""Каталог форматов. Числа лежат в YAML и правятся без пересборки."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

BUNDLED_DIR = Path(__file__).resolve().parent / "formats"
USER_DIR = Path.home() / ".config" / "photofordocs" / "formats"
ORDER_PATH = USER_DIR.parent / "format-order.yaml"


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    width_mm: float
    height_mm: float
    head_height_mm: float
    chin_bottom_mm: float
    background: str
    note: str
    builtin: bool = True
    have_corner: bool = False
    corner_kind: int = 0
    corner_placement: int = 0
    corner_rx_mm: float = 18.0
    corner_ry_mm: float = 18.0
    show_in_menu: bool = True
    extension: str = "jpg"
    hotkey: str = ""
    colored: bool = True
    default_count: int = 1
    place_horizontal: bool = False
    paper_kind: str = "Матовая"
    two_points: bool = False
    overhead_mm: float = 0.0
    overhead2_mm: float = 0.0
    face_height_mm: float = 0.0
    retouch: bool = False
    print_cx_mm: float = 0.0
    print_cy_mm: float = 0.0
    max_file_kb: int = 0
    file_dpi: int = 0
    kind: int = 0
    legacy_ext: tuple[str, ...] = ()
    tier: str = "extended"

    @property
    def aspect(self) -> float:
        return self.width_mm / self.height_mm

    @property
    def head_ratio(self) -> float:
        return self.head_height_mm / self.height_mm

    @property
    def chin_ratio(self) -> float:
        return self.chin_bottom_mm / self.height_mm

    @property
    def crop_under_mm(self) -> float:
        """Нижнее поле в окне кадра.

        В справочнике PhotoLab у форматов с уголком в INI пишут UnderHeadHeight=21 —
        это запас под обрез уголка на листе, а не зона, которую показывают под подбородком
        при кадрировании (там как у цветной 3×4, OverHeadHeight2).
        """
        if self.have_corner:
            if self.overhead2_mm > 0:
                return self.overhead2_mm
            if self.overhead_mm > 0:
                return self.overhead_mm
        return self.chin_bottom_mm

    @property
    def crop_chin_ratio(self) -> float:
        return self.crop_under_mm / self.height_mm

    @property
    def frame_head_height_mm(self) -> float:
        """Высота головы в окне кадра.

        У 3×4 с уголком в INI head=15 мм (40−4−21), но при кадрировании PhotoLab
        ведёт себя как у цветной 3×4: 32 мм между макушкой и подбородком, 4 мм снизу.
        """
        if self.have_corner and not self.two_points:
            top = self.overhead2_mm if self.overhead2_mm > 0 else self.height_mm * self.crown_ratio
            return max(self.height_mm - top - self.crop_under_mm, 0.1)
        return self.head_height_mm

    @property
    def frame_head_ratio(self) -> float:
        return self.frame_head_height_mm / self.height_mm

    @property
    def crown_ratio(self) -> float:
        return 1.0 - self.head_ratio - self.chin_ratio

    @property
    def chin_line(self) -> float:
        """Доля высоты кадра от верхнего края до подбородка."""
        return 1.0 - self.chin_ratio

    @property
    def eye_ratio(self) -> float:
        """Линия глаз: чуть выше середины головы, как в трёхточечном трафарете."""
        return self.crown_ratio + self.head_ratio * 0.55

    @property
    def eye_to_chin_ratio(self) -> float:
        """Лицевая часть: доля кадра от линии глаз до подбородка.

        Если в формате записана лицевая высота трёхточечного трафарета, берётся она.
        """
        if self.face_height_mm > 0:
            return min(max(self.face_height_mm / self.height_mm, 0.05), max(self.head_ratio * 0.95, 0.05))
        return self.chin_line - self.eye_ratio

    def summary(self) -> str:
        return f"{self.width_mm:g}×{self.height_mm:g} мм"

    def corner_text(self) -> str:
        """Как уголок записан в формате. Без своей настройки берётся стандарт 18×18 мм."""
        radius = f"{self.corner_rx_mm:g}×{self.corner_ry_mm:g} мм"
        if not self.have_corner or self.corner_placement == 0:
            return f"своего уголка нет, стандарт {radius}"
        shape = {0: "прямой", 1: "круглый, центр внутри", 3: "круг для печати"}.get(self.corner_kind, "круглый, центр снаружи")
        side = {2: "снизу слева", 8: "снизу справа", 15: "по всем углам"}.get(self.corner_placement, "как задано")
        return f"{shape}, {side}, {radius}"


def _parse_legacy_ext(data: dict) -> tuple[str, ...]:
    raw = data.get("legacy_ext")
    if raw is None:
        return ()
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        return ()
    return tuple(item.strip().lower() for item in items if str(item).strip())


def _parse(path: Path, builtin: bool, *, default_tier: str | None = None) -> Format | None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} должен быть словарём")
    if data.get("deleted"):
        return None
    width = float(data["width_mm"])
    height = float(data["height_mm"])
    head = float(data["head_height_mm"])
    chin = float(data["chin_bottom_mm"])
    if min(width, height, head, chin) <= 0:
        raise ValueError(f"{path}: размеры должны быть больше нуля")
    if head + chin >= height:
        raise ValueError(f"{path}: голова и поле под подбородком не умещаются в кадр")
    tier = str(data.get("tier", default_tier or "extended"))
    show_raw = data.get("show_in_menu")
    if show_raw is None:
        show_in_menu = not (builtin and tier == "extended")
    else:
        show_in_menu = bool(show_raw)
    return Format(
        id=str(data["id"]),
        name=str(data["name"]),
        width_mm=width,
        height_mm=height,
        head_height_mm=head,
        chin_bottom_mm=chin,
        background=str(data.get("background", "#FFFFFF")),
        note=scrub_import_note(str(data.get("note", ""))),
        builtin=builtin,
        have_corner=bool(data.get("have_corner", False)),
        corner_kind=int(data.get("corner_kind", 0)),
        corner_placement=int(data.get("corner_placement", 0)),
        corner_rx_mm=float(data.get("corner_rx_mm", 18)),
        corner_ry_mm=float(data.get("corner_ry_mm", 18)),
        show_in_menu=show_in_menu,
        extension=str(data.get("extension", "jpg")),
        hotkey=str(data.get("hotkey", "")),
        colored=bool(data.get("colored", True)),
        default_count=int(data.get("default_count", 1)),
        place_horizontal=bool(data.get("place_horizontal", False)),
        paper_kind=str(data.get("paper_kind", "Матовая")),
        two_points=bool(data.get("two_points", False)),
        overhead_mm=float(data.get("overhead_mm", 0)),
        overhead2_mm=float(data.get("overhead2_mm", 0)),
        face_height_mm=float(data.get("face_height_mm", 0)),
        retouch=bool(data.get("retouch", False)),
        print_cx_mm=float(data.get("print_cx_mm", 0)),
        print_cy_mm=float(data.get("print_cy_mm", 0)),
        max_file_kb=int(data.get("max_file_kb", 0) or 0),
        file_dpi=int(data.get("file_dpi", 0) or 0),
        kind=int(data.get("kind", 0) or 0),
        legacy_ext=_parse_legacy_ext(data),
        tier=tier,
    )


_CORE_ORDER = (
    "passport-rf-2013",
    "photo-3x4-color",
    "photo-9x12-color",
    "photo-4x6-no-corner",
    "photo-3x4-bw",
    "photo-3x4-corner-right",
    "photo-45x60-no-corner",
    "military-ticket-enlisted",
    "photo-9x12",
    "photo-3x4-corner-left",
)

_ORDER = _CORE_ORDER

_IMPORT_NOTE_PREFIXES = (
    "Перенесено из справочника оригинальной программы.",
    "Импортировано из внешнего справочника (INI).",
)


def scrub_import_note(note: str) -> str:
    text = (note or "").strip()
    for prefix in _IMPORT_NOTE_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip()
    return text


def _legacy_keys(fmt: Format) -> set[str]:
    keys = {item.lower() for item in fmt.legacy_ext if item}
    ext = fmt.extension.lstrip(".").lower()
    if ext and ext != "jpg":
        keys.add(ext)
    if fmt.id.lower().startswith("jp_"):
        keys.add(fmt.id.lower())
    return keys


def build_alias_map(formats: list[Format]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for fmt in formats:
        if fmt.tier != "core":
            continue
        for key in _legacy_keys(fmt):
            mapping.setdefault(key, fmt.id)
    return mapping


def resolve_format_id(raw: str, alias_map: dict[str, str], formats: list[Format] | None = None) -> str:
    key = (raw or "").strip()
    if not key:
        return raw
    if formats:
        by_id = {item.id: item for item in formats}
        if key in by_id:
            return key
        low = key.lower()
        for ident in by_id:
            if ident.lower() == low:
                return ident
    low = key.lower()
    return alias_map.get(low, raw)


def format_price(prices: dict[str, float], raw: str, formats: list[Format], alias_map: dict[str, str]) -> float:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        item = (value or "").strip()
        if item and item not in seen:
            seen.add(item)
            candidates.append(item)

    add(raw)
    add(resolve_format_id(raw, alias_map, formats))
    matched = format_by_id_or_legacy(raw, formats, alias_map)
    if matched is not None:
        add(matched.id)
        for legacy in _legacy_keys(matched):
            add(legacy)
    for ident in candidates:
        if ident in prices:
            return float(prices[ident])
    return 0.0


def format_by_id_or_legacy(raw: str, formats: list[Format], alias_map: dict[str, str]) -> Format | None:
    resolved = resolve_format_id(raw, alias_map, formats)
    by_id = {item.id: item for item in formats}
    found = by_id.get(resolved) or by_id.get(raw)
    if found is not None:
        return found
    low = raw.lower()
    for fmt in formats:
        if low in _legacy_keys(fmt):
            return fmt
    return None


def user_has_format_profile() -> bool:
    if not USER_DIR.exists():
        return False
    for path in USER_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict) and not raw.get("deleted"):
            return True
    return False


def formats_for_editor(formats: list[Format], *, user_has_profile: bool | None = None) -> list[Format]:
    """Справочник форматов: без профиля — только стартовое ядро (tier core)."""
    if user_has_profile is None:
        user_has_profile = user_has_format_profile()
    if user_has_profile:
        return list(formats)
    return [item for item in formats if item.builtin and item.tier == "core"]


def visible_formats(formats: list[Format], *, user_has_profile: bool | None = None) -> list[Format]:
    if user_has_profile is None:
        user_has_profile = user_has_format_profile()
    if not user_has_profile:
        return [item for item in formats if item.tier == "core" and item.show_in_menu]
    user_legacy: set[str] = set()
    for item in formats:
        if not item.builtin:
            user_legacy.update(_legacy_keys(item))
    visible: list[Format] = []
    for item in formats:
        if not item.show_in_menu:
            continue
        if not item.builtin:
            visible.append(item)
            continue
        if item.tier == "core" and _legacy_keys(item) & user_legacy:
            continue
        visible.append(item)
    return visible


def load_formats() -> list[Format]:
    found: dict[str, Format] = {}
    hidden: set[str] = set()
    for path in sorted(BUNDLED_DIR.glob("*.yaml")):
        item = _parse(path, builtin=True, default_tier="core")
        if item is not None:
            found[item.id] = item
    if USER_DIR.exists():
        for path in sorted(USER_DIR.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and raw.get("deleted"):
                hidden.add(str(raw.get("id", path.stem)))
                found.pop(str(raw.get("id", path.stem)), None)
                continue
            raw_note = str((raw if isinstance(raw, dict) else {}).get("note", "") or "").strip()
            item = _parse(path, builtin=False)
            if item is not None:
                if raw_note != item.note:
                    save_user_format(item)
                found[item.id] = item
    for ident in hidden:
        found.pop(ident, None)
    order = _load_order()
    rank = {ident: index for index, ident in enumerate(order)}

    def sort_key(item: Format) -> tuple:
        if item.id in rank:
            return (0, rank[item.id], item.name)
        if item.id in _ORDER:
            return (1, _ORDER.index(item.id), item.name)
        return (2, 100, item.name)

    return sorted(found.values(), key=sort_key)


def _load_order() -> list[str]:
    if not ORDER_PATH.exists():
        return []
    loaded = yaml.safe_load(ORDER_PATH.read_text(encoding="utf-8")) or []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def save_format_order(idents: list[str]) -> None:
    ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDER_PATH.write_text(yaml.safe_dump(idents, allow_unicode=True, sort_keys=False), encoding="utf-8")


def delete_format(fmt: Format) -> None:
    """Свой или импортированный формат — файл в профиле удаляется. Встроенный — пометка deleted."""
    path = USER_DIR / f"{fmt.id}.yaml"
    if fmt.builtin:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"id": fmt.id, "deleted": True}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return
    if path.is_file():
        path.unlink()


def count_user_format_files() -> int:
    if not USER_DIR.exists():
        return 0
    return sum(1 for path in USER_DIR.glob("*.yaml") if path.is_file())


def clear_user_format_profile(*, reset_order: bool = True) -> int:
    """Удаляет все yaml в профиле (импорт, свои, пометки скрытия встроенных)."""
    removed = 0
    if USER_DIR.exists():
        for path in USER_DIR.glob("*.yaml"):
            path.unlink(missing_ok=True)
            removed += 1
    if reset_order and ORDER_PATH.is_file():
        ORDER_PATH.unlink(missing_ok=True)
    return removed


def save_user_format(fmt: Format) -> Path:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_DIR / f"{fmt.id}.yaml"
    payload = {
        "id": fmt.id,
        "name": fmt.name,
        "width_mm": fmt.width_mm,
        "height_mm": fmt.height_mm,
        "head_height_mm": fmt.head_height_mm,
        "chin_bottom_mm": fmt.chin_bottom_mm,
        "background": fmt.background,
        "note": fmt.note,
        "have_corner": fmt.have_corner,
        "corner_kind": fmt.corner_kind,
        "corner_placement": fmt.corner_placement,
        "corner_rx_mm": fmt.corner_rx_mm,
        "corner_ry_mm": fmt.corner_ry_mm,
        "show_in_menu": fmt.show_in_menu,
        "extension": fmt.extension,
        "hotkey": fmt.hotkey,
        "colored": fmt.colored,
        "default_count": fmt.default_count,
        "place_horizontal": fmt.place_horizontal,
        "paper_kind": fmt.paper_kind,
        "two_points": fmt.two_points,
        "overhead_mm": fmt.overhead_mm,
        "overhead2_mm": fmt.overhead2_mm,
        "face_height_mm": fmt.face_height_mm,
        "retouch": fmt.retouch,
        "print_cx_mm": fmt.print_cx_mm,
        "print_cy_mm": fmt.print_cy_mm,
        "max_file_kb": fmt.max_file_kb,
        "file_dpi": fmt.file_dpi,
        "kind": fmt.kind,
        "legacy_ext": list(fmt.legacy_ext) if fmt.legacy_ext else None,
        "tier": fmt.tier,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _slug(name: str) -> str:
    chars: list[str] = []
    for char in name.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "format"


def _ini_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def _ini_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith(";"):
            continue
        if item.startswith("[") and item.endswith("]"):
            if current and current.get("Name") and current.get("Width") and current.get("Height"):
                records.append(current)
            current = {}
            continue
        if current is None or "=" not in item:
            continue
        key, value = item.split("=", 1)
        current[key.strip()] = value.strip()
    if current and current.get("Name") and current.get("Width") and current.get("Height"):
        records.append(current)
    return records


def _number(record: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(record.get(key, default))
    except ValueError:
        return default


def parse_photolab_formats(text: str) -> list[Format]:
    """Разбор справочника форматов в формате INI."""
    found: list[Format] = []
    used: dict[str, int] = {}
    for record in _ini_records(text):
        record = dict(record)
        name = record["Name"]
        kind = int(_number(record, "Kind"))
        dpi = _number(record, "DPI", 300) or 300
        raw_width = _number(record, "Width")
        raw_height = _number(record, "Height")
        pixel = kind == 1 or raw_width > 250 or raw_height > 250
        if pixel:
            # У электронного формата все линейные поля в пикселях, в том числе
            # короткие: верхнее поле в 40 px — это около 1,7 мм, а не 40 мм.
            for key in (
                "Width",
                "Height",
                "OverHeadHeight",
                "UnderHeadHeight",
                "OverHeadHeight2",
                "FaceHeight",
                "CornerRadiusX",
                "CornerRadiusY",
                "PrintCRDX",
                "PrintCRDY",
            ):
                value = _number(record, key)
                if value > 0:
                    record[key] = f"{value / dpi * 25.4:.2f}"
        width = _number(record, "Width")
        height = _number(record, "Height")
        if width <= 0 or height <= 0:
            continue
        two_points = _number(record, "isTwoPointsFrame") != 0
        overhead = _number(record, "OverHeadHeight")
        overhead2 = _number(record, "OverHeadHeight2", overhead)
        under = _number(record, "UnderHeadHeight", 1)
        face = _number(record, "FaceHeight")
        top = overhead if two_points else overhead2
        top = max(0.0, top)
        under = max(0.5, under)
        if top + under >= height:
            scale = (height - 1.0) / (top + under)
            top *= scale
            under *= scale
        head = height - top - under
        if face <= 0 or face >= head:
            face = round(head * 0.45, 2)
        extension = record.get("Ext", "").lstrip(".").lower()
        base = extension or f"imported-{_slug(name)}"
        used[base] = used.get(base, 0) + 1
        ident = base if used[base] == 1 else f"{base}-{used[base]}"
        paper = "Глянцевая" if _number(record, "PaperKind") == 1 else "Матовая"
        extension = extension or "jpg"
        hotkey = record.get("HotKey", "").strip()
        if hotkey.lower() in {"", "нет", "no"}:
            hotkey = ""
        limit = int(_number(record, "MaxFileSize"))
        note_parts: list[str] = []
        if pixel:
            note_parts.append(f"Электронный файл {raw_width:g}×{raw_height:g} px.")
        if limit:
            note_parts.append(f"Не больше {limit} КБ.")
        note = ". ".join(note_parts)
        legacy = (ident,) if ident.startswith("jp_") else ()
        found.append(
            Format(
                id=ident,
                name=name,
                width_mm=width,
                height_mm=height,
                head_height_mm=round(head, 2),
                chin_bottom_mm=round(under, 2),
                background="#FFFFFF",
                note=note,
                builtin=False,
                have_corner=bool(int(_number(record, "HaveCorner"))),
                corner_kind=int(_number(record, "CornerKind")),
                corner_placement=int(_number(record, "CornerPlacement")),
                corner_rx_mm=_number(record, "CornerRadiusX", _number(record, "CornerRadius", 18)) or 18,
                corner_ry_mm=_number(record, "CornerRadiusY", _number(record, "CornerRadius", 18)) or 18,
                show_in_menu=bool(int(_number(record, "ShowInMenu", 1))),
                extension=extension,
                hotkey=hotkey,
                colored=bool(int(_number(record, "Colored"))),
                default_count=max(1, int(_number(record, "DefaultCount", 1))),
                place_horizontal=bool(int(_number(record, "HorizPlacement"))),
                paper_kind=paper,
                two_points=two_points,
                overhead_mm=round(overhead, 2),
                overhead2_mm=round(overhead2, 2),
                face_height_mm=round(face, 2),
                retouch=bool(int(_number(record, "Retouch"))),
                print_cx_mm=_number(record, "PrintCRDX"),
                print_cy_mm=_number(record, "PrintCRDY"),
                max_file_kb=limit,
                file_dpi=int(dpi),
                kind=kind,
                legacy_ext=legacy,
                tier="extended",
            )
        )
    return found


def import_photolab_file(path: Path) -> list[Format]:
    """Кладёт форматы из ini в справочник программы. Папка со снимками на это не влияет."""
    loaded = parse_photolab_formats(_ini_text(path))
    if not loaded:
        raise ValueError("В файле нет форматов с названием и размерами.")
    for fmt in loaded:
        save_user_format(fmt)
    order = _load_order()
    known = set(order)
    for fmt in loaded:
        if fmt.id not in known:
            order.append(fmt.id)
            known.add(fmt.id)
    if order:
        save_format_order(order)
    return loaded
