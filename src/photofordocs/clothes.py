"""Свои рубашки для кадра. Чужие комплекты формы сюда не входят."""

from __future__ import annotations

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


def save_user_png(path: Path) -> Path:
    CLOTHES_DIR.mkdir(parents=True, exist_ok=True)
    target = CLOTHES_DIR / path.name
    Image.open(path).convert("RGBA").save(target)
    return target
