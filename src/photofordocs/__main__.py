import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photofordocs.window import MainWindow, install_indicator_style

APP_ID = "photofordocs"
APP_TITLE = "Фото на документы"


def _desktop_entry() -> None:
    """Док Linux берёт имя из ярлыка. Русские буквы в служебном поле окна он читает криво."""
    folder = Path.home() / ".local" / "share" / "applications"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{APP_ID}.desktop"
    exe = Path(sys.argv[0]).resolve()
    text = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_TITLE}\n"
        f"Exec={exe}\n"
        f"StartupWMClass={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Graphics;\n"
    )
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def main() -> None:
    if sys.platform.startswith("linux"):
        _desktop_entry()
    app = QApplication(sys.argv)
    install_indicator_style(app)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_TITLE)
    app.setDesktopFileName(APP_ID)
    icon = Path(__file__).resolve().parents[2] / "assets" / "photofordocs.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.resize(1240, 780)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
