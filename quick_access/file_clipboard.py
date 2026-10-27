"""Windows file clipboard interop with Explorer; copies never request a move."""

import ctypes
import io
import html
import os
import struct
import time
from ctypes import wintypes

from .storage import EntryError, normalize_path

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
CLIPBOARD_OPEN_ATTEMPTS = 5


def hdrop_payload(paths):
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(paths) + "\0\0").encode("utf-16-le")


def _api():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.CloseClipboard.argtypes = []
    user.EmptyClipboard.argtypes = []
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    user.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return user, kernel


def _windows_error(action):
    error = getattr(ctypes, "get_last_error", lambda: 0)()
    if hasattr(ctypes, "WinError"):
        return ctypes.WinError(error)
    return OSError(error, action)


def _open_clipboard(user, owner):
    for attempt in range(CLIPBOARD_OPEN_ATTEMPTS):
        if user.OpenClipboard(owner):
            return
        if attempt + 1 < CLIPBOARD_OPEN_ATTEMPTS:
            time.sleep(0.025 * (attempt + 1))
    raise EntryError("系统剪贴板正被其他程序使用，请稍候再试。")


def _global_memory(kernel, data):
    memory = kernel.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not memory:
        raise _windows_error("无法分配剪贴板内存")
    try:
        pointer = kernel.GlobalLock(memory)
        if not pointer:
            raise _windows_error("无法锁定剪贴板内存")
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(memory)
        return memory
    except BaseException:
        kernel.GlobalFree(memory)
        raise


def write_text(widget, text):
    """Write text through Tk, retrying transient clipboard ownership conflicts."""
    import tkinter as tk

    for attempt in range(CLIPBOARD_OPEN_ATTEMPTS):
        try:
            widget.clipboard_clear()
            widget.clipboard_append(text)
            widget.update_idletasks()
            return
        except tk.TclError as exc:
            if attempt + 1 == CLIPBOARD_OPEN_ATTEMPTS:
                raise EntryError("系统剪贴板正被其他程序使用，请稍候再复制。") from exc
            time.sleep(0.025 * (attempt + 1))


def write_image(root, image):
    """Publish a bitmap so image editors and chat apps can paste it."""
    stream = io.BytesIO()
    image.convert("RGB").save(stream, format="BMP")
    user, kernel = _api()
    memory = _global_memory(kernel, stream.getvalue()[14:])
    transferred = False
    try:
        _open_clipboard(user, root.winfo_id())
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(8, memory):
                raise _windows_error("无法复制图片")
            transferred = True
        finally:
            user.CloseClipboard()
    finally:
        if not transferred:
            kernel.GlobalFree(memory)


def write_rich_content(root, parts):
    """Offer ordered text/images as HTML, with text and bitmap fallbacks."""
    import base64
    from PIL import Image

    fragment = "".join(html.escape(part["text"]).replace("\n", "<br>")
                       if "text" in part else f'<img src="data:image/png;base64,{part["image"]}">'
                       for part in parts).encode("utf-8")
    prefix, suffix = b"<html><body><!--StartFragment-->", b"<!--EndFragment--></body></html>"
    template = "Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\nStartFragment:{:010d}\r\nEndFragment:{:010d}\r\n"
    start = len(template.format(0, 0, 0, 0).encode("ascii"))
    header = template.format(start, start + len(prefix) + len(fragment) + len(suffix),
                             start + len(prefix), start + len(prefix) + len(fragment)).encode("ascii")
    user, kernel = _api()
    html_format = user.RegisterClipboardFormatW("HTML Format")
    if not html_format:
        raise EntryError("无法注册图文剪贴板格式。")
    formats = [(html_format, header + prefix + fragment + suffix + b"\0"),
               (CF_UNICODETEXT, "".join(part.get("text", "") for part in parts).encode("utf-16-le") + b"\0\0")]
    first = next(part["image"] for part in parts if "image" in part)
    with Image.open(io.BytesIO(base64.b64decode(first))) as image:
        stream = io.BytesIO()
        image.convert("RGB").save(stream, "BMP")
        formats.append((8, stream.getvalue()[14:]))
    allocated = []
    try:
        for format_id, payload in formats:
            allocated.append([format_id, _global_memory(kernel, payload), False])
        _open_clipboard(user, root.winfo_id())
        try:
            if not user.EmptyClipboard():
                raise _windows_error("无法清空剪贴板")
            for item in allocated:
                if not user.SetClipboardData(item[0], item[1]):
                    raise _windows_error("无法复制卡片内容")
                item[2] = True
        finally:
            user.CloseClipboard()
    finally:
        for _, memory, transferred in allocated:
            if not transferred:
                kernel.GlobalFree(memory)


def write_files(root, paths):
    paths = [normalize_path(value) for value in paths]
    if not paths or any(not os.path.exists(path) for path in paths):
        raise EntryError("请选择可用的文件或文件夹。")
    user, kernel = _api()
    formats = [(CF_HDROP, hdrop_payload(paths), True),
               (CF_UNICODETEXT, "\r\n".join(paths).encode("utf-16-le") + b"\0\0", False)]
    preferred_drop = user.RegisterClipboardFormatW("Preferred DropEffect")
    if preferred_drop:
        formats.append((preferred_drop, struct.pack("<I", 1), False))

    allocated = []
    try:
        for format_id, data, required in formats:
            try:
                memory = _global_memory(kernel, data)
            except OSError:
                if required:
                    raise
                continue
            allocated.append({"format": format_id, "memory": memory,
                              "required": required, "transferred": False})

        _open_clipboard(user, root.winfo_id())
        try:
            if not user.EmptyClipboard():
                raise _windows_error("Windows 无法清空剪贴板")
            for item in allocated:
                if user.SetClipboardData(item["format"], item["memory"]):
                    item["transferred"] = True
                elif item["required"]:
                    raise _windows_error("Windows 无法复制文件到剪贴板")
        finally:
            user.CloseClipboard()
    finally:
        for item in allocated:
            if not item["transferred"]:
                kernel.GlobalFree(item["memory"])


def read_files(root):
    user, kernel = _api()
    _open_clipboard(user, root.winfo_id())
    try:
        handle = user.GetClipboardData(CF_HDROP)
        if handle:
            shell = ctypes.WinDLL("shell32", use_last_error=True)
            shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell.DragQueryFileW.restype = wintypes.UINT
            count = shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            paths = []
            for index in range(count):
                length = shell.DragQueryFileW(handle, index, None, 0)
                buffer = ctypes.create_unicode_buffer(length + 1)
                shell.DragQueryFileW(handle, index, buffer, length + 1)
                paths.append(buffer.value)
            return paths
        handle = user.GetClipboardData(CF_UNICODETEXT)
        if handle:
            pointer = kernel.GlobalLock(handle)
            if pointer:
                try:
                    return [normalize_path(value) for value in ctypes.wstring_at(pointer).splitlines() if value.strip()]
                finally:
                    kernel.GlobalUnlock(handle)
        return []
    finally:
        user.CloseClipboard()
