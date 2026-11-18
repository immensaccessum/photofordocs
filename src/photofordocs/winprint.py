"""Печать на Windows своим окном: выбранный принтер и сохранённые параметры драйвера."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from photofordocs.session import CONFIG_DIR

DM_IN_BUFFER = 8
DM_OUT_BUFFER = 2
DM_IN_PROMPT = 4
PRINTER_ENUM_LOCAL = 2
PRINTER_ENUM_CONNECTIONS = 4
HORZRES = 8
VERTRES = 10
HALFTONE = 4
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

_winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
_gdi = ctypes.WinDLL("gdi32", use_last_error=True)


class _PrinterInfo4(ctypes.Structure):
    _fields_ = [
        ("pPrinterName", wintypes.LPWSTR),
        ("pServerName", wintypes.LPWSTR),
        ("Attributes", wintypes.DWORD),
    ]


class _DocInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_int),
        ("lpszDocName", wintypes.LPCWSTR),
        ("lpszOutput", wintypes.LPCWSTR),
        ("lpszDatatype", wintypes.LPCWSTR),
        ("fwType", wintypes.DWORD),
    ]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _bind() -> None:
    _winspool.OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    _winspool.OpenPrinterW.restype = wintypes.BOOL
    _winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    _winspool.ClosePrinter.restype = wintypes.BOOL
    _winspool.DocumentPropertiesW.argtypes = [
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _winspool.DocumentPropertiesW.restype = ctypes.c_long
    _winspool.EnumPrintersW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _winspool.EnumPrintersW.restype = wintypes.BOOL
    _winspool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _winspool.GetDefaultPrinterW.restype = wintypes.BOOL
    _gdi.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    _gdi.CreateDCW.restype = wintypes.HDC
    _gdi.DeleteDC.argtypes = [wintypes.HDC]
    _gdi.DeleteDC.restype = wintypes.BOOL
    _gdi.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(_DocInfo)]
    _gdi.StartDocW.restype = ctypes.c_int
    _gdi.EndDoc.argtypes = [wintypes.HDC]
    _gdi.EndDoc.restype = ctypes.c_int
    _gdi.StartPage.argtypes = [wintypes.HDC]
    _gdi.StartPage.restype = ctypes.c_int
    _gdi.EndPage.argtypes = [wintypes.HDC]
    _gdi.EndPage.restype = ctypes.c_int
    _gdi.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    _gdi.GetDeviceCaps.restype = ctypes.c_int
    _gdi.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    _gdi.SetStretchBltMode.restype = ctypes.c_int
    _gdi.StretchDIBits.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.DWORD,
    ]
    _gdi.StretchDIBits.restype = ctypes.c_int


_bind()


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in name.strip())
    return cleaned[:80] or "printer"


def devmode_path(name: str) -> Path:
    return CONFIG_DIR / "printers" / f"{_safe_name(name)}.devmode"


def load_devmode(name: str) -> bytes | None:
    path = devmode_path(name)
    if path.is_file():
        data = path.read_bytes()
        if data:
            return data
    folder = CONFIG_DIR / "printers"
    if not folder.is_dir():
        return None
    needle = name.encode("utf-16le")[:32]
    for cfg in folder.glob("*.cfg"):
        raw = cfg.read_bytes()
        if needle and raw.startswith(needle):
            return raw
    return None


def save_devmode(name: str, data: bytes) -> None:
    path = devmode_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def printer_names() -> list[str]:
    needed = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    _winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if needed.value <= 0:
        return []
    buffer = ctypes.create_string_buffer(needed.value)
    if not _winspool.EnumPrintersW(flags, None, 4, buffer, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
        return []
    rows = ctypes.cast(buffer, ctypes.POINTER(_PrinterInfo4))
    names = []
    for index in range(returned.value):
        title = rows[index].pPrinterName
        if title and title not in names:
            names.append(title)
    return names


def default_printer() -> str:
    size = wintypes.DWORD(0)
    _winspool.GetDefaultPrinterW(None, ctypes.byref(size))
    if size.value <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _winspool.GetDefaultPrinterW(buffer, ctypes.byref(size)):
        return ""
    return buffer.value


def _open(name: str):
    handle = wintypes.HANDLE()
    if not _winspool.OpenPrinterW(name, ctypes.byref(handle), None):
        return None
    return handle


def _properties(name: str, hwnd: int, current: bytes | None, prompt: bool) -> bytes | None:
    handle = _open(name)
    if handle is None:
        return None
    try:
        needed = _winspool.DocumentPropertiesW(hwnd, handle, name, None, None, 0)
        if needed <= 0:
            return None
        output = ctypes.create_string_buffer(needed)
        mode = DM_OUT_BUFFER | (DM_IN_PROMPT if prompt else 0)
        incoming = None
        if current and len(current) == needed:
            incoming = ctypes.create_string_buffer(current)
            mode |= DM_IN_BUFFER
        result = _winspool.DocumentPropertiesW(hwnd, handle, name, output, incoming, mode)
        if result != 1:
            return None
        return output.raw
    finally:
        _winspool.ClosePrinter(handle)


def configure(name: str, hwnd: int) -> bytes | None:
    """Окно драйвера. None — оператор закрыл его, не сохранив."""
    return _properties(name, hwnd, load_devmode(name), True)


def print_images(name: str, images: list[tuple[int, int, int, bytes]], copies: int) -> str | None:
    """Печатает страницы BGRA. Пустая строка ошибки значит, что принтер принял задание."""
    if not name:
        return "Принтер не выбран"
    if not images:
        return "Нет страниц для печати"
    devmode = _properties(name, 0, load_devmode(name), False)
    if not devmode:
        return "Не удалось открыть принтер"
    stored = ctypes.create_string_buffer(devmode)
    hdc = _gdi.CreateDCW("WINSPOOL", name, None, ctypes.cast(stored, ctypes.c_void_p) if stored is not None else None)
    if not hdc:
        return "Не удалось открыть принтер"
    info = _DocInfo()
    info.cbSize = ctypes.sizeof(_DocInfo)
    info.lpszDocName = "Фото на документы"
    if _gdi.StartDocW(hdc, ctypes.byref(info)) <= 0:
        _gdi.DeleteDC(hdc)
        return "Принтер не принял задание"
    try:
        for _copy in range(max(1, copies)):
            for width, height, stride, raw in images:
                if _gdi.StartPage(hdc) <= 0:
                    _gdi.AbortDoc(hdc)
                    return "Принтер не начал страницу"
                _draw_page(hdc, width, height, stride, raw)
                _gdi.EndPage(hdc)
        _gdi.EndDoc(hdc)
    finally:
        _gdi.DeleteDC(hdc)
    return None


def _draw_page(hdc, width: int, height: int, stride: int, raw: bytes) -> None:
    page_w = _gdi.GetDeviceCaps(hdc, HORZRES)
    page_h = _gdi.GetDeviceCaps(hdc, VERTRES)
    if page_w <= 0 or page_h <= 0 or width <= 0 or height <= 0:
        return
    scale = min(page_w / width, page_h / height)
    draw_w = max(1, int(width * scale))
    draw_h = max(1, int(height * scale))
    origin_x = (page_w - draw_w) // 2
    origin_y = (page_h - draw_h) // 2
    header = _BitmapInfoHeader()
    header.biSize = ctypes.sizeof(_BitmapInfoHeader)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biSizeImage = stride * height
    _gdi.SetStretchBltMode(hdc, HALFTONE)
    pixels = ctypes.create_string_buffer(raw)
    _gdi.StretchDIBits(
        hdc,
        origin_x,
        origin_y,
        draw_w,
        draw_h,
        0,
        0,
        width,
        height,
        pixels,
        ctypes.byref(header),
        DIB_RGB_COLORS,
        SRCCOPY,
    )


# AbortDoc may be missing from the early bind if a printer test imports symbols only.
_gdi.AbortDoc.argtypes = [wintypes.HDC]
_gdi.AbortDoc.restype = ctypes.c_int
