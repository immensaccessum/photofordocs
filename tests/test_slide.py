import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication

from photofordocs.canvas import PrintCanvas
from photofordocs.session import SheetDoc, SheetItem


class SlideIntoGapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_drag_into_a_blocked_neighbor_keeps_the_free_axis(self) -> None:
        canvas = PrintCanvas()
        photos = [
            SheetItem("a", "photo", 4, 4, 30, 40),
            SheetItem("b", "photo", 34, 4, 30, 40),
            SheetItem("c", "photo", 4, 44, 30, 40),
            SheetItem("d", "photo", 64, 44, 30, 40),
        ]
        canvas.doc = SheetDoc(items=list(photos))
        canvas.block_overlap = True
        canvas._drag_ids = {"d"}
        canvas._press_items = {item.id: item for item in photos}
        # Курсор заехал в кадр слева, но по вертикали уже на уровне свободной ячейки.
        dx, dy = canvas._slide(34 - 64, 44 - 44)
        moved = photos[3].moved(dx, dy)
        self.assertAlmostEqual(moved.x_mm, 34, delta=0.2)
        self.assertAlmostEqual(moved.y_mm, 44, delta=0.2)
        self.assertFalse(canvas._hits(dx, dy))

    def test_corner_clip_does_not_jump_back_to_the_start(self) -> None:
        canvas = PrintCanvas()
        photos = [
            SheetItem("b", "photo", 34, 4, 30, 40),
            SheetItem("d", "photo", 64, 50, 30, 40),
        ]
        canvas.doc = SheetDoc(items=list(photos))
        canvas.block_overlap = True
        canvas._drag_ids = {"d"}
        canvas._press_items = {item.id: item for item in photos}
        dx, dy = canvas._slide(40 - 64, 10 - 50)
        self.assertNotEqual((dx, dy), (0.0, 0.0))
        self.assertFalse(canvas._hits(dx, dy))
        self.assertGreater(abs(dx) + abs(dy), 1.0)

    def test_push_past_neighbor_stops_at_the_edge(self) -> None:
        canvas = PrintCanvas()
        photos = [
            SheetItem("a", "photo", 10, 10, 30, 40),
            SheetItem("b", "photo", 45, 10, 30, 40),
        ]
        canvas.doc = SheetDoc(items=list(photos), snap=True)
        canvas.block_overlap = True
        canvas._drag_ids = {"b"}
        canvas._press_items = {item.id: item for item in photos}
        dx, dy = canvas._slide(-20, 0)
        moved = photos[1].moved(dx, dy)
        self.assertGreaterEqual(moved.x_mm, photos[0].x_mm + photos[0].w_mm - 1e-4)
        self.assertFalse(canvas._hits(dx, dy))
