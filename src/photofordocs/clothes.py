"""Свои рубашки для кадра и комплекты из папки, указанной в переносе PhotoLab."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

CLOTHES_DIR = Path.home() / ".config" / "photofordocs" / "clothes"


def shirt(name: str, body: tuple[int, int, int], collar: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (480, 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(40, 90), (120, 40), (200, 110), (240, 70), (280, 110), (360, 40), (440, 90), (400, 250), (80, 250)], fill=body + (255,))
    draw.polygon([(190, 78), (240, 150), (290, 78), (250, 60), (230, 60)], fill=collar + (255,))
    image.info["name"] = name
    return image


def builtins() -> list[tuple[str, Image.Image]]:
    return [
        ("Белая рубашка", shirt("white", (236, 236, 236), (250, 250, 250))),
        ("Голубая рубашка", shirt("blue", (186, 210, 232), (244, 248, 252))),
        ("Серый пиджак", shirt("grey", (90, 96, 104), (230, 230, 230))),
    ]


def user_clothes() -> list[tuple[str, Image.Image]]:
    if not CLOTHES_DIR.exists():
        return []
    found = []
    for path in sorted(CLOTHES_DIR.glob("*.png")):
        found.append((path.stem, Image.open(path).convert("RGBA")))
    return found


def all_clothes() -> list[tuple[str, Image.Image]]:
    return builtins() + user_clothes()


def _pngs(raw: bytes) -> list[Image.Image]:
    found = []
    signature = b"\x89PNG\r\n\x1a\n"
    start = 0
    while True:
        index = raw.find(signature, start)
        if index < 0:
            break
        end = raw.find(b"IEND", index)
        if end < 0:
            break
        try:
            image = Image.open(io.BytesIO(raw[index : end + 8]))
            image.load()
        except (OSError, ValueError):
            start = index + 8
            continue
        found.append(image.convert("RGBA"))
        start = end + 8
    return found


def _title(raw: bytes, fallback: str) -> str:
    text = raw[:800].decode("utf-16le", errors="ignore")
    marker = "clothes file:"
    if marker not in text:
        return fallback
    chars: list[str] = []
    for char in text.split(marker, 1)[1]:
        if char.isalnum() or char in " -().,":
            chars.append(char)
        elif chars:
            break
    title = "".join(chars).strip()
    return title[:80] or fallback


def install_clothes(folder: Path) -> int:
    """Берёт крупные картинки комплекта и кладёт их в одежду программы."""
    if not folder.is_dir():
        return 0
    CLOTHES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for path in sorted(folder.iterdir()):
        images: list[tuple[str, Image.Image]] = []
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            try:
                images.append((path.stem, Image.open(path).convert("RGBA")))
            except OSError:
                continue
        elif path.suffix.lower() == ".cls":
            raw = path.read_bytes()
            title = _title(raw, path.stem)
            for image in _pngs(raw):
                if image.height < 600 or image.width < 600:
                    continue
                images.append((title, image))
        for offset, (name, image) in enumerate(images, start=1):
            safe = "".join(ch if ch not in '\\/:*?"<>|' else " " for ch in name).strip() or "одежда"
            target = CLOTHES_DIR / f"{safe}.png"
            if target.exists():
                target = CLOTHES_DIR / f"{safe}-{offset}.png"
            image.save(target)
            saved += 1
    return saved


def save_user_png(path: Path) -> Path:
    CLOTHES_DIR.mkdir(parents=True, exist_ok=True)
    target = CLOTHES_DIR / path.name
    Image.open(path).convert("RGBA").save(target)
    return target
