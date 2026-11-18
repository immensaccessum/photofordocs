import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from PySide6.QtWidgets import QApplication

from photofordocs.session import ReadyItem, SheetItem
from photofordocs.window import MainWindow, Shot


class SheetPreviewCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_opening_print_again_does_not_render_photos(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            fmt = window.formats[0]
            image = Image.new("RGB", (180, 220), (160, 90, 70))
            path = Path("person.jpg")
            window.shots = [Shot(path, image, image, None, replace=False)]
            window.ready = [ReadyItem("r1", str(path), fmt.id)]
            window.doc.items = [
                SheetItem("p1", "photo", 4, 4, fmt.width_mm, fmt.height_mm, "r1", 0, "", str(path))
            ]
            calls = {"n": 0}
            original = window._render

            def counting(*args, **kwargs):
                calls["n"] += 1
                return original(*args, **kwargs)

            window._render = counting
            window._refresh_sheet()
            window._refresh_sheet()
            self.assertEqual(calls["n"], 1)
            self.assertIn("r1", window.printer.pixmaps)
        finally:
            window.close()
            self._app.processEvents()

    def test_corner_radius_comes_from_the_panel(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            window.corner_on.setChecked(True)
            window.corner_radius.setValue(7.5)
            fmt = window.current_format()
            self.assertEqual(fmt.corner_rx_mm, 7.5)
            self.assertEqual(fmt.corner_ry_mm, 7.5)
        finally:
            window.close()
            self._app.processEvents()
