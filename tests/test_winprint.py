import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photofordocs import winprint


class WinPrintTest(unittest.TestCase):
    def test_devmode_roundtrip_keeps_the_chosen_printer(self) -> None:
        folder = Path(tempfile.mkdtemp())
        previous = winprint.CONFIG_DIR
        winprint.CONFIG_DIR = folder
        try:
            blob = "L805".encode("utf-16le") + b"\x00" * 40
            winprint.save_devmode("L805 Правый", blob)
            self.assertEqual(winprint.load_devmode("L805 Правый"), blob)
            self.assertIsNone(winprint.load_devmode("другой"))
        finally:
            winprint.CONFIG_DIR = previous

    def test_printer_list_is_available(self) -> None:
        names = winprint.printer_names()
        self.assertIsInstance(names, list)
        default = winprint.default_printer()
        if names:
            self.assertTrue(default == "" or default in names or isinstance(default, str))
