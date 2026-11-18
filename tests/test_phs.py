import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photofordocs import clothes, formats, phs, session


SAMPLE = """[Workdata]
LastPath=D:\\archive\\11.2026
Brightness=5
Contrast=0
DrawCuttingLine=1
CuttingLinePatterned=1
CuttingLineOnlyCorners=0
LineCornerLengthMM=3
SortType=2
FilePanelWidth=299
[Paper]
DPI=720
PaperWidth=100
PaperHeight=150
PaperOrientation=0
TopMargin=3
LeftMargin=3
BottomMargin=3
RightMargin=3
[Common]
PrinterName=L805 Правый
JPEGComprPhoto=80
JPEGComprPaper=100
ICCOutputFName=C:\\color\\matte.icc
AdvertiseF=
AdvertiseP=#!@
WhiteFrameWidth=0
[DetectFace]
Enabled=1
[Colors]
Count=1
Name1=Голубой
Value1=16777152
[License]
Key=secret
"""

PASSPORT = """[Формат]
Kind=0
Index=1
Name=Паспорт РФ (2013)
Width=35
Height=45
FaceHeight=12
OverHeadHeight=2
UnderHeadHeight=9
Colored=1
HaveCorner=0
DefaultCount=4
Ext=.jp_41
HotKey=Ctrl + A
isTwoPointsFrame=1
DPI=600
th=229000
PaperKind=0
ShowInMenu=1
"""

ELECTRONIC = """[Формат]
Kind=1
Index=2
Name=Паспорт РФ (электронный для госуслуг)
Width=827
Height=1063
FaceHeight=12
OverHeadHeight=72
UnderHeadHeight=141
Colored=1
Ext=.jp_pasp_gu
isTwoPointsFrame=1
DPI=600
MaxFileSize=300
ShowInMenu=1
"""


class PhsImportTest(unittest.TestCase):
    def _import(self, path: Path):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous = (
                session.CONFIG_DIR,
                session.SETTINGS_PATH,
                phs.CONFIG_DIR,
                formats.USER_DIR,
                formats.ORDER_PATH,
                clothes.CLOTHES_DIR,
            )
            session.CONFIG_DIR = root
            session.SETTINGS_PATH = root / "settings.yaml"
            phs.CONFIG_DIR = root
            formats.USER_DIR = root / "formats"
            formats.ORDER_PATH = root / "format-order.yaml"
            clothes.CLOTHES_DIR = root / "clothes"
            try:
                report = phs.import_phs(path)
                loaded = session.load_settings()
                catalog = formats.load_formats()
                saved = session.SETTINGS_PATH.read_text(encoding="utf-8")
                return report, loaded, catalog, saved
            finally:
                (
                    session.CONFIG_DIR,
                    session.SETTINGS_PATH,
                    phs.CONFIG_DIR,
                    formats.USER_DIR,
                    formats.ORDER_PATH,
                    clothes.CLOTHES_DIR,
                ) = previous

    def test_sample_brings_workplace_formats_and_skips_license(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.phs"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("PhotoLab.ini", SAMPLE)
                archive.writestr("formats/format.jp_41", PASSPORT)
                archive.writestr("formats/format.jp_pasp_gu", ELECTRONIC)
                archive.writestr("license.ini", "[License]\nKey=secret\n")
            report, loaded, catalog, saved = self._import(path)
        self.assertNotIn("secret", saved)
        passport = next(item for item in catalog if item.id == "jp_41")
        electronic = next(item for item in catalog if item.id == "jp_pasp_gu")
        self.assertEqual((passport.width_mm, passport.height_mm), (35, 45))
        self.assertAlmostEqual(electronic.width_mm, 35.01, places=2)
        self.assertAlmostEqual(electronic.height_mm, 45.0, places=2)
        self.assertEqual(electronic.max_file_kb, 300)
        self.assertEqual(loaded.prices["jp_41"], 229)
        self.assertEqual(loaded.archive_root, r"D:\archive\11.2026")
        self.assertEqual(loaded.default_paper, "10x15")
        self.assertEqual(loaded.margin_left_mm, 3)
        self.assertEqual(loaded.printer_name, "L805 Правый")
        self.assertEqual(loaded.tone_brightness, 5)
        self.assertEqual(loaded.cut_style, "dash")
        self.assertEqual(loaded.jpeg_photo, 80)
        self.assertEqual(loaded.advert_text, "")
        self.assertEqual(loaded.colors[0]["name"], "Голубой")
        self.assertEqual(report.formats, 2)

    def test_real_transfer_file(self) -> None:
        path = Path(__file__).resolve().parents[1] / "оригинал" / "18.11.phs"
        if not path.is_file():
            self.skipTest("файл переноса не лежит в папке оригинал")
        _report, loaded, catalog, saved = self._import(path)
        self.assertNotIn("license", saved.lower())
        passport = next(item for item in catalog if item.id == "jp_41")
        self.assertEqual(passport.name, "Паспорт РФ (2013)")
        self.assertEqual((passport.width_mm, passport.height_mm), (35, 45))
        self.assertGreaterEqual(len([item for item in catalog if not item.builtin]), 50)
        self.assertEqual(loaded.archive_root, r"D:\Полный архив на доки 2003-2021\2026\11.2026")
        self.assertEqual(loaded.default_paper, "10x15")
        self.assertEqual(loaded.margin_top_mm, 3)
        self.assertEqual(loaded.printer_name, "L805 Правый")
        self.assertEqual(loaded.icc, r"C:\Windows\system32\SPOOL\DRIVERS\COLOR\EPSON L805 Series Matte.icc")
        self.assertEqual(loaded.prices.get("jp_41"), 229)
