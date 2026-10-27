"""Windows notification-area icon and reversible edge docking."""

from __future__ import annotations

import ctypes
import hashlib
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from tkinter import messagebox

from .branding import asset_path


def restore_message_name(directory):
    return "FileAssistant.Show-" + hashlib.sha256(str(directory).casefold().encode()).hexdigest()


def request_restore(directory):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user.RegisterWindowMessageW.restype = wintypes.UINT
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    message = user.RegisterWindowMessageW(restore_message_name(directory))
    hwnd = user.FindWindowW(restore_message_name(directory), None)
    return bool(hwnd and message and user.PostMessageW(hwnd, message, 0, 0))


def edge_position(rect, work, threshold=20):
    """Snap to the nearest nearby left, right or top edge of the work area."""
    left, top, right, bottom = rect
    wl, wt, wr, wb = work
    width, height = right - left, bottom - top
    if width >= wr - wl or height > wb - wt:
        return None
    x = max(wl, min(left, wr - width))
    y = max(wt, min(top, wb - height))
    candidates = [(abs(left - wl), "left", (wl, y, wl + width, y + height)),
                  (abs(right - wr), "right", (wr - width, y, wr, y + height)),
                  (abs(top - wt), "top", (x, wt, x + width, wt + height))]
    distance, side, target = min(candidates, key=lambda item: item[0])
    if distance <= threshold:
        return side, target
    return None


def ball_position(left, top, size, work, snap=True):
    wl, wt, wr, wb = work
    right, bottom = max(wl, wr - size), max(wt, wb - size)
    x = max(wl, min(left, right))
    y = max(wt, min(top, bottom))
    if snap:
        _, (x, y) = min([(x - wl, (wl, y)), (right - x, (right, y)),
                         (y - wt, (x, wt))], key=lambda item: item[0])
    return x, y


def ball_side(position, size, work):
    x, y = position
    wl, wt, wr, _ = work
    if x == wl:
        return "left"
    if x == wr - size:
        return "right"
    if y == wt:
        return "top"
    return None


