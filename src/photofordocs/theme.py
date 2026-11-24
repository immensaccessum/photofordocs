"""Светлая и тёмная тема. «Авто» берёт схему системы, выбор в настройках её перекрывает.

Новая схема — ещё один полный словарь в `_THEMES` и его имя в `theme_name`.
Ключи красят окно: фон, кнопки, меню, стекло поверх снимка, нейтральный ползунок.
Бумага листа, метки реза и цветные дорожки ползунков сюда не входят: это рисунок
документа и подпись инструмента, их тема окна не меняет.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPolygon
from PySide6.QtWidgets import QApplication

_bound = False
_choice = "auto"


def set_theme_choice(name: str) -> None:
    """auto — схема системы, light и dark — выбранная тема."""
    global _choice
    _choice = name if name in ("auto", "light", "dark") else "auto"


def _system_dark(app: QApplication | None = None) -> bool:
    app = app or QApplication.instance()
    if app is None:
        return False
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def is_dark(app: QApplication | None = None) -> bool:
    return theme_name(app) == "dark"


def theme_name(app: QApplication | None = None) -> str:
    if _choice in ("light", "dark"):
        return _choice
    return "dark" if _system_dark(app) else "light"


def tokens(app: QApplication | None = None) -> dict[str, str]:
    """Цвета текущей схемы. Палитра, таблица стилей и ползунки читают только её."""
    return _THEMES[theme_name(app)]


def slider_chrome(app: QApplication | None = None) -> tuple[str, str, str]:
    """Ручка ползунка: заливка, обводка ручки, обводка дорожки."""
    color = tokens(app)
    return color["slider_handle"], color["slider_edge"], color["slider_border"]


# Стекло лежит поверх снимка, поэтому в обеих схемах пока одно и то же.
_GLASS = {
    "accent": "#2a62c9",
    "glass": "rgba(255, 255, 255, 140)",
    "glass_border": "rgba(90, 90, 90, 80)",
    "glass_text": "#202020",
    "glass_hover": "rgba(255, 255, 255, 210)",
    "glass_disabled": "rgba(243, 243, 243, 100)",
    "preview": "#ffffff",
    "preview_text": "#202020",
}

_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "text": "#f0f0f0",
        "window": "#2c2c2c",
        "button": "#3a3a3a",
        "button_border": "#6a6a6a",
        "field": "#1e1e1e",
        "pane": "#333333",
        "group": "#262626",
        "group_border": "#5d6e82",
        "title_bg": "#1e3348",
        "title": "#d6e6f8",
        "split": "#4a4a4a",
        "hover": "#314862",
        "track": "#2a2a2a",
        "handle": "#6e6e6e",
        "handle_border": "#555555",
        "handle_hover": "#8a8a8a",
        "menu": "#2a2a2a",
        "menu_border": "#555555",
        "selected": "#3c5f86",
        "selected_text": "#f4f4f4",
        "line": "#555555",
        "checked": "#454545",
        "muted": "#9a9a9a",
        "mid": "#8d8d8d",
        "link": "#8eb6e8",
        "slider": "#5a5a5a",
        "slider_handle": "#e6e6e6",
        "slider_edge": "#111111",
        "slider_border": "#6a6a6a",
        "mat": "#4a4e55",
        "mat_border": "#6a6e76",
        **_GLASS,
    },
    "light": {
        "text": "#202020",
        "window": "#ececec",
        "button": "#f7f7f7",
        "button_border": "#b5b5b5",
        "field": "#ffffff",
        "pane": "#f3f3f3",
        "group": "#fbfbfb",
        "group_border": "#8ea0b5",
        "title_bg": "#d9e4f2",
        "title": "#16375f",
        "split": "#c4c4c4",
        "hover": "#e7eef8",
        "track": "#dedede",
        "handle": "#b4b4b4",
        "handle_border": "#9a9a9a",
        "handle_hover": "#9e9e9e",
        "menu": "#f4f4f4",
        "menu_border": "#b0b0b0",
        "selected": "#c5ceda",
        "selected_text": "#101010",
        "line": "#d0d0d0",
        "checked": "#ffffff",
        "muted": "#9a9a9a",
        "mid": "#8a8a8a",
        "link": "#1d4f89",
        "slider": "#d5d5d5",
        "slider_handle": "#ffffff",
        "slider_edge": "#4a4a4a",
        "slider_border": "#9a9a9a",
        "mat": "#c5c8ce",
        "mat_border": "#9aa0a8",
        **_GLASS,
    },
}


def _colors(dark: bool) -> dict[str, str]:
    return _THEMES["dark" if dark else "light"]


def palette_for(dark: bool) -> QPalette:
    color = _colors(dark)
    palette = QPalette()
    window = QColor(color["window"])
    field = QColor(color["field"])
    text = QColor(color["text"])
    button = QColor(color["button"])
    highlight = QColor(color["selected"])
    highlighted = QColor(color["selected_text"])
    mid = QColor(color["mid"])
    muted = QColor(color["muted"])
    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: field,
        QPalette.ColorRole.AlternateBase: QColor(color["pane"]),
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: button,
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: QColor(color["menu"]),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.Highlight: highlight,
        QPalette.ColorRole.HighlightedText: highlighted,
        QPalette.ColorRole.Mid: mid,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Link: QColor(color["link"]),
    }
    for role, value in roles.items():
        palette.setColor(QPalette.ColorGroup.Active, role, value)
        palette.setColor(QPalette.ColorGroup.Inactive, role, value)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(color["pane"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(color["pane"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Mid, muted)
    return palette


def _spin_arrow(color: str, up: bool) -> str:
    """Маленький треугольник для кнопок счётчика. Своя рамка поля их иначе не рисует."""
    folder = Path.home() / ".cache" / "photofordocs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"spin-{'up' if up else 'down'}-{color.lstrip('#')}.png"
    if not path.exists():
        image = QImage(9, 6, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        if up:
            painter.drawPolygon(QPolygon([QPoint(4, 0), QPoint(8, 5), QPoint(0, 5)]))
        else:
            painter.drawPolygon(QPolygon([QPoint(0, 0), QPoint(8, 0), QPoint(4, 5)]))
        painter.end()
        image.save(str(path))
    return path.as_posix()


def stylesheet_for(dark: bool) -> str:
    color = _colors(dark)
    arrow = color["text"]
    up = _spin_arrow(arrow, True)
    down = _spin_arrow(arrow, False)
    return f"""
        QMainWindow, QDialog, QWidget {{ color: {color["text"]}; font-size: 12px; }}
        QMainWindow, QDialog, QScrollArea > QWidget {{ background: {color["window"]}; }}
        QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QListView, QTreeView {{
            background: {color["field"]}; color: {color["text"]};
            border: 1px solid {color["button_border"]};
            selection-background-color: {color["selected"]}; selection-color: {color["selected_text"]};
        }}
        QComboBox QAbstractItemView {{
            background: {color["field"]}; color: {color["text"]};
            selection-background-color: {color["selected"]}; selection-color: {color["selected_text"]};
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 16px;
            border-left: 1px solid {color["button_border"]};
            background: {color["button"]};
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 16px;
            border-left: 1px solid {color["button_border"]};
            border-top: 1px solid {color["button_border"]};
            background: {color["button"]};
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background: {color["hover"]};
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            width: 9px; height: 6px;
            image: url({up});
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            width: 9px; height: 6px;
            image: url({down});
        }}
        QPushButton {{ background: {color["button"]}; border: 1px solid {color["button_border"]}; padding: 3px 6px; }}
        QPushButton#step {{ padding: 4px 10px; }}
        QPushButton#step:checked {{ background: {color["checked"]}; font-weight: 600; }}
        QTreeWidget, QListWidget {{ background: {color["field"]}; color: {color["text"]}; }}
        QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
        QLabel#photopreview {{ background: {color["preview"]}; color: {color["preview_text"]}; border: 1px solid {color["button_border"]}; }}
        QLabel#framepreview {{ background: {color["mat"]}; color: {color["text"]}; border: 1px solid {color["mat_border"]}; padding: 12px; }}
        QFrame#filepane {{ background: {color["pane"]}; }}
        QWidget#scrollcorner {{ background: {color["track"]}; }}
        QGroupBox {{
            background: {color["group"]};
            border: 1px solid {color["group_border"]};
            border-radius: 4px;
            margin-top: 14px;
            padding: 12px 8px 8px 8px;
        }}
        QGroupBox#tonebox {{
            margin-top: 10px;
            padding: 6px 6px 4px 6px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 1px 6px;
            background: {color["title_bg"]};
            color: {color["title"]};
            font-weight: 700;
        }}
        QSplitter::handle {{ background: {color["split"]}; }}
        QSplitter::handle:horizontal {{ width: 6px; }}
        QSplitter::handle:vertical {{ height: 6px; }}
        QToolButton#filetool {{ background: {color["button"]}; border: 1px solid {color["button_border"]}; border-radius: 3px; padding: 0; }}
        QToolButton#filetool:hover {{ background: {color["hover"]}; }}
        QToolButton#filetool:checked {{ background: {color["selected"]}; border-color: {color["accent"]}; }}
        QToolButton#sizeaction {{ background: {color["button"]}; border: 1px solid {color["button_border"]}; border-radius: 2px; padding: 2px 4px; }}
        QToolButton#sizeaction:disabled {{ color: {color["muted"]}; background: {color["pane"]}; border-color: {color["line"]}; }}
        QWidget#overlaybar {{ background: transparent; }}
        QToolButton#viewzoom {{ background: {color["glass"]}; border: 1px solid {color["glass_border"]}; border-radius: 3px; padding: 0; color: {color["glass_text"]}; }}
        QToolButton#viewzoom:hover {{ background: {color["glass_hover"]}; }}
        QToolButton#viewzoom:disabled {{ background: {color["glass_disabled"]}; color: {color["muted"]}; }}
        QScrollBar:vertical {{ background: {color["track"]}; width: 14px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {color["handle"]}; min-height: 28px; border: 1px solid {color["handle_border"]}; }}
        QScrollBar::handle:vertical:hover {{ background: {color["handle_hover"]}; }}
        QScrollBar:horizontal {{ background: {color["track"]}; height: 14px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: {color["handle"]}; min-width: 28px; border: 1px solid {color["handle_border"]}; }}
        QScrollBar::handle:horizontal:hover {{ background: {color["handle_hover"]}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QPushButton#printaction {{ background: {color["field"]}; border: 1px solid {color["button_border"]}; padding: 4px 8px; text-align: left; }}
        QPushButton#printaction:hover {{ background: {color["hover"]}; }}
        QPushButton#printaction:disabled {{ color: {color["muted"]}; background: {color["pane"]}; border-color: {color["line"]}; }}
        QMenu {{ background: {color["menu"]}; border: 1px solid {color["menu_border"]}; color: {color["text"]}; }}
        QMenu::item {{ padding: 5px 28px 5px 18px; background: transparent; }}
        QMenu::item:selected {{ background: {color["selected"]}; color: {color["selected_text"]}; }}
        QMenu::separator {{ height: 1px; background: {color["line"]}; margin: 4px 8px; }}
        QToolTip {{ background: {color["menu"]}; color: {color["text"]}; border: 1px solid {color["menu_border"]}; }}
    """


def apply_chrome(app: QApplication | None) -> None:
    if app is None:
        return
    dark = is_dark(app)
    app.setPalette(palette_for(dark))
    app.setStyleSheet(stylesheet_for(dark))


def bind_theme(app: QApplication | None) -> None:
    """Красит приложение. Смена схемы системы действует, пока выбрано «Авто»."""
    global _bound
    apply_chrome(app)
    if app is None or _bound:
        return
    app.styleHints().colorSchemeChanged.connect(lambda *_scheme: _retint(app))
    _bound = True


def _retint(app: QApplication) -> None:
    if _choice != "auto":
        return
    apply_chrome(app)
    for widget in app.topLevelWidgets():
        refresh = getattr(widget, "refresh_theme", None)
        if refresh is not None:
            refresh()
        widget.update()
