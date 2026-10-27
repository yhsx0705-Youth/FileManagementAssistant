"""Shared application artwork, available in source and PyInstaller builds."""

import sys
import tkinter as tk
from pathlib import Path


def asset_path(name):
    return Path(__file__).resolve().parent / "assets" / name


def apply_app_icon(window):
    try:
        if sys.platform == "win32":
            window.iconbitmap(default=str(asset_path("app.ico")))
        else:
            window._app_icon = tk.PhotoImage(master=window, file=str(asset_path("app-256.png")))
            window.iconphoto(True, window._app_icon)
    except (tk.TclError, OSError):
        # Missing optional artwork must not prevent access to saved files.
        pass
