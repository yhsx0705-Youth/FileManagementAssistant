"""Explorer drops add path links; outgoing file drags offer COPY only."""

from pathlib import Path
import sys
import tkinter as tk

from .storage import EntryError


class NativeDragDrop:
    def __init__(self, root, on_drop):
        try:
            from tkinterdnd2 import TkinterDnD
        except ImportError:
            vendor = str(Path(__file__).resolve().parent.parent / ".vendor")
            if vendor not in sys.path:
                sys.path.insert(0, vendor)
            from tkinterdnd2 import TkinterDnD
        try:
            TkinterDnD._require(root)
        except RuntimeError as exc:
            raise EntryError("原生拖拽组件加载失败，请重新打包程序。") from exc
        self.root = root
        self.on_drop = on_drop
        self.exporting = False
        self._accept_command = root.register(self.accept)
        self._drop_command = root.register(self.drop)
        def register(widget):
            if not isinstance(widget, tk.Menu):
                root.tk.call("tkdnd::drop_target", "register", widget._w, ("DND_Files",))
                root.tk.call("bind", widget._w, "<<DropEnter>>", self._accept_command)
                root.tk.call("bind", widget._w, "<<DropPosition>>", self._accept_command)
                root.tk.call("bind", widget._w, "<<Drop>>", self._drop_command + " %D")
                for child in widget.winfo_children():
                    register(child)
        register(root)

    def accept(self):
        return "refuse_drop" if self.exporting else "link"

    def drop(self, data):
        if self.exporting:
            return "refuse_drop"
        try:
            paths = self.root.tk.splitlist(data)
        except tk.TclError:
            return "refuse_drop"
        return "link" if paths and self.on_drop(paths) else "refuse_drop"

    def export(self, widget, path):
        if Path(path).is_dir():
            return "refuse_drop"
        self.exporting = True
        try:
            return self.root.tk.call("_DoDragDrop", widget._w, ("copy",),
                                     ("CF_HDROP",), ((path,),), 1)
        finally:
            self.exporting = False