def ball_frame(position, size, side, exposure):
    """Crop inside this monitor, so the hidden half cannot leak onto a neighbour."""
    x, y = position
    visible = max(size // 2, min(size, round(size * exposure)))
    if side == "left":
        return (x, y, visible, size), (visible - size, 0)
    if side == "right":
        return (x + size - visible, y, visible, size), (0, 0)
    if side == "top":
        return (x, y, size, visible), (0, visible - size)
    return (x, y, size, size), (0, 0)


class WindowMotion:
    """Short, interruptible easing on Tk's event loop; no blocking or worker threads."""
    def __init__(self, root):
        self.root = root
        self.jobs = {}
        self.tokens = {}
        self.closed = False

    def active(self, name):
        return name in self.tokens

    def cancel(self, name):
        self.tokens.pop(name, None)
        job = self.jobs.pop(name, None)
        if job is not None:
            self.root.after_cancel(job)

    def start(self, name, start, target, apply, on_complete=None, duration=0.22):
        self.cancel(name)
        if self.closed:
            return
        if start == target:
            apply(target)
            if on_complete:
                on_complete()
            return
        token = self.tokens[name] = object()
        began = time.monotonic()

        def frame():
            if self.tokens.get(name) is not token:
                return
            self.jobs.pop(name, None)
            progress = min(1.0, max(0.0, (time.monotonic() - began) / duration))
            eased = 1 - (1 - progress) ** 3
            apply(target if progress == 1 else tuple(a + (b - a) * eased for a, b in zip(start, target)))
            if self.tokens.get(name) is not token:
                return
            if progress == 1:
                self.tokens.pop(name, None)
                if on_complete:
                    on_complete()
            else:
                self.jobs[name] = self.root.after(16, frame)

        self.jobs[name] = self.root.after(0, frame)

    def close(self):
        self.closed = True
        for name in list(self.tokens):
            self.cancel(name)


class WindowsTray:
    """A hidden native window; Tk's event loop also pumps its messages."""
    def __init__(self, root, show, menu, directory):
        self.root, self.show, self.menu = root, show, menu
        self.available = False
        self.events = deque()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_message = 0x8000 + 41
        self.callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", self.callback_type),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                        ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

        self.kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self.user.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        self.user.RegisterClassW.restype = wintypes.WORD
        self.user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                             wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                             wintypes.HINSTANCE, ctypes.c_void_p]
        self.user.CreateWindowExW.restype = wintypes.HWND
        self.user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self.user.RegisterWindowMessageW.restype = wintypes.UINT
        self.user.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        self.user.LoadIconW.restype = wintypes.HICON
        self.user.DestroyWindow.argtypes = [wintypes.HWND]
        self.user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
        self.shell.Shell_NotifyIconW.restype = wintypes.BOOL
        self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")
        self.restore_message = self.user.RegisterWindowMessageW(restore_message_name(directory))
        self.callback = self.callback_type(self._message)
        self.instance = self.kernel.GetModuleHandleW(None)
        self.class_name = restore_message_name(directory)
        self.window_class = WNDCLASS()
        self.window_class.lpfnWndProc = self.callback
        self.window_class.hInstance = self.instance
        self.window_class.lpszClassName = self.class_name
        if not self.user.RegisterClassW(ctypes.byref(self.window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = self.user.CreateWindowExW(0, self.class_name, "", 0, 0, 0, 0, 0,
                                              None, None, self.instance, None)
        if not self.hwnd:
            self.user.UnregisterClassW(self.class_name, self.instance)
            raise ctypes.WinError(ctypes.get_last_error())
        self.data = NOTIFYICONDATA()
        self.data.cbSize = ctypes.sizeof(self.data)
        self.data.hWnd, self.data.uID = self.hwnd, 1
        self.data.uFlags, self.data.uCallbackMessage = 1 | 2 | 4, self.callback_message
        self.data.hIcon = self.user.LoadIconW(None, ctypes.c_void_p(32512))
        self.user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.LoadImageW.restype = wintypes.HANDLE
        self.user.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.owned_icon = self.user.LoadImageW(None, str(asset_path("app.ico")), 1,
                                              self.user.GetSystemMetrics(49),
                                              self.user.GetSystemMetrics(50), 0x10)
        if self.owned_icon:
            self.data.hIcon = self.owned_icon
        self.data.szTip = "文件管理辅助器 · 点击显示，右键退出"
        self.available = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(self.data)))
        if not self.available:
            self.close()
            raise OSError("无法创建系统托盘图标。")

    def _message(self, hwnd, message, wparam, lparam):
        if message == self.restore_message:
            self.events.append("show")
            return 0
        if message == self.callback_message:
            if lparam in (0x0202, 0x0203):  # Left click / double click.
                self.events.append("show")
            elif lparam in (0x0205, 0x007B):
                self.events.append("menu")
            return 0
        if message == self.taskbar_created and hasattr(self, "data"):
            self.events.append("taskbar")
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def process_events(self):
        # A native WndProc can interrupt Tcl with its GIL released. Never call Tk there,
        # including root.after(); consume its small queue from Tk's normal timer instead.
        while self.events:
            event = self.events.popleft()
            if event == "show":
                self.show()
            elif event == "menu":
                self._popup()
            elif event == "taskbar":
                self.available = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(self.data)))
                if not self.available:
                    self.show()

    def _popup(self):
        self.user.SetForegroundWindow(self.hwnd)
        try:
            self.menu.tk_popup(*self.root.winfo_pointerxy())
        finally:
            self.menu.grab_release()
            self.user.PostMessageW(self.hwnd, 0, 0, 0)

    def close(self):
        if hasattr(self, "data"):
            self.shell.Shell_NotifyIconW(2, ctypes.byref(self.data))
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
            self.user.UnregisterClassW(self.class_name, self.instance)
        if getattr(self, "owned_icon", None):
            self.user.DestroyIcon.argtypes = [wintypes.HICON]
            self.user.DestroyIcon(self.owned_icon)
            self.owned_icon = None
        self.available = False


