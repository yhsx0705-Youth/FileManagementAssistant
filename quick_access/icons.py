"""Small Windows file-type icons, with dependency-free fallback images."""

from __future__ import annotations

import ctypes
import math
import os
import struct
import sys
import tkinter as tk
import zlib
from ctypes import wintypes
from pathlib import Path


TYPE_STYLES = {
    ".pdf": ("#C94646", "PDF"),
    ".doc": ("#2867B2", "W"), ".docx": ("#2867B2", "W"),
    ".rtf": ("#2867B2", "W"),
    ".xls": ("#238257", "X"), ".xlsx": ("#238257", "X"),
    ".csv": ("#238257", "X"),
    ".ppt": ("#CF6635", "P"), ".pptx": ("#CF6635", "P"),
    ".txt": ("#687889", "T"), ".md": ("#687889", "T"),
    ".zip": ("#9A753C", "Z"), ".rar": ("#9A753C", "Z"),
    ".7z": ("#9A753C", "Z"),
    ".png": ("#8170B7", "I"), ".jpg": ("#8170B7", "I"),
    ".jpeg": ("#8170B7", "I"), ".gif": ("#8170B7", "I"),
    ".lnk": ("#4C7C9F", "L"), ".url": ("#397C8A", "@"), ".web": ("#397C8A", "@"),
}
GLYPHS = {
    "P": ("110", "101", "110", "100", "100"),
    "D": ("110", "101", "101", "101", "110"),
    "F": ("111", "100", "110", "100", "100"),
    "@": ("111", "101", "111", "100", "111"),
    "X": ("101", "101", "010", "101", "101"),
    "T": ("111", "010", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "I": ("111", "010", "010", "010", "111"),
    "L": ("100", "100", "100", "100", "111"),
    "W": ("101", "101", "101", "111", "101"),
}


def _png(rgba: bytes, size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def shell_icon(extension: str, folder: bool = False, *, path: str | None = None) -> tuple[bytes, int] | None:
    """Read a type icon, or a shortcut's real Shell icon (including its overlay)."""
    if sys.platform != "win32":
        return None

    class SHFILEINFO(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD), ("szDisplayName", wintypes.WCHAR * 260),
                    ("szTypeName", wintypes.WCHAR * 80)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    shell.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.POINTER(SHFILEINFO), wintypes.UINT, wintypes.UINT]
    shell.SHGetFileInfoW.restype = ctypes.c_size_t
    user.DestroyIcon.argtypes = [wintypes.HICON]
    user.DestroyIcon.restype = wintypes.BOOL
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER),
                                    wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi.CreateDIBSection.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteObject.restype = wintypes.BOOL
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.DeleteDC.restype = wintypes.BOOL
    gdi.GdiFlush.argtypes = []
    gdi.GdiFlush.restype = wintypes.BOOL
    user.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
    user.DrawIconEx.restype = wintypes.BOOL

    info = SHFILEINFO()
    # Shortcuts need the real path; USEFILEATTRIBUTES only returns a generic .lnk icon.
    flags = 0x121 if path is not None else 0x111
    if not shell.SHGetFileInfoW(path if path is not None else "folder" if folder else "file" + extension,
                               0 if path is not None else 0x10 if folder else 0x80,
                               ctypes.byref(info), ctypes.sizeof(info), flags):
        return None
    if not info.hIcon:
        return None
    dc, bitmap, previous = None, None, None
    try:
        size = 16
        dc = gdi.CreateCompatibleDC(None)
        if not dc:
            return None
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = size, -size
        header.biPlanes, header.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            return None
        previous = gdi.SelectObject(dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            previous = None
            return None
        frames = []
        # Rendering against black and white recovers alpha for both modern and masked icons.
        for background in (0, 255):
            gdi.GdiFlush()
            ctypes.memset(bits.value, background, size * size * 4)
            if not user.DrawIconEx(dc, 0, 0, info.hIcon, size, size, 0, None, 3):
                return None
            gdi.GdiFlush()
            frames.append(ctypes.string_at(bits.value, size * size * 4))
        rgba = bytearray()
        black, white = frames
        for offset in range(0, len(black), 4):
            alpha = 255 - max(white[offset + channel] - black[offset + channel] for channel in range(3))
            alpha = max(0, min(255, alpha))
            for channel in (2, 1, 0):
                rgba.append(min(255, round(black[offset + channel] * 255 / alpha)) if alpha else 0)
            rgba.append(alpha)
        return _png(bytes(rgba), size), info.iIcon
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        user.DestroyIcon(info.hIcon)


class IconCache:
    def __init__(self, master: tk.Misc):
        self.master = master
        self.images: dict[tuple[str, str], tk.PhotoImage] = {}
        self._generic_index: int | None = None
        self._generic_checked = False

    def get(self, path: str, kind: str) -> tk.PhotoImage:
        extension = Path(path).suffix.lower() if kind in ("file", "shortcut") else ""
        if kind == "web":
            extension = ".web"
        key = (kind, os.path.normcase(path) if kind == "shortcut" else extension)
        if key in self.images:
            return self.images[key]
        try:
            if kind == "web":
                image = self._globe()
            else:
                if not self._generic_checked:
                    generic = shell_icon(".suda_unregistered_type")
                    self._generic_index = generic[1] if generic else None
                    self._generic_checked = True
                native = (shell_icon(extension, path=path) if kind == "shortcut"
                          else shell_icon(extension, kind == "folder"))
                # Known document types must remain distinguishable even without an installed app.
                if native and not (extension in TYPE_STYLES and native[1] == self._generic_index):
                    image = tk.PhotoImage(master=self.master, data=native[0], format="png")
                else:
                    image = self._fallback(extension, kind)
        except (OSError, ValueError, AttributeError, tk.TclError):
            image = self._fallback(extension, kind)
        self.images[key] = image
        return image

    def _globe(self) -> tk.PhotoImage:
        image = tk.PhotoImage(master=self.master, width=16, height=16)
        for y in range(16):
            for x in range(16):
                dx, dy = x - 7.5, y - 7.5
                radius = math.hypot(dx, dy)
                if radius > 7.4:
                    continue
                meridian = 3.5 * math.sqrt(max(0, 1 - (dy / 7.4) ** 2))
                grid = abs(abs(dx) - meridian) < 0.65 or abs(dx) < 0.6 or abs(abs(dy) - 3) < 0.6 or abs(dy) < 0.6
                color = "#286CA4" if radius > 6.6 else "#C7E8FA" if grid else "#429AD0"
                image.put(color, (x, y))
        return image

    def _fallback(self, extension: str, kind: str) -> tk.PhotoImage:
        image = tk.PhotoImage(master=self.master, width=16, height=16)
        if kind == "folder":
            image.put("#C79531", to=(1, 4, 8, 7))
            image.put("#E8B64C", to=(1, 6, 15, 14))
            image.put("#F3CD70", to=(2, 8, 15, 14))
            return image
        color, label = TYPE_STYLES.get(extension, ("#687889", "F"))
        image.put("#DCE2E8", to=(3, 0, 13, 15))
        image.put("#FFFFFF", to=(4, 1, 12, 14))
        image.put(color, to=(1, 7, 15, 15))
        left = (16 - (len(label) * 4 - 1)) // 2
        for index, letter in enumerate(label):
            for y, row in enumerate(GLYPHS[letter]):
                for x, value in enumerate(row):
                    if value == "1":
                        image.put("#FFFFFF", (left + index * 4 + x, 8 + y))
        return image
