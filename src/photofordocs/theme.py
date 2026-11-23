"""Светлая и тёмная тема по схеме системы. Смена схемы перекрашивает окно сразу."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_bound = False


def is_dark(app: QApplication | None = None) -> bool:
    app = app or QApplication.instance()
    if app is None:
        return False
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def slider_chrome() -> tuple[str, str, str]:
    """Ручка ползунка: заливка, обводка ручки, обводка дорожки."""
    if is_dark():
        return "#e6e6e6", "#111111", "#6a6a6a"
    return "#ffffff", "#4a4a4a", "#9a9a9a"


def _colors(dark: bool) -> dict[str, str]:
    if dark:
        return {
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
        }
    return {
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
    }


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
        QPalette.ColorRole.Link: QColor("#8eb6e8") if dark else QColor("#1d4f89"),
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


def stylesheet_for(dark: bool) -> str:
    color = _colors(dark)
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
        QPushButton {{ background: {color["button"]}; border: 1px solid {color["button_border"]}; padding: 3px 6px; }}
        QPushButton#step {{ padding: 4px 10px; }}
        QPushButton#step:checked {{ background: {color["checked"]}; font-weight: 600; }}
        QTreeWidget, QListWidget {{ background: {color["field"]}; color: {color["text"]}; }}
        QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
        QLabel#photopreview {{ background: white; color: #202020; border: 1px solid {color["button_border"]}; }}
        QFrame#filepane {{ background: {color["pane"]}; }}
        QWidget#scrollcorner {{ background: {color["track"]}; }}
        QGroupBox {{
            background: {color["group"]};
            border: 1px solid {color["group_border"]};
            border-radius: 4px;
            margin-top: 14px;
            padding: 12px 8px 8px 8px;
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
        QToolButton#sizeaction {{ background: {color["button"]}; border: 1px solid {color["button_border"]}; border-radius: 2px; padding: 2px 4px; }}
        QWidget#overlaybar {{ background: transparent; }}
        QToolButton#viewzoom {{ background: rgba(255, 255, 255, 140); border: 1px solid rgba(90, 90, 90, 80); border-radius: 3px; padding: 0; color: #202020; }}
        QToolButton#viewzoom:hover {{ background: rgba(255, 255, 255, 210); }}
        QToolButton#viewzoom:disabled {{ background: rgba(243, 243, 243, 100); color: {color["muted"]}; }}
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
    """Красит приложение и дальше слушает смену светлой и тёмной схемы."""
    global _bound
    apply_chrome(app)
    if app is None or _bound:
        return
    app.styleHints().colorSchemeChanged.connect(lambda *_scheme: _retint(app))
    _bound = True


def _retint(app: QApplication) -> None:
    apply_chrome(app)
    for widget in app.topLevelWidgets():
        refresh = getattr(widget, "refresh_theme", None)
        if refresh is not None:
            refresh()
        widget.update()
