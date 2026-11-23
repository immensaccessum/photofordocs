import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photofordocs.formats import import_photolab_file, load_formats, parse_photolab_formats
from photofordocs.imageops import (
    format_eye_t,
    guide_ratios,
    apply_corner,
    resolve_corner,
    FaceBox,
    head_pose_pixels,
    Rect,
    frame_points,
    matte_to_alpha,
    place_crop,
    contain_guides,
    move_middle,
    pose_for_ends,
    pose_from_ends,
    render_photo,
    replace_background,
    zoom_shift_for_box,
)
from photofordocs.session import SheetDoc, SheetItem, read_ready, ready_belongs, ready_copy_path, ready_photo_path, write_ready_info
from photofordocs.sheet import PAPERS, plan_sheet, render_document, render_sheet


class FormatCatalogTest(unittest.TestCase):
    def test_builtin_formats_fit_on_10x15(self) -> None:
        formats = load_formats()
        ids = {fmt.id for fmt in formats}
        core = {
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
        }
        self.assertTrue(core <= ids)
        paper = next(item for item in PAPERS if item.id == "10x15")
        for fmt in formats:
            if not fmt.builtin:
                continue
            self.assertLess(fmt.head_height_mm + fmt.chin_bottom_mm, fmt.height_mm)
            if fmt.width_mm <= 90 and fmt.height_mm <= 140:
                plan = plan_sheet(fmt, paper)
                self.assertTrue(plan.fits, fmt.id)
                self.assertGreaterEqual(plan.count, 1, fmt.id)

    def test_original_catalog_is_imported_into_the_program(self) -> None:
        text = """
[Common]
Count=1

[Формат0]
Name=Паспорт РФ
Width=37
Height=47
FaceHeight=12
OverHeadHeight=5
UnderHeadHeight=30
Colored=0
HaveCorner=0
CornerKind=0
CornerPlacement=0
DefaultCount=4
HorizPlacement=1
Ext=.jp_04
HotKey=Ctrl + A
isTwoPointsFrame=0
OverHeadHeight2=5
ShowInMenu=1
PaperKind=1
CornerRadiusX=18
CornerRadiusY=18
"""
        parsed = parse_photolab_formats(text)
        self.assertEqual(parsed[0].name, "Паспорт РФ")
        self.assertEqual((parsed[0].width_mm, parsed[0].height_mm), (37, 47))
        self.assertEqual(parsed[0].head_height_mm, 12)
        self.assertEqual(parsed[0].chin_bottom_mm, 30)
        self.assertTrue(parsed[0].show_in_menu)
        self.assertFalse(parsed[0].two_points)
        self.assertEqual(parsed[0].hotkey, "Ctrl + A")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ini = root / "formats.ini"
            ini.write_text(text, encoding="cp1251")
            from photofordocs import formats as catalog

            previous_dir, previous_order = catalog.USER_DIR, catalog.ORDER_PATH
            catalog.USER_DIR = root / "user"
            catalog.ORDER_PATH = root / "format-order.yaml"
            try:
                loaded = import_photolab_file(ini)
                self.assertEqual(len(loaded), 1)
                saved = catalog.USER_DIR / f"{loaded[0].id}.yaml"
                self.assertTrue(saved.is_file())
                self.assertNotEqual(saved.parent, ini.parent)
            finally:
                catalog.USER_DIR = previous_dir
                catalog.ORDER_PATH = previous_order

    def test_electronic_format_converts_short_pixel_margins(self) -> None:
        text = """
[Формат]
Kind=1
Name=Вод.удост.(национальные, электр)
Width=496
Height=709
FaceHeight=142
OverHeadHeight=40
UnderHeadHeight=126
isTwoPointsFrame=1
OverHeadHeight2=47
DPI=600
Ext=.jp_50
"""
        fmt = parse_photolab_formats(text)[0]
        self.assertAlmostEqual(fmt.width_mm, 21.0, places=2)
        self.assertAlmostEqual(fmt.height_mm, 30.01, places=2)
        self.assertAlmostEqual(fmt.chin_bottom_mm, 5.33, places=2)
        self.assertAlmostEqual(fmt.head_height_mm, 22.99, places=2)
        self.assertAlmostEqual(fmt.face_height_mm, 6.01, places=2)
        crown, eyes, chin = guide_ratios(fmt, format_eye_t(fmt))
        self.assertAlmostEqual(crown, fmt.crown_ratio, places=2)
        self.assertAlmostEqual(chin, 1.0 - fmt.chin_ratio, places=2)
        self.assertAlmostEqual(chin - eyes, fmt.face_height_mm / fmt.height_mm, places=2)
        self.assertGreater(fmt.head_ratio, 0.7)


