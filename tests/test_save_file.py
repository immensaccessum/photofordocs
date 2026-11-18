import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from photofordocs.imageops import write_jpeg
from photofordocs.window import SaveFileDialog


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
