import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photofordocs.imageops import FaceBox
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
            window.corner.setCurrentIndex(window.corner.findData("round-right"))
            window.corner_radius.setValue(7.5)
            fmt = window.current_format()
            self.assertEqual(fmt.corner_rx_mm, 7.5)
            self.assertEqual(fmt.corner_ry_mm, 7.5)
        finally:
            window.close()
            self._app.processEvents()

    def test_format_switch_keeps_the_paws_until_another_photo(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            face = FaceBox(0.35, 0.22, 0.30, 0.42, eyes_x=0.5, eyes_y=0.45, mouth_y=0.58)
            image = Image.new("RGB", (800, 1000), (200, 200, 210))
            shot = Shot(Path("a.jpg"), image, image, face)
            other = Shot(Path("b.jpg"), image, image, face)
            window.shots = [shot, other]
            window.index = 0
            window.format_box.setCurrentIndex(window.format_box.findData("photo-3x4-color"))
            window._nudge_guide("crown", 0, -40)
            crown, chin = shot.crown, shot.chin
            self.assertIsNotNone(crown)
            window.format_box.setCurrentIndex(window.format_box.findData("passport-rf-2013"))
            self.assertAlmostEqual(shot.crown[0], crown[0], delta=1.5)
            self.assertAlmostEqual(shot.crown[1], crown[1], delta=1.5)
            self.assertAlmostEqual(shot.chin[1], chin[1], delta=1.5)
            window.format_box.setCurrentIndex(window.format_box.findData("photo-3x4-color"))
            self.assertAlmostEqual(shot.crown[1], crown[1], delta=1.5)
            self.assertAlmostEqual(shot.chin[1], chin[1], delta=1.5)
            self.assertTrue(shot.guides_manual)
            window._load_source(other.path)
            window._load_source(shot.path)
            self.assertFalse(shot.guides_manual)
            self.assertGreater(abs(shot.crown[1] - crown[1]), 10)
        finally:
            window.close()
            self._app.processEvents()

    def test_head_share_is_the_gap_between_the_paws(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            face = FaceBox(0.35, 0.22, 0.30, 0.42, eyes_x=0.5, eyes_y=0.45, mouth_y=0.58)
            image = Image.new("RGB", (800, 1000), (200, 200, 210))
            shot = Shot(Path("a.jpg"), image, image, face)
            window.shots = [shot]
            window.index = 0
            window.format_box.setCurrentIndex(window.format_box.findData("photo-3x4-color"))
            window.refresh()
            self.assertIn("% кадра", window.head_share.text())
            before = window.head_share.text()
            window._nudge_guide("eyes", 0, -30)
            self.assertNotEqual(window.head_share.text(), before)
            self.assertIn("% кадра", window.head_share.text())
        finally:
            window.close()
            self._app.processEvents()

    def test_deleting_the_open_photo_clears_every_view(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        window.settings.ask_before_delete = False
        try:
            with tempfile.TemporaryDirectory() as folder:
                first = Path(folder) / "first.jpg"
                second = Path(folder) / "second.jpg"
                Image.new("RGB", (60, 80), (180, 40, 40)).save(first, quality=90)
                Image.new("RGB", (60, 80), (40, 40, 180)).save(second, quality=90)
                window.folder_root = Path(folder)
                window._fill_files()
                window._load_source(first)
                window._load_source(second)
                window._select_row(0)
                fmt = window.current_format()
                window.ready.append(ReadyItem("r1", str(first), fmt.id))
                window.doc.items.append(
                    SheetItem("p1", "photo", 4, 4, fmt.width_mm, fmt.height_mm, "r1", 0, "", str(first))
                )
                window._set_mode("ready")
                window._show_finished()
                for index in range(window.files.topLevelItemCount()):
                    item = window.files.topLevelItem(index)
                    if Path(str(item.data(0, Qt.ItemDataRole.UserRole))).name == "first.jpg":
                        window.files.setCurrentItem(item)
                window._delete_selected_file()
                self.assertEqual([shot.path.name for shot in window.shots], ["second.jpg"])
                self.assertEqual(window.current_shot().path.name, "second.jpg")
                self.assertIsNotNone(window.source.crop)
                self.assertEqual(window.ready, [])
                self.assertEqual(window.doc.items, [])
                self.assertEqual(window.printer.pixmaps, {})
                for index in range(window.files.topLevelItemCount()):
                    item = window.files.topLevelItem(index)
                    if Path(str(item.data(0, Qt.ItemDataRole.UserRole))).name == "second.jpg":
                        window.files.setCurrentItem(item)
                window._delete_selected_file()
                self.assertEqual(window.shots, [])
                self.assertIsNone(window.current_shot())
                self.assertIsNone(window.source.crop)
                self.assertIsNone(window._finished_image)
                self.assertEqual(window.format_preview.text(), "Нет кадра")
        finally:
            window.close()
            self._app.processEvents()