class DesktopIntegration:
    def __init__(self, app):
        self.app, self.root = app, app.root
        self.side = None
        self.expanded_rect = None
        self.collapsed = False
        self._poll_job = self._drag_job = None
        self._unmap_job = None
        self.motion = WindowMotion(self.root)
        self._grace_until = 0
        self._outside_since = None
        self._clipboard_visible = False
        self.ball_size = app.workspace.settings.get("ball_size", 44)
        self.ball_enabled = app.workspace.settings.get("floating_ball", True)
        self.ball_position = None
        self._ball_drag_origin = None
        self._ball_dragged = False
        self._ball_side = None
        self._ball_exposure = 1.0
        self._ball_offset = (0, 0)
        self._ball_tuck_job = None
        self.enabled = tk.BooleanVar(value=app.workspace.settings["edge_docking"])
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="显示主界面", command=self.show)
        self.menu.add_command(label="打开剪贴板", command=self.show_clipboard)
        self.menu.add_command(label="关闭悬浮球" if self.ball_enabled else "打开悬浮球",
                              command=self.toggle_floating_ball)
        self.ball_menu_index = self.menu.index("end")
        self.menu.add_separator()
        self.menu.add_checkbutton(label="边缘吸附", variable=self.enabled, command=self.toggle_docking)
        self.ball_size_var = tk.IntVar(value=self.ball_size)
        self.size_menu = tk.Menu(self.menu, tearoff=False)
        for size, label in ((44, "100%（最小）"), (55, "125%"), (66, "150%"),
                            (77, "175%"), (88, "200%（最大）")):
            self.size_menu.add_radiobutton(label=label, variable=self.ball_size_var, value=size,
                                          command=self.resize_ball)
        self.menu.add_cascade(label="悬浮球大小", menu=self.size_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出程序", command=app.quit)
        self.tray = WindowsTray(self.root, self.show, self.menu, app.store.directory.resolve())
        self.user = self.tray.user
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.hwnd = self.user.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user.MonitorFromWindow.restype = wintypes.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.tab = tk.Toplevel(self.root)
        self.tab.withdraw()
        self.tab.title("文件管理辅助器悬浮球")
        self.tab.overrideredirect(True)
        self.tab.attributes("-topmost", True)
        background = "#FF00FF"
        self.tab.configure(bg=background)
        self.tab.attributes("-transparentcolor", background)
        self.tab.geometry(f"{self.ball_size}x{self.ball_size}+0+0")
        handle = tk.Canvas(self.tab, width=self.ball_size, height=self.ball_size, bg=background,
                           highlightthickness=0, bd=0, cursor="hand2")
        handle.pack(fill="both", expand=True)
        self.ball_canvas = handle
        self._draw_ball()
        handle.bind("<ButtonPress-1>", self._ball_press)
        handle.bind("<B1-Motion>", self._ball_motion)
        handle.bind("<ButtonRelease-1>", self._ball_release)
        handle.bind("<Enter>", self._ball_enter)
        handle.bind("<Leave>", self._ball_leave)
        handle.bind("<Button-3>", lambda _: self.tray._popup())
        self.root.bind("<Configure>", self._configured, add="+")
        self.root.bind("<Unmap>", self._main_unmapped, add="+")
        self.root.bind("<Map>", self._main_unmapped, add="+")
        self._poll_job = self.root.after(180, self._poll)

    def _draw_ball(self):
        from PIL import Image, ImageTk
        handle, size = self.ball_canvas, self.ball_size
        handle.delete("all")
        handle.configure(width=size, height=size)
        self._ball_offset = (0, 0)
        try:
            with Image.open(asset_path("app-ball.png" if size == 44 else "app-256.png")) as source:
                artwork = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            artwork.putalpha(artwork.getchannel("A").point(lambda value: 255 if value >= 128 else 0))
            self.ball_image = ImageTk.PhotoImage(artwork, master=self.tab)
            handle.create_image(size // 2, size // 2, image=self.ball_image)
        except (tk.TclError, OSError):
            handle.create_oval(2, 2, size - 2, size - 2, fill="#E5F0FB", outline="#83A9CC", width=1)
            handle.create_text(size // 2, size // 2, text="文", fill="#315F85",
                               font=("Microsoft YaHei UI", round(12 * size / 44), "bold"))

    def resize_ball(self):
        size = self.ball_size_var.get()
        try:
            self.app.workspace.set_ball_size(size)
        except (OSError, ValueError) as exc:
            self.ball_size_var.set(self.ball_size)
            messagebox.showerror("设置保存失败", str(exc), parent=self.tab)
            return
        self.motion.cancel("ball")
        self.motion.cancel("reveal")
        self._cancel_ball_tuck()
        old_size, side = self.ball_size, self._ball_side
        visible = self.tab.state() != "withdrawn"
        work = self.work_area(self._ball_hwnd()) if self.ball_position else None
        if self.ball_position and work:
            x, y = self.ball_position
            if side == "right":
                x = work[2] - size
            elif side == "top":
                x += (old_size - size) // 2
            else:
                y += (old_size - size) // 2
            self.ball_position = ball_position(x, y, size, work, self.enabled.get())
        self.ball_size = size
        self._draw_ball()
        if visible:
            self._show_ball(work=work)

    @property
    def available(self):
        return self.tray.available

    def rect(self):
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top, rect.right, rect.bottom

    def work_area(self, hwnd=None):
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        monitor = self.user.MonitorFromWindow(hwnd or self.hwnd, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom

    def move(self, rect):
        left, top, right, bottom = map(round, rect)
        self.user.SetWindowPos(self.hwnd, None, left, top, right - left, bottom - top, 0x0010 | 0x0004)

    def _main_frame(self, frame):
        self.move(frame[:4])
        self.root.attributes("-alpha", frame[4])

    def _resting(self):
        self._outside_since = None
        self._grace_until = time.monotonic() + 0.7

    def _folded_rect(self):
        dx = -14 if self.side == "left" else 14 if self.side == "right" else 0
        dy = -14 if self.side == "top" else 0
        left, top, right, bottom = self.expanded_rect
        return left + dx, top + dy, right + dx, bottom + dy

    def _ball_hwnd(self):
        return self.user.GetParent(self.tab.winfo_id()) or self.tab.winfo_id()

    def _move_ball(self, x, y):
        self._shift_ball_art((0, 0))
        self._ball_exposure = 1.0
        self.user.SetWindowPos(self._ball_hwnd(), None, round(x), round(y),
                               self.ball_size, self.ball_size, 0x0010 | 0x0004)

    def _shift_ball_art(self, offset):
        old_x, old_y = self._ball_offset
        self.ball_canvas.move("all", offset[0] - old_x, offset[1] - old_y)
        self._ball_offset = offset

    def _set_ball_exposure(self, exposure):
        self._ball_exposure = exposure
        geometry, offset = ball_frame(self.ball_position, self.ball_size, self._ball_side, exposure)
        self._shift_ball_art(offset)
        self.user.SetWindowPos(self._ball_hwnd(), None, *geometry, 0x0010 | 0x0004)

    def _cancel_ball_tuck(self):
        if self._ball_tuck_job is not None:
            self.root.after_cancel(self._ball_tuck_job)
            self._ball_tuck_job = None

    def _ball_enter(self, _event=None):
        if self.motion.closed:
            return
        self._cancel_ball_tuck()
        if self._ball_side and self._ball_drag_origin is None:
            self.motion.start("reveal", (self._ball_exposure,), (1.0,),
                              lambda value: self._set_ball_exposure(value[0]), duration=0.16)

    def _ball_leave(self, _event=None):
        if self.motion.closed:
            return
        self._cancel_ball_tuck()
        if self._ball_side:
            self._ball_tuck_job = self.root.after(600, self._tuck_ball)

    def _tuck_ball(self):
        self._ball_tuck_job = None
        if not self._ball_side or self.tab.state() == "withdrawn":
            return
        point, rect = wintypes.POINT(), wintypes.RECT()
        self.user.GetCursorPos(ctypes.byref(point))
        self.user.GetWindowRect(self._ball_hwnd(), ctypes.byref(rect))
        inside = rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom
        if inside or self._ball_drag_origin is not None or self.root.grab_current() is not None:
            self._ball_tuck_job = self.root.after(600, self._tuck_ball)
            return
        self.motion.start("reveal", (self._ball_exposure,), (0.5,),
                          lambda value: self._set_ball_exposure(value[0]), duration=0.18)

    def _dock_ball(self, work):
        self._ball_side = ball_side(self.ball_position, self.ball_size, work) if self.enabled.get() else None
        if self._ball_side:
            self._ball_leave()

    def _show_ball(self, position=None, work=None):
        if getattr(self, "tab", None) is None or not getattr(self, "ball_enabled", True):
            return
        self.motion.cancel("ball")
        self.motion.cancel("reveal")
        self._cancel_ball_tuck()
        if work is None:
            work = self.work_area(self._ball_hwnd()) if position is None and self.ball_position else self.work_area()
        if position is None:
            position = self.ball_position or (work[2] - self.ball_size, (work[1] + work[3] - self.ball_size) // 2)
        self.ball_position = ball_position(*position, self.ball_size, work, self.enabled.get())
        self._ball_side = ball_side(self.ball_position, self.ball_size, work) if self.enabled.get() else None
        exposure = 0.5 if self._ball_side else 1.0
        self._set_ball_exposure(exposure)
        self.tab.deiconify()
        self.tab.update_idletasks()
        self._set_ball_exposure(exposure)
        self.tab.lift()

    def _ball_press(self, _event=None):
        self.motion.cancel("ball")
        self.motion.cancel("reveal")
        self._cancel_ball_tuck()
        if self._ball_side:
            self._set_ball_exposure(1.0)
        self._ball_side = None
        point, rect = wintypes.POINT(), wintypes.RECT()
        self.user.GetCursorPos(ctypes.byref(point))
        self.user.GetWindowRect(self._ball_hwnd(), ctypes.byref(rect))
        self._ball_drag_origin = point.x, point.y, rect.left, rect.top
        self._ball_dragged = False

    def _ball_motion(self, _event=None):
        if self._ball_drag_origin is None:
            return
        point = wintypes.POINT()
        self.user.GetCursorPos(ctypes.byref(point))
        px, py, left, top = self._ball_drag_origin
        dx, dy = point.x - px, point.y - py
        if max(abs(dx), abs(dy)) >= 4:
            self._ball_dragged = True
        if self._ball_dragged:
            self._move_ball(left + dx, top + dy)

    def _ball_release(self, _event=None):
        if self._ball_drag_origin is None:
            return
        self._ball_drag_origin = None
        if not self._ball_dragged:
            self.show()
            return
        rect = wintypes.RECT()
        self.user.GetWindowRect(self._ball_hwnd(), ctypes.byref(rect))
        work = self.work_area(self._ball_hwnd())
        self.ball_position = ball_position(rect.left, rect.top, self.ball_size, work, self.enabled.get())
        self.motion.start("ball", (rect.left, rect.top), self.ball_position,
                          lambda point: self._move_ball(*point), lambda: self._dock_ball(work))

    def _hide_clipboard(self):
        panel = self.app.clipboard_panel
        if panel is not None and panel.winfo_exists() and panel.state() != "withdrawn":
            if not panel.hide():
                return False
            self._clipboard_visible = True
        return True

    def _restore_clipboard(self):
        if self._clipboard_visible:
            self._clipboard_visible = False
            self.app.open_clipboard()

    def hide(self):
        if not self.available:
            self.root.iconify()
            return
        self.motion.cancel("main")
        self.expand(restore_clipboard=False, animate=False)
        self.side = None
        self._outside_since = None
        self.root.withdraw()
        self.root.attributes("-alpha", 1.0)
        self._show_ball()

    def _main_unmapped(self, event):
        if event.widget is not self.root or self.motion.closed or self._unmap_job is not None:
            return
        self._unmap_job = self.root.after_idle(self._sync_hidden_ball)

    def _sync_hidden_ball(self):
        self._unmap_job = None
        if self.motion.closed:
            return
        state = self.root.state()
        if state == "iconic":
            self.motion.cancel("main")
            self.side = None
            self._outside_since = None
            self.root.attributes("-alpha", 1.0)
            self._show_ball()
        elif state == "withdrawn" and not self.collapsed and self.tab.state() == "withdrawn":
            self._show_ball()
        elif state in ("normal", "zoomed") and not self.collapsed:
            self.motion.cancel("ball")
            self.motion.cancel("reveal")
            self._cancel_ball_tuck()
            self.tab.withdraw()

    def show(self):
        self.motion.cancel("ball")
        self.motion.cancel("reveal")
        self._cancel_ball_tuck()
        if self.collapsed:
            self.expand(restore_clipboard=False)
        else:
            self.root.deiconify()
            if not self.motion.active("main"):
                self.root.attributes("-alpha", 1.0)
        if getattr(self, "tab", None) is not None:
            self.tab.withdraw()
        self.root.lift()
        self._grace_until = time.monotonic() + 1.5
        self._outside_since = None
        self._restore_clipboard()

    def show_clipboard(self):
        self.show()
        self.app.open_clipboard()

    def toggle_floating_ball(self):
        enabled = not self.ball_enabled
        try:
            self.app.workspace.set_floating_ball(enabled)
        except OSError as exc:
            messagebox.showerror("设置保存失败", str(exc), parent=self.root)
            return False
        self.ball_enabled = enabled
        self.menu.entryconfigure(self.ball_menu_index,
                                 label="关闭悬浮球" if enabled else "打开悬浮球")
        if not enabled:
            if self.collapsed:
                self.show()
            else:
                self.motion.cancel("ball")
                self.motion.cancel("reveal")
                self._cancel_ball_tuck()
                self._ball_drag_origin = None
                self.tab.withdraw()
        elif self.root.state() in ("iconic", "withdrawn") and not self.collapsed:
            self._show_ball()
        self.app.update_floating_ball_button(enabled)
        return True

    def toggle_docking(self):
        try:
            self.app.workspace.set_edge_docking(self.enabled.get())
        except OSError as exc:
            self.enabled.set(self.app.workspace.settings["edge_docking"])
            messagebox.showerror("设置保存失败", str(exc), parent=self.root)
        if not self.enabled.get():
            self.motion.cancel("ball")
            self.motion.cancel("reveal")
            self._cancel_ball_tuck()
            if getattr(self, "tab", None) is not None and self.tab.state() == "normal":
                if self._ball_side:
                    self._set_ball_exposure(1.0)
                else:
                    rect = wintypes.RECT()
                    self.user.GetWindowRect(self._ball_hwnd(), ctypes.byref(rect))
                    self.ball_position = ball_position(rect.left, rect.top, self.ball_size,
                                                       self.work_area(self._ball_hwnd()), False)
                    self._move_ball(*self.ball_position)
                self._ball_side = None
            if not self.collapsed and self.motion.active("main"):
                self.motion.cancel("main")
                self._main_frame((*self.expanded_rect, 1.0))
            self.expand()
            self.side = None
            self._outside_since = None

    def _configured(self, event):
        if (event.widget is self.root and not self.motion.active("main") and not self.collapsed
                and self.enabled.get() and self.user.GetAsyncKeyState(1) & 0x8000):
            if self._drag_job is not None:
                self.root.after_cancel(self._drag_job)
            self.side = None
            self._outside_since = None
            self._drag_job = self.root.after(180, self._finish_drag)

    def _finish_drag(self):
        self._drag_job = None
        if self.user.GetAsyncKeyState(1) & 0x8000:
            self._drag_job = self.root.after(180, self._finish_drag)
            return
        if not self.enabled.get() or self.root.state() != "normal":
            return
        current = self.rect()
        result = edge_position(current, self.work_area())
        if result:
            self.side, self.expanded_rect = result
            self.motion.start("main", current, self.expanded_rect, self.move, self._resting, duration=0.18)

    def collapse(self):
        if self.collapsed or not self.side or not getattr(self, "ball_enabled", True):
            return
        left, top, right, bottom = self.expanded_rect
        work = self.work_area()
        wl, wt, wr, _ = work
        if self.side == "top":
            ball = ((left + right - self.ball_size) // 2, wt)
        else:
            ball = (wl if self.side == "left" else wr - self.ball_size, top + 70)
        self.collapsed = True
        self._outside_since = None

        def finish():
            self.root.withdraw()
            self.move(self.expanded_rect)
            self.root.attributes("-alpha", 1.0)
            self._show_ball(ball, work=work)

        self.motion.start("main", (*self.rect(), 1.0), (*self._folded_rect(), 0.0),
                          self._main_frame, finish, duration=0.16)

    def expand(self, restore_clipboard=True, animate=True):
        if self.collapsed:
            self.motion.cancel("main")
            self.collapsed = False
            if getattr(self, "tab", None) is not None:
                self.tab.withdraw()
            if animate:
                if self.root.state() == "withdrawn":
                    self._main_frame((*self._folded_rect(), 0.0))
                start = (*self.rect(), float(self.root.attributes("-alpha")))
                self.root.deiconify()
                self.motion.start("main", start, (*self.expanded_rect, 1.0),
                                  self._main_frame, self._resting, duration=0.18)
            else:
                self._main_frame((*self.expanded_rect, 1.0))
            self._resting()
            if restore_clipboard:
                self._restore_clipboard()

    def _poll(self):
        self._poll_job = None
        try:
            self.tray.process_events()
            if self.motion.closed:
                return
            if (self.side and self.enabled.get() and getattr(self, "ball_enabled", True)
                    and self.root.state() == "normal"
                    and not self.collapsed and not self.motion.active("main")
                    and self.root.grab_current() is None and not self.user.GetAsyncKeyState(1) & 0x8000):
                point = wintypes.POINT()
                self.user.GetCursorPos(ctypes.byref(point))
                left, top, right, bottom = self.rect()
                inside = left - 8 <= point.x < right + 8 and top - 8 <= point.y < bottom + 8
                panel = self.app.clipboard_panel
                if panel is not None and panel.winfo_exists() and panel.state() != "withdrawn":
                    panel_hwnd = self.user.GetParent(panel.winfo_id()) or panel.winfo_id()
                    panel_rect = wintypes.RECT()
                    if self.user.GetWindowRect(panel_hwnd, ctypes.byref(panel_rect)):
                        inside = inside or (panel_rect.left <= point.x < panel_rect.right
                                            and panel_rect.top <= point.y < panel_rect.bottom)
                now = time.monotonic()
                if inside or now < self._grace_until:
                    self._outside_since = None
                elif self._outside_since is None:
                    self._outside_since = now
                elif now - self._outside_since >= 0.6:
                    self.collapse()
            else:
                self._outside_since = None
        finally:
            if not self.motion.closed:
                self._poll_job = self.root.after(180, self._poll)

    def close(self):
        self._cancel_ball_tuck()
        self.motion.close()
        self.expand(restore_clipboard=False, animate=False)
        for job in (self._poll_job, self._drag_job, self._unmap_job):
            if job is not None:
                self.root.after_cancel(job)
        self.tray.close()
        if getattr(self, "tab", None) is not None:
            self.tab.destroy()
