"""Раскладка одинаковых кадров на лист с метками реза."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from photofordocs.formats import Format
from photofordocs.session import SheetDoc, SheetItem, paper_mm


@dataclass(frozen=True)
class Paper:
    id: str
    name: str
    width_mm: float
    height_mm: float


PAPERS = (
    Paper("10x15", "10×15 см", 100, 150),
    Paper("15x20", "15×20 см", 150, 200),
    Paper("15x21", "15×21 см", 150, 210),
    Paper("20x30", "20×30 см", 200, 300),
    Paper("a6", "A6", 105, 148),
    Paper("a5", "A5", 148, 210),
    Paper("a4", "A4", 210, 297),
    Paper("a3", "A3", 297, 420),
)


@dataclass(frozen=True)
class Cell:
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class SheetPlan:
    paper: Paper
    columns: int
    rows: int
    cells: tuple[Cell, ...]
    fits: bool

    @property
    def count(self) -> int:
        return len(self.cells)


def plan_sheet(fmt: Format, paper: Paper, margin_mm: float = 4.0, gap_mm: float = 2.0) -> SheetPlan:
    usable_w = paper.width_mm - 2 * margin_mm
    usable_h = paper.height_mm - 2 * margin_mm
    if fmt.width_mm > usable_w or fmt.height_mm > usable_h:
        return SheetPlan(paper, 0, 0, (), False)
    columns = max(1, int((usable_w + gap_mm) // (fmt.width_mm + gap_mm)))
    rows = max(1, int((usable_h + gap_mm) // (fmt.height_mm + gap_mm)))
    grid_w = columns * fmt.width_mm + (columns - 1) * gap_mm
    grid_h = rows * fmt.height_mm + (rows - 1) * gap_mm
    origin_x = (paper.width_mm - grid_w) / 2
    origin_y = (paper.height_mm - grid_h) / 2
    cells = []
    for row in range(rows):
        for column in range(columns):
            cells.append(
                Cell(
                    origin_x + column * (fmt.width_mm + gap_mm),
                    origin_y + row * (fmt.height_mm + gap_mm),
                )
            )
    return SheetPlan(paper, columns, rows, tuple(cells), True)


def render_sheet(photo: Image.Image, fmt: Format, plan: SheetPlan, dpi: int = 300) -> Image.Image:
    def px(mm: float) -> int:
        return round(mm / 25.4 * dpi)

    canvas = Image.new("RGB", (px(plan.paper.width_mm), px(plan.paper.height_mm)), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    photo_w = px(fmt.width_mm)
    photo_h = px(fmt.height_mm)
    fitted = photo.resize((photo_w, photo_h), Image.Resampling.LANCZOS)
    mark = max(6, px(2.2))
    gap = max(2, px(0.7))
    for cell in plan.cells:
        x = px(cell.x_mm)
        y = px(cell.y_mm)
        canvas.paste(fitted, (x, y))
        _cut_marks(draw, x, y, photo_w, photo_h, mark, gap)
    return canvas


def render_document(
    doc: SheetDoc,
    photos: dict[str, Image.Image],
    dpi: int = 300,
    footer: str = "",
    logo: Image.Image | None = None,
) -> Image.Image:
    width_mm, height_mm = paper_mm(doc)

    def px(mm: float) -> int:
        return round(mm / 25.4 * dpi)

    canvas = Image.new("RGB", (max(1, px(width_mm)), max(1, px(height_mm))), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    mark = max(4, px(2.2))
    gap = max(2, px(0.7))
    for item in doc.items:
        _paint_item(canvas, draw, item, photos, footer, logo, px, mark, gap, doc.cut_marks)
    return canvas


def _paint_item(canvas, draw, item: SheetItem, photos, footer, logo, px, mark, gap, cut_marks: bool) -> None:
    x, y = px(item.x_mm), px(item.y_mm)
    w, h = max(1, px(item.w_mm)), max(1, px(item.h_mm))
    if item.kind == "photo" and item.ready_id in photos:
        image = photos[item.ready_id]
        turn = item.rotation % 360
        if turn:
            image = image.rotate(-turn, expand=True)
        image = image.resize((w, h), Image.Resampling.LANCZOS)
        canvas.paste(image, (x, y))
        if cut_marks:
            _cut_marks(draw, x, y, w, h, mark, gap)
    elif item.kind == "footer" and (footer or item.text):
        draw.text((x + 4, y + 4), footer or item.text, fill=(20, 20, 20))
    elif item.kind == "logo" and logo is not None:
        fitted = logo.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
        canvas.paste(fitted, (x, y), fitted)


def _cut_marks(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, mark: int, gap: int) -> None:
    corners = (
        (x, y, -1, -1),
        (x + w, y, 1, -1),
        (x, y + h, -1, 1),
        (x + w, y + h, 1, 1),
    )
    for cx, cy, sx, sy in corners:
        ox = cx + sx * gap
        oy = cy + sy * gap
        draw.line((ox, oy, ox + sx * mark, oy), fill=(0, 0, 0), width=1)
        draw.line((ox, oy, ox, oy + sy * mark), fill=(0, 0, 0), width=1)
