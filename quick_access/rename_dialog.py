"""A compact editor for an actual file or folder name."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .storage import EntryError


class RenameDialog(tk.Toplevel):
    def __init__(self, parent, target, on_rename):
        super().__init__(parent)
        self.title("重命名原文件")
        self.transient(parent)
        self.resizable(False, False)
        self.on_rename = on_rename
        self.result = None
        source = Path(target.path)
        self.suffix = "" if target.is_directory or os.path.isdir(source) else source.suffix
        self.name_var = tk.StringVar(value=source.name[:-len(self.suffix)] if self.suffix else source.name)
        self.error_var = tk.StringVar()

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="修改磁盘上的实际名称，并同步更新入口路径。", wraplength=510).pack(anchor="w")
        ttk.Label(body, text=f"原路径：{source}", wraplength=510, foreground="#555555").pack(
            fill="x", pady=(10, 16))
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text="新名称：").pack(side="left")
        self.name_input = ttk.Entry(row, textvariable=self.name_var, width=38)
        self.name_input.pack(side="left", fill="x", expand=True, padx=(4, 0), ipady=3)
        if self.suffix:
            ttk.Label(row, text=self.suffix).pack(side="left", padx=(6, 0))
            ttk.Label(body, text=f"扩展名 {self.suffix} 会保留，只需输入前面的名称。",
                      foreground="#555555", wraplength=510).pack(anchor="w", pady=(8, 0))
        ttk.Label(body, textvariable=self.error_var, foreground="#B42318", wraplength=510).pack(
            fill="x", pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        self.save_button = ttk.Button(buttons, text="保存", command=self.submit)
        self.save_button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel)
        self.cancel_button.pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _: self.submit())
        self.bind("<Escape>", lambda _: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._resize()
        self.grab_set()
        self.name_input.focus_set()
        self.name_input.selection_range(0, "end")

    def _resize(self):
        self.update_idletasks()
        width, height = 550, self.winfo_reqheight()
        x = max(0, self.master.winfo_rootx() + (self.master.winfo_width() - width) // 2)
        y = max(0, self.master.winfo_rooty() + (self.master.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def submit(self):
        if not self.name_var.get().strip():
            self.error_var.set("请输入新的名称。")
            self._resize()
            return
        try:
            self.result = self.on_rename(self.name_var.get() + self.suffix)
        except (EntryError, OSError) as exc:
            self.error_var.set(str(exc) if isinstance(exc, EntryError) else f"重命名失败，请检查文件是否被占用或有写入权限。\n{exc}")
            self._resize()
            self.name_input.focus_set()
            return
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()
