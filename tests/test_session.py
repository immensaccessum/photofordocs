import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photofordocs.session import (
    JournalLine,
    ReadyItem,
    SheetDoc,
    SheetItem,
    add_journal,
    archive_names,
    autoplace,
    day_total,
    month_folder,
    move_selected,
    next_number,
    overflows,
    paper_mm,
    photo_too_big,
    append_photos,
    center_photos,
    sheet_edges,
    split_place,
)


class SheetCanvasTest(unittest.TestCase):
    def test_two_formats_can_be_moved(self) -> None:
        doc = SheetDoc()
        photos = [
            SheetItem("a", "photo", 0, 0, 30, 40, "passport"),
            SheetItem("b", "photo", 0, 0, 30, 40, "visa"),
        ]
        doc.items = autoplace(doc, photos)
        kinds = [item.kind for item in doc.items]
        self.assertEqual(kinds.count("photo"), 2)
        self.assertIn("footer", kinds)
        placed = [item for item in doc.items if item.kind == "photo"]
        self.assertGreater(placed[1].x_mm, placed[0].x_mm)
        selected = {placed[0].id}
        moved = move_selected(doc, selected, 3, 4)
        first = next(item for item in moved.items if item.id == placed[0].id)
        self.assertAlmostEqual(first.x_mm, placed[0].x_mm + 3, delta=1.3)
        self.assertTrue(overflows(SheetItem("z", "photo", 90, 140, 35, 45), doc))

    def test_month_folder_is_archive_root(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "11.2026"
            self.assertEqual(month_folder(root), root)
            self.assertTrue(root.is_dir())

    def test_archive_number_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(next_number(root), 1)
            (root / "001-паспорт.jpg").write_bytes(b"x")
            self.assertEqual(next_number(root), 2)
            photo, sheet = archive_names(2, "passport-rf")
            self.assertEqual(photo, "002-passport-rf.jpg")
            self.assertTrue(sheet.endswith("лист.jpg"))
            lines = add_journal([], "002", "passport-rf, usa-visa", 2, 500, root)
            self.assertEqual(day_total(lines)[1], 500)
            self.assertTrue((root / "журнал.yaml").exists())
            self.assertEqual(paper_mm(SheetDoc(landscape=True)), (150.0, 100.0))
            _ = (JournalLine, ReadyItem)

    def test_overflow_goes_to_the_next_sheet(self) -> None:
        doc = SheetDoc()
        photos = [SheetItem(f"p{index}", "photo", 0, 0, 90, 40) for index in range(6)]
        placed, rest = split_place(doc, photos)
        fitted = [item for item in placed if item.kind == "photo"]
        self.assertGreaterEqual(len(fitted), 1)
        self.assertTrue(rest)
        self.assertLess(fitted[-1].y_mm + fitted[-1].h_mm, paper_mm(doc)[1])
        self.assertTrue(photo_too_big(doc, SheetItem("big", "photo", 0, 0, 200, 40)))

    def test_horizontal_copies_fill_the_row_and_stay_put(self) -> None:
        doc = SheetDoc()
        first = [SheetItem("a", "photo", 0, 0, 45, 35, "same"), SheetItem("b", "photo", 0, 0, 45, 35, "same")]
        items, rest = append_photos(doc, first)
        photos = [item for item in items if item.kind == "photo"]
        self.assertEqual(rest, [])
        self.assertEqual(len(photos), 2)
        self.assertAlmostEqual(photos[1].x_mm, photos[0].x_mm + photos[0].w_mm)
        self.assertAlmostEqual(photos[0].y_mm, photos[1].y_mm)
        doc.items = items
        origin = (photos[0].x_mm, photos[0].y_mm)
        more = [SheetItem("c", "photo", 0, 0, 45, 35, "same"), SheetItem("d", "photo", 0, 0, 45, 35, "same")]
        items, rest = append_photos(doc, more)
        photos = [item for item in items if item.kind == "photo"]
        self.assertEqual(rest, [])
        self.assertEqual(len(photos), 4)
        stayed = next(item for item in photos if item.id == "a")
        self.assertEqual((stayed.x_mm, stayed.y_mm), origin)
        self.assertAlmostEqual(photos[2].y_mm, photos[0].y_mm + photos[0].h_mm)

    def test_centered_block_sits_on_the_top_margin(self) -> None:
        doc = SheetDoc()
        doc.items = [
            SheetItem("a", "photo", 4, 40, 35, 45),
            SheetItem("b", "photo", 41, 40, 35, 45),
        ]
        center_photos(doc)
        photos = [item for item in doc.items if item.kind == "photo"]
        top, right, _bottom, left = sheet_edges(doc)
        width, _height = paper_mm(doc)
        self.assertAlmostEqual(min(item.y_mm for item in photos), top)
        min_x = min(item.x_mm for item in photos)
        max_x = max(item.x_mm + item.w_mm for item in photos)
        self.assertAlmostEqual((min_x + max_x) / 2, left + (width - left - right) / 2)

    def test_workplace_flags_survive_restart(self) -> None:
        import photofordocs.session as session

        folder = tempfile.mkdtemp()
        previous_dir = session.CONFIG_DIR
        previous_path = session.SETTINGS_PATH
        session.CONFIG_DIR = Path(folder)
        session.SETTINGS_PATH = Path(folder) / "settings.yaml"
        try:
            settings = session.Settings()
            settings.show_quick_bar = False
            settings.print_horizontal = True
            settings.snap = False
            settings.show_footer = True
            settings.allow_rotate = True
            settings.logo_place = "top-left"
            settings.default_format = "passport-rf"
            settings.quick_tunes = [{"name": "Тени", "tone": {"brightness": 1, "balance": [[0, 0, 0]]}}]
            session.save_settings(settings)
            loaded = session.load_settings()
            self.assertFalse(loaded.show_quick_bar)
            self.assertTrue(loaded.print_horizontal)
            self.assertFalse(loaded.snap)
            self.assertTrue(loaded.show_footer)
            self.assertTrue(loaded.allow_rotate)
            self.assertEqual(loaded.logo_place, "top-left")
            self.assertEqual(loaded.default_format, "passport-rf")
            self.assertEqual(loaded.quick_tunes[0]["name"], "Тени")
        finally:
            session.CONFIG_DIR = previous_dir
            session.SETTINGS_PATH = previous_path

    def test_tone_defaults_survive_restart(self) -> None:
        import photofordocs.session as session

        folder = tempfile.mkdtemp()
        previous_dir = session.CONFIG_DIR
        previous_path = session.SETTINGS_PATH
        session.CONFIG_DIR = Path(folder)
        session.SETTINGS_PATH = Path(folder) / "settings.yaml"
        try:
            settings = session.Settings()
            settings.tone_brightness = 5
            settings.tone_contrast = 0
            settings.tone_gamma = 0
            settings.tone_saturation = 0
            settings.tone_sharpness = 0
            settings.tone_balance = [0.0] * 9
            settings.ground_threshold = 4.2
            session.save_settings(settings)
            loaded = session.load_settings()
            self.assertEqual(loaded.tone_brightness, 5)
            self.assertEqual(loaded.tone_balance, [0.0] * 9)
            self.assertAlmostEqual(loaded.ground_threshold, 4.2)
        finally:
            session.CONFIG_DIR = previous_dir
            session.SETTINGS_PATH = previous_path
