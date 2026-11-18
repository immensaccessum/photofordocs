import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photofordocs.window import MainWindow, install_indicator_style


def main() -> None:
    app = QApplication(sys.argv)
    install_indicator_style(app)
    app.setApplicationName("Фото для документов")
    icon = Path(__file__).resolve().parents[2] / "assets" / "photofordocs.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.resize(1240, 780)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