class CropTest(unittest.TestCase):
    def test_three_point_middle_resizes_frame(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-color")
        crown = (500.0, 200.0)
        chin = (500.0, 360.0)
        low = pose_from_ends(fmt, crown, chin, False, 0.3)[2]
        high = pose_from_ends(fmt, crown, chin, False, 0.75)[2]
        self.assertGreater(low, high)

    def test_three_point_frame_matches_photolab_3x4(self) -> None:
        """Скриншот PhotoLab, преднастройка 3×4: те же точки дают ту же рамку.

        Масштаб — глаза–подбородок / 12 мм. Высота рамки — 40 мм этого масштаба,
        верх — 4 мм над макушкой. Нижнее поле 4 мм не подтягивает подбородок.
        """
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-color")
        crown = (298.0, 272.0)
        chin = (298.0, 565.0)
        eye_t = (416.0 - 272.0) / (565.0 - 272.0)
        left, top, height, angle = pose_from_ends(fmt, crown, chin, False, eye_t)
        scale = (565.0 - 416.0) / fmt.face_height_mm
        self.assertEqual(angle, 0.0)
        self.assertAlmostEqual(height, fmt.height_mm * scale, places=2)
        self.assertAlmostEqual(top, 272.0 - fmt.overhead2_mm * scale, places=2)
        self.assertAlmostEqual(height, 496.67, delta=0.1)
        self.assertAlmostEqual(top, 222.33, delta=0.1)
        self.assertAlmostEqual((left + height * fmt.aspect / 2), 298.0, places=2)
        box = Rect(left, top, height * fmt.aspect, height)
        points = frame_points(box, fmt, 0.0, eye_t)
        self.assertAlmostEqual(points["crown"][1], 272.0, places=2)
        self.assertAlmostEqual(points["eyes"][1], 416.0, places=2)
        self.assertAlmostEqual(points["chin"][1], 565.0, places=2)

    def test_corner_right_crop_matches_head_field(self) -> None:
        color = next(item for item in load_formats() if item.id == "photo-3x4-color")
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-corner-right")
        face = FaceBox(
            0.35,
            0.22,
            0.30,
            0.42,
            eyes_x=0.50,
            eyes_y=0.47,
            mouth_y=0.58,
        )
        size = (1000, 1200)
        crop = place_crop(size, face, fmt)
        color_crop = place_crop(size, face, color)
        crown_y, _eye_y, chin_y, _ = head_pose_pixels(face, *size, fmt)
        self.assertAlmostEqual(crop.height, color_crop.height, delta=2.0)
        self.assertAlmostEqual(fmt.frame_head_height_mm, color.frame_head_height_mm)
        self.assertAlmostEqual(fmt.chin_bottom_mm, 21.0, places=1)
        self.assertAlmostEqual(fmt.crop_under_mm, 4.0, places=1)

    def test_three_point_crop_matches_head_and_chin_field(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-color")
        face = FaceBox(
            0.35,
            0.22,
            0.30,
            0.42,
            eyes_x=0.50,
            eyes_y=0.47,
            mouth_y=0.58,
        )
        size = (1000, 1200)
        eye_t = format_eye_t(fmt)
        crop = place_crop(size, face, fmt, eye_t=eye_t)
        crown_y, _eye_y, chin_y, _ = head_pose_pixels(face, *size, fmt)
        self.assertAlmostEqual((crop.top + crop.height - chin_y) / crop.height, fmt.crop_chin_ratio, places=2)
        self.assertAlmostEqual((chin_y - crown_y) / crop.height, fmt.frame_head_ratio, places=2)

    def test_head_lands_on_guides(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        face = FaceBox(0.35, 0.30, 0.30, 0.34)
        crop = place_crop((1000, 1200), face, fmt)
        self.assertAlmostEqual(crop.width / crop.height, fmt.aspect, places=3)
        height = 1200
        crown_y, _eye_y, chin_y, _ = head_pose_pixels(face, 1000, height, fmt)
        head_frac = (chin_y - crown_y) / crop.height
        chin_gap = (crop.top + crop.height - chin_y) / crop.height
        self.assertAlmostEqual(head_frac, fmt.head_ratio, places=3)
        self.assertAlmostEqual(chin_gap, fmt.chin_ratio, places=3)

    def test_landmark_head_lands_on_guides(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        face = FaceBox(
            0.35,
            0.22,
            0.30,
            0.42,
            eyes_x=0.50,
            eyes_y=0.47,
            mouth_y=0.58,
        )
        size = (1000, 1200)
        crop = place_crop(size, face, fmt)
        crown_y, eye_y, chin_y, _ = head_pose_pixels(face, *size, fmt)
        head_frac = (chin_y - crown_y) / crop.height
        chin_gap = (crop.top + crop.height - chin_y) / crop.height
        self.assertAlmostEqual(head_frac, fmt.head_ratio, places=2)
        self.assertAlmostEqual(chin_gap, fmt.chin_ratio, places=2)
        eye_line = (eye_y - crop.top) / crop.height
        self.assertGreater(eye_line, 0.35)
        self.assertLess(eye_line, 0.75)

    def test_handles_resize_shift_and_tilt(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        face = FaceBox(0.35, 0.30, 0.30, 0.34)
        size = (1000, 1200)
        crop = place_crop(size, face, fmt)
        points = frame_points(crop, fmt, 0)
        left, top, height, angle = pose_from_ends(fmt, points["crown"], (points["chin"][0], points["chin"][1] + 80), False)
        self.assertEqual(angle, 0.0)
        self.assertGreater(height, crop.height)
        moved = frame_points(type(crop)(left, top, height * fmt.aspect, height), fmt, 0)
        self.assertAlmostEqual(moved["crown"][1], points["crown"][1], places=2)
        self.assertAlmostEqual(moved["chin"][1], points["chin"][1] + 80, places=2)
        shifted = pose_from_ends(
            fmt,
            (points["crown"][0] + 40, points["crown"][1]),
            (points["chin"][0] + 40, points["chin"][1]),
            False,
        )
        again = frame_points(type(crop)(shifted[0], shifted[1], shifted[2] * fmt.aspect, shifted[2]), fmt, 0)
        self.assertAlmostEqual(again["crown"][0], points["crown"][0] + 40, places=2)
        self.assertAlmostEqual(again["chin"][0], points["chin"][0] + 40, places=2)
        tilted = pose_from_ends(fmt, points["crown"], (points["chin"][0] + 50, points["chin"][1]), True)
        self.assertNotEqual(tilted[3], 0.0)
        ends = frame_points(type(crop)(tilted[0], tilted[1], tilted[2] * fmt.aspect, tilted[2]), fmt, tilted[3])
        self.assertAlmostEqual(ends["crown"][0], points["crown"][0], places=1)
        self.assertAlmostEqual(ends["chin"][0], points["chin"][0] + 50, places=1)
        self.assertAlmostEqual(ends["eyes"][0], ends["crown"][0] + 0.55 * (ends["chin"][0] - ends["crown"][0]), places=2)
        zoom, shift_x, shift_y = zoom_shift_for_box(size, face, fmt, crop.left + 12, crop.top - 8, crop.height)
        back = place_crop(size, face, fmt, zoom, shift_x, shift_y)
        self.assertAlmostEqual(back.left, crop.left + 12, places=2)
        self.assertAlmostEqual(back.top, crop.top - 8, places=2)

    def test_shrinking_past_the_old_limit_keeps_the_frame(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-corner-left")
        face = FaceBox(0.4, 0.2, 0.25, 0.3)
        size = (800, 1000)
        base = place_crop(size, face, fmt)
        target_h = base.height / 6
        zoom, shift_x, shift_y = zoom_shift_for_box(size, face, fmt, base.left, base.top, target_h)
        back = place_crop(size, face, fmt, zoom, shift_x, shift_y)
        self.assertGreater(zoom, 3)
        self.assertAlmostEqual(back.height, target_h, places=1)
        self.assertAlmostEqual(back.top, base.top, places=1)
        self.assertAlmostEqual(back.left, base.left, places=1)

    def test_middle_handle_slides_along_the_segment(self) -> None:
        crown = (100.0, 80.0)
        chin = (100.0, 240.0)
        moved_crown, moved_chin, fraction = move_middle(crown, chin, (100.0, 186.0), False)
        self.assertEqual(moved_crown[1], 80.0)
        self.assertEqual(moved_chin[1], 240.0)
        self.assertAlmostEqual(fraction, (186.0 - 80.0) / 160.0, places=3)
        self.assertAlmostEqual(move_middle(crown, chin, (100.0, 70.0), False)[2], 0.1)
        self.assertAlmostEqual(move_middle(crown, chin, (100.0, 400.0), False)[2], 0.9)
        shifted_crown, shifted_chin, _fraction = move_middle(crown, chin, (140.0, 168.0), False)
        self.assertEqual((shifted_crown[0], shifted_chin[0]), (140.0, 140.0))
        self.assertEqual((shifted_crown[1], shifted_chin[1]), (80.0, 240.0))
        tilted_crown = (100.0, 80.0)
        tilted_chin = (150.0, 240.0)
        stayed_crown, stayed_chin, fraction = move_middle(tilted_crown, tilted_chin, (200.0, 160.0), True)
        self.assertEqual(stayed_crown, tilted_crown)
        self.assertEqual(stayed_chin, tilted_chin)
        self.assertGreaterEqual(fraction, 0.1)
        self.assertLessEqual(fraction, 0.9)
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        _left, _top, near, _angle = pose_from_ends(fmt, crown, chin, False, 0.2)
        left, top, far, _angle = pose_from_ends(fmt, crown, chin, False, 0.8)
        self.assertGreater(near, far)
        head = abs(chin[1] - crown[1])
        _left, _top, nominal, _angle = pose_from_ends(fmt, crown, chin, False, 0.55)
        scale = head * 0.45 / fmt.face_height_mm
        expected = head + (fmt.height_mm * fmt.crown_ratio + fmt.crop_under_mm) * scale
        self.assertAlmostEqual(nominal, expected, places=2)
        crop = Rect(left, top, far * fmt.aspect, far)
        points = frame_points(crop, fmt, 0.0, 0.8)
        self.assertAlmostEqual(points["crown"][1], crown[1], places=2)
        self.assertAlmostEqual(points["chin"][1], chin[1], places=2)

    def test_guides_stay_on_the_photo(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        face = FaceBox(0.35, 0.30, 0.30, 0.34)
        size = (1000, 1200)
        shift_x, shift_y = contain_guides(size, face, fmt, 1.0, -5.0, 0.0)
        crop = place_crop(size, face, fmt, 1.0, shift_x, shift_y)
        xs = [point[0] for point in frame_points(crop, fmt).values()]
        self.assertGreaterEqual(min(xs), -0.5)
        self.assertLessEqual(max(xs), size[0] - 0.5)


class BackgroundTest(unittest.TestCase):
    def test_border_color_is_replaced_and_center_stays(self) -> None:
        image = Image.new("RGB", (200, 240), (180, 190, 200))
        draw = ImageDraw.Draw(image)
        draw.ellipse((70, 50, 130, 130), fill=(210, 170, 140))
        draw.rectangle((60, 120, 140, 220), fill=(20, 20, 20))
        result = replace_background(image, (255, 255, 255), tolerance=28)
        pixels = result.load()
        self.assertGreater(pixels[2, 2][0], 245)
        self.assertLess(pixels[100, 180][0], 40)

    def test_gradient_backdrop_fills_and_head_stays(self) -> None:
        image = Image.new("RGB", (240, 300), (210, 214, 218))
        draw = ImageDraw.Draw(image)
        for y in range(300):
            shade = 250 - int(70 * y / 300)
            draw.line((0, y, 239, y), fill=(shade, shade, shade + 4))
        draw.ellipse((80, 40, 160, 140), fill=(214, 164, 132))
        draw.rectangle((70, 130, 170, 280), fill=(30, 30, 34))
        face = FaceBox(80 / 240, 40 / 300, 80 / 240, 100 / 300, eyes_x=0.5, eyes_y=0.28, mouth_y=0.38)
        result = replace_background(image, (0, 0, 255), tolerance=42, face=face)
        pixels = result.load()
        self.assertEqual(pixels[4, 4], (0, 0, 255))
        self.assertEqual(pixels[230, 8], (0, 0, 255))
        self.assertEqual(pixels[16, 90], (0, 0, 255))
        cheek = pixels[120, 90]
        self.assertGreater(cheek[0], 150)
        self.assertLess(cheek[2], 160)
        self.assertLess(pixels[120, 200][0], 50)

    def test_filled_background_ignores_tone(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-color")
        image = Image.new("RGB", (120, 160), (180, 190, 200))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 50, 80, 130), fill=(20, 20, 30))
        crop = place_crop(image.size, None, fmt)
        plain = render_photo(
            image,
            crop,
            fmt,
            72,
            replace=True,
            tolerance=40,
            brightness=0,
            contrast=0,
            grayscale=False,
            background="#0000FF",
        )
        bright = render_photo(
            image,
            crop,
            fmt,
            72,
            replace=True,
            tolerance=40,
            brightness=80,
            contrast=40,
            grayscale=False,
            background="#0000FF",
            gamma=30,
        )
        # Без замены цвета фон остаётся как на снимке и не светлеет вместе с лицом.
        self.assertEqual(plain.getpixel((1, 1)), (180, 190, 200))
        self.assertEqual(bright.getpixel((1, 1)), (180, 190, 200))
        self.assertNotEqual(plain.getpixel((plain.width // 2, plain.height // 2)), bright.getpixel((bright.width // 2, bright.height // 2)))
        painted = render_photo(
            image,
            crop,
            fmt,
            72,
            replace=True,
            tolerance=40,
            brightness=80,
            contrast=40,
            grayscale=False,
            background="#0000FF",
            gamma=30,
            paint=True,
        )
        self.assertEqual(painted.getpixel((1, 1)), (0, 0, 255))

    def test_matte_threshold_grows_with_tolerance(self) -> None:
        matte = np.linspace(0.0, 1.0, 400, dtype=np.float32).reshape(20, 20)
        low = matte_to_alpha(matte, 10)
        high = matte_to_alpha(matte, 90)
        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertGreater(float(high.mean()), float(low.mean()))

    def test_given_mask_is_painted_without_searching(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-color")
        image = Image.new("RGB", (80, 100), (10, 20, 30))
        alpha = np.zeros((100, 80), np.float32)
        alpha[:, :40] = 1.0
        with patch("photofordocs.imageops.background_alpha", side_effect=AssertionError("поиск фона")):
            painted = render_photo(
                image,
                Rect(0, 0, 80, 100),
                fmt,
                72,
                replace=True,
                tolerance=40,
                brightness=0,
                contrast=0,
                grayscale=False,
                background="#00FF00",
                paint=True,
                source_alpha=alpha,
                corner="none",
            )
        self.assertEqual(painted.getpixel((2, 2)), (0, 255, 0))
        self.assertEqual(painted.getpixel((painted.width - 3, 2)), (10, 20, 30))

    def test_two_formats_keep_the_same_paws(self) -> None:
        formats = load_formats()
        small = next(item for item in formats if item.id == "photo-3x4-color")
        passport = next(item for item in formats if item.id == "passport-rf-2013")
        size = (800, 1000)
        face = FaceBox(0.35, 0.2, 0.3, 0.4, eyes_x=0.5, eyes_y=0.42, mouth_y=0.55)
        crown = (400.0, 280.0)
        chin = (400.0, 640.0)
        first = pose_for_ends(size, face, small, crown, chin, False, 0.55)
        second = pose_for_ends(size, face, passport, crown, chin, False, 0.55)
        self.assertNotEqual(tuple(round(value, 3) for value in first), tuple(round(value, 3) for value in second))

        def paws(fmt, pose):
            zoom, shift_x, shift_y, angle, eye_t = pose
            crop = place_crop(size, face, fmt, zoom, shift_x, shift_y, eye_t=eye_t)
            points = frame_points(crop, fmt, angle, eye_t)
            return points["crown"], points["chin"]

        left_crown, left_chin = paws(small, first)
        right_crown, right_chin = paws(passport, second)
        for actual, expected in (
            (left_crown, right_crown),
            (left_chin, right_chin),
            (left_crown, crown),
            (left_chin, chin),
        ):
            self.assertAlmostEqual(actual[0], expected[0], delta=1.5)
            self.assertAlmostEqual(actual[1], expected[1], delta=1.5)


class SheetTest(unittest.TestCase):
    def test_passport_sheet_has_several_copies(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "passport-rf-2013")
        paper = next(item for item in PAPERS if item.id == "10x15")
        plan = plan_sheet(fmt, paper)
        self.assertGreaterEqual(plan.count, 4)
        image = Image.new("RGB", (80, 100), (240, 230, 220))
        photo = render_photo(
            image,
            place_crop(image.size, None, fmt),
            fmt,
            72,
            replace=False,
            tolerance=0,
            brightness=0,
            contrast=0,
            grayscale=False,
        )
        sheet = render_sheet(photo, fmt, plan, dpi=72)
        self.assertEqual(sheet.size[0], round(paper.width_mm / 25.4 * 72))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sheet.jpg"
            sheet.save(path, quality=90)
            self.assertGreater(path.stat().st_size, 1000)

    def test_horizontal_photo_keeps_a_square_square(self) -> None:
        image = Image.new("RGB", (40, 80), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 30, 29, 49), fill=(255, 0, 0))
        doc = SheetDoc(cut_marks=False)
        doc.items = [SheetItem("p", "photo", 0, 0, 8, 4, "r", 90)]
        sheet = render_document(doc, {"r": image}, dpi=254)
        xs = []
        ys = []
        for y in range(sheet.height):
            for x in range(sheet.width):
                pixel = sheet.getpixel((x, y))
                if pixel[0] > 200 and pixel[1] < 40:
                    xs.append(x)
                    ys.append(y)
        self.assertTrue(xs)
        self.assertAlmostEqual(max(xs) - min(xs), max(ys) - min(ys), delta=2)


class ReadyFolderTest(unittest.TestCase):
    def test_saved_format_lands_beside_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "клиент.jpg"
            neighbor = root / "клиент.копия.jpg"
            source.write_bytes(b"")
            neighbor.write_bytes(b"")
            photo = ready_photo_path(source, "Паспорт РФ")
            self.assertEqual(photo, root / "Ready" / "клиент.Паспорт РФ.jpg")
            other = ready_photo_path(neighbor, "Виза")
            photo.parent.mkdir()
            photo.write_bytes(b"jpeg")
            other.write_bytes(b"jpeg")
            write_ready_info(photo, {"format_id": "passport-rf", "name": "Паспорт РФ", "printed": False, "source": source.name})
            stems = {source.stem, neighbor.stem}
            self.assertTrue(ready_belongs(source, photo.name, stems))
            self.assertFalse(ready_belongs(source, other.name, stems))
            records = read_ready(source, stems, names={})
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["format_id"], "passport-rf")
            self.assertEqual(records[0]["name"], "Паспорт РФ")
            copy, number = ready_copy_path(photo)
            self.assertEqual(number, 2)
            self.assertEqual(copy.name, "клиент.Паспорт РФ 2.jpg")
            copy.write_bytes(b"jpeg")
            write_ready_info(copy, {"format_id": "passport-rf", "name": "Паспорт РФ 2", "printed": False, "source": source.name})
            self.assertTrue(ready_belongs(source, copy.name, stems))
            self.assertFalse(ready_belongs(neighbor, copy.name, stems))
            again, again_number = ready_copy_path(photo)
            self.assertEqual(again_number, 3)
            rows = read_ready(source, stems, names={})
            self.assertEqual({row["name"] for row in rows}, {"Паспорт РФ", "Паспорт РФ 2"})

    def test_original_ready_file_is_attached_to_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            month = Path(folder) / "11.2026"
            inner = month / "11.2026"
            inner.mkdir(parents=True)
            source = inner / "18.11 (1).png"
            neighbor = inner / "18.11 (1) 1.jpg"
            source.write_bytes(b"")
            neighbor.write_bytes(b"")
            ready = month / "Ready"
            ready.mkdir()
            (ready / "18.11 (1).jp_41~p").write_bytes(b"jpeg")
            (ready / "18.11 (1) 1.jp_201").write_bytes(b"jpeg")
            (ready / "чужой.jp_41~p").write_bytes(b"jpeg")
            stems = {source.stem, neighbor.stem}
            names = {"jp_41": "Паспорт РФ (2013)", "jp_201": "3 х 4 цветная"}
            self.assertTrue(ready_belongs(source, "18.11 (1).jp_41~p", stems))
            self.assertFalse(ready_belongs(source, "18.11 (1) 1.jp_201", stems))
            records = read_ready(source, stems, names)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["name"], "Паспорт РФ (2013)")
            self.assertTrue(records[0]["printed"])
            self.assertEqual(records[0]["format_id"], "passport-rf-2013")
            other = read_ready(neighbor, stems, names)
            self.assertEqual(other[0]["name"], "3 х 4 цветная")
            self.assertFalse(other[0]["printed"])

    def test_format_corner_is_a_real_cut_not_a_speck(self) -> None:
        fmt = next(item for item in load_formats() if item.id == "photo-3x4-corner-left")
        placement, kind, rx_mm, ry_mm = resolve_corner(fmt, "format")
        self.assertEqual((placement, kind, rx_mm, ry_mm), (2, 2, 13.0, 13.0))
        image = Image.new("RGB", (270, 340), (20, 20, 20))
        rx, ry = round(13 / 27 * 270), round(13 / 34 * 340)
        cut = apply_corner(image, placement, kind, rx, ry, (255, 255, 255))
        self.assertLess(cut.getpixel((0, 0))[0], 40)
        self.assertGreater(cut.getpixel((0, 339))[0], 240)
        self.assertGreater(cut.getpixel((40, 330))[0], 240)
        self.assertLess(cut.getpixel((rx - 4, 340 - ry + 4))[0], 40)
        inward = apply_corner(image, 2, 1, rx, ry, (255, 255, 255))
        self.assertGreater(inward.getpixel((0, 339))[0], 240)
        self.assertLess(inward.getpixel((rx // 2, 340 - ry // 2))[0], 40)
        self.assertGreater(cut.getpixel((rx // 2, 340 - ry // 2))[0], 240)
        straight = apply_corner(image, 2, 0, rx, ry, (255, 255, 255))
        self.assertGreater(straight.getpixel((0, 339))[0], 240)
        self.assertGreater(straight.getpixel((8, 332))[0], 240)
        self.assertLess(straight.getpixel((rx - 2, 340 - ry + 2))[0], 40)
        plain = next(item for item in load_formats() if item.id == "passport-rf-2013")
        self.assertEqual(resolve_corner(plain, "format")[0], 0)
        self.assertEqual(resolve_corner(plain, "straight-right")[:2], (8, 0))


if __name__ == "__main__":
    unittest.main()
