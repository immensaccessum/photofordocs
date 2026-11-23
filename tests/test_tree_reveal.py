import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from photofordocs.session import ready_photo_path, write_ready_info
from photofordocs.window import MainWindow, reveal_opened_branch


class TreeRevealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_expanding_the_bottom_row_shows_its_last_child(self) -> None:
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.resize(180, 72)
        for index in range(12):
            tree.addTopLevelItem(QTreeWidgetItem([f"photo {index}"]))
        photo = tree.topLevelItem(11)
        for index in range(3):
            photo.addChild(QTreeWidgetItem([f"layout {index}"]))
        tree.show()
        self._app.processEvents()
        tree.scrollToItem(photo)
        self._app.processEvents()
        photo.setExpanded(True)
        self._app.processEvents()
        reveal_opened_branch(tree, photo)
        self._app.processEvents()
        view = tree.viewport().rect()
        last = tree.visualItemRect(photo.child(2))
        self.assertTrue(last.intersects(view))
        self.assertGreaterEqual(last.bottom(), view.top())
        self.assertLessEqual(last.bottom(), view.bottom() + 1)

    def test_saved_frame_opens_its_branch_in_the_file_tree(self) -> None:
        window = MainWindow()
        window._save_left_sizes = lambda: None
        try:
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "face.jpg"
                Image.new("RGB", (16, 16), (30, 40, 50)).save(source, quality=90)
                saved = ready_photo_path(source, "Паспорт")
                saved.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (8, 8), (255, 255, 255)).save(saved, quality=90)
                write_ready_info(
                    saved,
                    {"source": source.name, "format_id": "passport-rf-2013", "name": "Паспорт", "printed": False},
                )
                window.folder_root = Path(folder)
                window._fill_files()
                node = window.files.topLevelItem(0)
                self.assertIsNotNone(node)
                self.assertEqual(node.childCount(), 1)
                self.assertTrue(node.isExpanded())
                self.assertFalse(window.files.expandsOnDoubleClick())
        finally:
            window.close()
            self._app.processEvents()
