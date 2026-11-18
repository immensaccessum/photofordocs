import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from photofordocs.window import reveal_opened_branch


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
