import sys

from PySide6.QtWidgets import QApplication

from photofordocs.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Фото для документов")
    window = MainWindow()
    window.resize(1240, 780)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
