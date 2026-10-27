"""Three explicit confirmations, with a visible warning at every step."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .deletion import DeleteTarget


class DeleteDialog(tk.Toplevel):
    def __init__(self, parent, target: DeleteTarget, title: str):
        super().__init__(parent)
        self.title("警告：删除原文件")
        self.configure(bg="white")
        self.transient(parent)
        self.resizable(False, False)
        self.confirmed = False
        self.stage = 1
        self.target = target

        body = tk.Frame(self, bg="white", padx=24, pady=20)
        body.pack(fill="both", expand=True)
        self.heading = tk.Label(body, text=f"第一次确认（1/3）：删除真实{target.kind_label}",
                                 fg="#B42318", bg="white", font=("Microsoft YaHei UI", 15, "bold"),
                                 anchor="w", justify="left", wraplength=560)
        self.heading.pack(fill="x", pady=(0, 14))
        warning = ("此文件夹及其全部内容都会从原位置移除。" if target.is_directory and not target.is_link
                   else "将删除链接本身，不删除链接指向的内容。" if target.is_link
                   else "此文件会从原位置移除。")
        tk.Label(body, text=warning, bg="#FFF0EE", fg="#A51E16", padx=12, pady=12,
                 anchor="w", justify="left", wraplength=530,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", pady=(0, 14))
        tk.Label(body, text=f"入口：{title}\n目标：{Path(target.path).name}", bg="white", fg="#252525",
                 anchor="w", justify="left", wraplength=560).pack(fill="x", pady=(0, 8))
        tk.Label(body, text="完整路径（请仔细核对）：", bg="white", fg="#252525", anchor="w").pack(fill="x")
        path_box = tk.Text(body, height=3, width=57, wrap="char", bg="#F4F4F4", fg="#252525",
                           relief="solid", bd=1, padx=8, pady=8, font=("Microsoft YaHei UI", 10))
        path_box.insert("1.0", target.path)
        path_box.configure(state="disabled")
        path_box.pack(fill="x", pady=(6, 12))
        self.explanation = tk.Label(body, bg="white", fg="#555555", anchor="w", justify="left", wraplength=560,
                                     text="确认后尝试移入 Windows 回收站；无法回收时会停止。\n仅想从列表去掉，请取消并使用「移除入口」。")
        self.explanation.pack(fill="x")

        footer = tk.Frame(self, bg="#F4F4F4", padx=24, pady=14)
        footer.pack(fill="x")
        self.confirm_button = tk.Button(footer, text="继续（第一次确认）", command=self.advance,
                                        bg="#B42318", fg="white", activebackground="#8C1B13",
                                        activeforeground="white", bd=0, padx=14, pady=8, cursor="hand2")
        self.confirm_button.pack(side="right")
        self.cancel_button = ttk.Button(footer, text="取消", command=self.cancel)
        self.cancel_button.pack(side="right", padx=(0, 14))
        self.bind("<Escape>", lambda _: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._resize(parent)
        self.grab_set()
        self.cancel_button.focus_set()

    def _resize(self, parent):
        self.update_idletasks()
        width, height = 620, self.winfo_reqheight()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def advance(self):
        if self.stage >= 3:
            return
        self.stage += 1
        if self.stage == 2:
            self.heading.configure(text=f"第二次确认（2/3）：确定删除此{self.target.kind_label}？")
            self.explanation.configure(text="请再次核对文件名和完整路径。\n继续后还需要最后一次确认，才会删除原文件。")
            self.confirm_button.configure(text="继续（第二次确认）", command=self.advance)
        else:
            self.heading.configure(text="第三次确认（3/3）：最后确认删除")
            self.explanation.configure(text="这是最后一次确认。点击「确认删除」后，原文件将移入回收站。\n点击「取消」可立即退出，原文件保持不变。")
            self.confirm_button.configure(text="确认删除（第三次确认）", command=self.confirm)
        self._resize(self.master)
        self.cancel_button.focus_set()

    def confirm(self):
        if self.stage == 3:
            self.confirmed = True
            self.destroy()

    def cancel(self):
        self.confirmed = False
        self.destroy()


def confirm_source_deletion(parent, target: DeleteTarget, title: str) -> bool:
    dialog = DeleteDialog(parent, target, title)
    parent.wait_window(dialog)
    return dialog.confirmed
