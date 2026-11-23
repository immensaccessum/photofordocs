import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from photofordocs.imageops import write_jpeg, write_pdf
from photofordocs.dialogs import SaveFileDialog, SaveFileSpec
from photofordocs.window import MainWindow


class SaveFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_millimeters_match_the_format_and_pixels_follow_dpi(self) -> None:
        dialog = SaveFileDialog(None, 35, 45, 300, 48, True, False, True, QPixmap())
        self.assertEqual(dialog.units.currentIndex(), 1)
        self.assertEqual(int(dialog.width_box.value()), 35)
        self.assertEqual(int(dialog.height_box.value()), 45)
        self.assertEqual(dialog.dpi_box.value(), 300)
        self.assertFalse(dialog.limit_bytes.isChecked())
        self.assertFalse(dialog.max_kb.isEnabled())
        self.assertTrue(dialog.open_folder.isChecked())
        dialog.units.setCurrentIndex(0)
        self.assertEqual(int(dialog.width_box.value()), round(35 / 25.4 * 300))
        self.assertEqual(int(dialog.height_box.value()), round(45 / 25.4 * 300))
        dialog.limit_size.setChecked(False)
        self.assertFalse(dialog.size_frame.isEnabled())
        spec = dialog.spec()
        self.assertFalse(spec.limit_size)
        self.assertEqual(spec.max_kb, 48)
        self.assertTrue(spec.open_folder)

    def test_jpeg_stays_under_the_kilobyte_cap(self) -> None:
        image = Image.linear_gradient("L").resize((640, 800)).convert("RGB")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.jpg"
            write_jpeg(image, path, 300, 95, 12)
            self.assertLessEqual(path.stat().st_size, 12 * 1024)
            self.assertGreater(path.stat().st_size, 200)

    def test_ready_tree_file_is_copied_without_rebuilding_the_frame(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            with tempfile.TemporaryDirectory() as folder:
                ready = Path(folder) / "person.Паспорт.jpg"
                Image.new("RGB", (40, 50), (10, 20, 30)).save(ready, quality=95)
                window._ask_export = lambda *_args: SaveFileSpec(True, 20, 25, 200, False, 48, False)

                def refuse_render(*_args, **_kwargs):
                    raise AssertionError("кадр собран заново")

                window._render = refuse_render
                destination = Path(folder) / "copy.jpg"

                def save_name(*_args, **_kwargs):
                    return str(destination), ""

                from photofordocs import window as window_mod

                original = window_mod.QFileDialog.getSaveFileName
                window_mod.QFileDialog.getSaveFileName = save_name
                try:
                    window._save_ready_as(ready, "passport-rf-2013")
                finally:
                    window_mod.QFileDialog.getSaveFileName = original
                saved = Image.open(destination)
                self.assertEqual(saved.size, (20, 25))
                self.assertEqual(saved.getpixel((0, 0)), (10, 20, 30))
                saved.close()
        finally:
            window.close()
            self._app.processEvents()

    def test_pdf_page_is_the_paper_size(self) -> None:
        width_mm, height_mm = 100.0, 150.0
        dpi = 300
        image = Image.new(
            "RGB",
            (round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi)),
            (255, 255, 255),
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sheet.pdf"
            write_pdf([image], path, dpi, 90)
            boxes = re.findall(br"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", path.read_bytes())
        self.assertEqual(len(boxes), 1)
        width = float(boxes[0][0]) / 72 * 25.4
        height = float(boxes[0][1]) / 72 * 25.4
        self.assertAlmostEqual(width, width_mm, delta=0.1)
        self.assertAlmostEqual(height, height_mm, delta=0.1)
