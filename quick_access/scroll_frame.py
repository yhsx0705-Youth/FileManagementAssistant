"""A compact scrolling container for cards and preset rows."""

import tkinter as tk
from tkinter import ttk


class ScrollFrame(ttk.Frame):
    def __init__(self, master, *, prominent=False):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#F4F5F7")
        if prominent:
            self.scrollbar = tk.Scrollbar(
                self, orient="vertical", command=self.canvas.yview, width=16,
                relief="flat", bd=0, elementborderwidth=1,
                background="#AEBBC7", activebackground="#8294A5",
                troughcolor="#E0E6EC", highlightthickness=0,
            )
        else:
            self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y", padx=(3, 2), pady=4)
        self.content = ttk.Frame(self.canvas, padding=6)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.item, width=event.width))
        # Toplevel bindtag includes child widgets; never install a global wheel binding.
        self._toplevel = self.winfo_toplevel()
        self._wheel_binding = self._toplevel.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _wheel(self, event):
        if event.widget is not self and not str(event.widget).startswith(str(self) + "."):
            return
        if isinstance(event.widget, tk.Text):
            return
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _destroyed(self, event):
        if event.widget is self:
            self._toplevel.unbind("<MouseWheel>", self._wheel_binding)
