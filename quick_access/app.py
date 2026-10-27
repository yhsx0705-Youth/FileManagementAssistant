"""A simple, single-window file shortcut manager."""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import uuid
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .icons import IconCache
from .branding import apply_app_icon
from .delete_dialog import confirm_source_deletion
from .rename_dialog import RenameDialog
from .copying import copy_files
from .file_clipboard import read_files, write_files, write_text
from .storage import ENTRY_KINDS, EntryError, EntryStore, path_key
from .targets import normalize_target
from .dragdrop import NativeDragDrop
from .workspace import WorkspaceStore
from .clipboard_panel import ClipboardPanel
from .launch_dialog import ShortcutLaunch

PANEL_TITLES = {"folder": "文件夹", "file": "文件", "shortcut": "快捷方式", "web": "网页链接"}


@dataclass
class DragState:
    tree: ttk.Treeview
    kind: str
    entry_id: str
    original_ids: tuple[str, ...]
    start_x: int
    start_y: int
    body_top: int
    active: bool = False
    x: int = 0
    y: int = 0
    scroll_direction: int = 0
    scroll_job: str | None = None
    target_index: int | None = None
    ghost: tk.Label | None = None
    indicator: tk.Frame | None = None
    source_tags: tuple[str, ...] = ()


class App:
    def __init__(self, root: tk.Tk, store: EntryStore):
        self.root, self.store = root, store
        self.workspace = WorkspaceStore(store.directory)
        self.preset_warning = ""
        try:
            self.workspace.upgrade_presets(store)
        except OSError:
            self.preset_warning = "预设启动信息保存失败，请检查数据目录写入权限。"
        self.clipboard_panel = None
        self.desktop = None
        self.editing_id: str | None = None
        self.form_mode = "path"
        self._copy_executor = None
        self._copy_future = None
        self._copy_poll_job = None
        self.path_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.show_paths_var = tk.BooleanVar(value=False)
        self.icons = IconCache(root)
        self.trees: dict[str, ttk.Treeview] = {}
        self.panels: dict[str, tk.Frame] = {}
        self.panel_headers: dict[str, tk.Label] = {}
        self.panel_heading_frames: dict[str, tk.Frame] = {}
        self._panel_settings_dialog = None
        self._panel_settings_vars = None
        self._panel_drag_kind = None
        self._panel_drag_start = None
        self._panel_drag_active = False
        self._panel_drag_target = None
        self._panel_drag_ghost = None
        self.horizontal_scrollbars = []
        self._drag_state: DragState | None = None
        self._last_drag_finished = 0.0
        self.native_dnd = None
        root.title("文件管理辅助器")
        apply_app_icon(root)
        root.geometry("1000x720")
        root.minsize(720, 520)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.list_font = tkfont.Font(root=root, family="Microsoft YaHei UI", size=10)
        style.configure("Treeview", rowheight=28, font=self.list_font)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10))
        style.map(
            "Shortcuts.Treeview",
            background=[("selected", "!focus", "SystemWindow"),
                        ("selected", "focus", "SystemHighlight")],
            foreground=[("selected", "!focus", "SystemWindowText"),
                        ("selected", "focus", "SystemHighlightText")],
        )

        body = ttk.Frame(root, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(body)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.add_button = ttk.Button(toolbar, text="添加", command=self.begin_add)
        self.add_button.pack(side="left")
        self.developer_label = ttk.Label(toolbar, text="开发者：云鹤sx",
                                         foreground="#888888",
                                         font=("Microsoft YaHei UI", 9))
        self.developer_label.pack(side="right", padx=(8, 2))
        self.form_actions = ttk.Frame(toolbar)
        self.form_frame = ttk.Frame(body)
        self.form_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.form_frame.columnconfigure(1, weight=1)
        self.path_label = ttk.Label(self.form_frame, text="路径或网址：")
        self.path_label.grid(row=0, column=0, sticky="w")
        self.path_input = ttk.Entry(self.form_frame, textvariable=self.path_var, exportselection=False)
        self.path_input.grid(row=0, column=1, sticky="ew", ipady=3)
        self.path_menu = tk.Menu(root, tearoff=False)
        self.path_menu.add_command(label="剪切", command=lambda: self.path_input.event_generate("<<Cut>>"))
        self.path_menu.add_command(label="复制", command=lambda: self.path_input.event_generate("<<Copy>>"))
        self.path_menu.add_command(label="粘贴", command=self.paste_path)
        self.path_menu.add_separator()
        self.path_menu.add_command(label="全选", command=self.select_path)
        self.choose_file_button = ttk.Button(self.form_actions, text="选择文件", command=self.choose_file)
        self.choose_file_button.pack(side="left", padx=(0, 8))
        self.choose_folder_button = ttk.Button(self.form_actions, text="选择文件夹", command=self.choose_folder)
        self.choose_folder_button.pack(side="left", padx=(0, 8))
        self.name_label = ttk.Label(self.form_frame, text="显示名称：")
        self.name_label.grid(row=1, column=0, sticky="w")
        self.name_input = ttk.Entry(self.form_frame, textvariable=self.name_var)
        self.name_input.grid(row=1, column=1, sticky="ew", ipady=3)
        self.save_button = ttk.Button(self.form_actions, text="保存", command=self.submit)
        self.save_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(self.form_actions, text="取消", command=self.clear_form)
        self.cancel_button.pack(side="left")
        self.form_frame.grid_remove()

        table = self.table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky="nsew")
        for row in range(2):
            table.rowconfigure(row, weight=1, uniform="panels")
        for column in range(2):
            table.columnconfigure(column, weight=1, uniform="panels")
        for kind in ENTRY_KINDS:
            panel = tk.Frame(table, bg="white", highlightthickness=1,
                             highlightbackground="#D2D9E0", highlightcolor="#2676B8")
            self.panels[kind] = panel
            panel.rowconfigure(1, weight=1)
            panel.columnconfigure(0, weight=1)
            heading = tk.Frame(panel, bg="#EEF2F5", highlightthickness=1,
                               highlightbackground="#D2D9E0")
            heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            heading.columnconfigure(0, weight=1)
            self.panel_heading_frames[kind] = heading
            header = tk.Label(heading, text=PANEL_TITLES[kind], anchor="w", cursor="fleur",
                              bg="#EEF2F5", fg="#253343", padx=10, pady=7,
                              highlightthickness=0)
            header.grid(row=0, column=0, sticky="ew")
            header.bind("<ButtonPress-1>", lambda event, k=kind: self._panel_drag_start_event(k, event))
            self.panel_headers[kind] = header
            tree = ttk.Treeview(panel, columns=("path",), displaycolumns=(), show="tree headings",
                                selectmode="browse", style="Shortcuts.Treeview")
            tree.heading("#0", text="名称", anchor="w")
            tree.heading("path", text="路径", anchor="w")
            tree.column("#0", width=380, minwidth=140, stretch=True)
            tree.column("path", width=250, minwidth=150, stretch=False)
            tree.grid(row=1, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
            vertical.grid(row=1, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(panel, orient="horizontal", command=tree.xview)
            horizontal.grid(row=2, column=0, sticky="ew")
            horizontal.grid_remove()
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.tag_configure("drag-source", foreground="#8A8A8A", background="#F3F5F8")
            self.trees[kind] = tree
            self.horizontal_scrollbars.append(horizontal)
            tree.bind("<Button-1>", self._tree_clicked)
            tree.bind("<B1-Motion>", self._drag_motion)
            tree.bind("<ButtonRelease-1>", self._drag_release)
            tree.bind("<Button-3>", self._show_entry_menu)
            tree.bind("<Double-1>", self._double_click)
            tree.bind("<FocusIn>", lambda event: self._activate_tree(event.widget))
            tree.bind("<Return>", lambda _: self.open_selected())
            tree.bind("<Delete>", self.delete_selected)
            tree.bind("<F2>", lambda _: self.edit_name_selected())
            tree.bind("<Control-c>", self.copy_selected)
            tree.bind("<Control-C>", self.copy_selected)
            tree.bind("<Control-v>", self.paste_files_selected)
            tree.bind("<Control-V>", self.paste_files_selected)
        self.entry_menu = tk.Menu(root, tearoff=False)
        self.shortcut_launch = ShortcutLaunch(self)
        self._configure_entry_menu("file")
        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.panel_settings_button = ttk.Button(footer, command=self.open_panel_settings,
                                                style="PanelSettings.TButton")
        self.panel_settings_button.pack(side="left")
        style.configure("PanelSettings.TButton", padding=(12, 7), font=("Microsoft YaHei UI", 10, "bold"))
        self._update_panel_settings_button()
        ttk.Label(footer, text="拖入添加；文件可拖出复制，文件夹请用 Ctrl+C").pack(side="right")
        ttk.Checkbutton(footer, text="显示路径", variable=self.show_paths_var,
                        command=self.toggle_paths).pack(side="right", padx=(0, 16))
        self.floating_ball_button = ttk.Button(
            footer, text="关闭悬浮球" if self.workspace.settings["floating_ball"] else "打开悬浮球",
            command=self.toggle_floating_ball, state="disabled")
        self.floating_ball_button.pack(side="right", padx=(0, 12))
        status_bar = ttk.Frame(body)
        status_bar.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_bar, textvariable=self.status_var, wraplength=730)
        self.status_label.grid(row=0, column=0, sticky="w")
        self.clipboard_button = ttk.Button(status_bar, text="打开剪贴板", command=self.toggle_clipboard)
        self.clipboard_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.path_input.bind("<Return>", lambda _: self.submit())
        self.path_input.bind("<Button-3>", self._show_path_menu)
        self.path_input.bind("<Control-v>", lambda _: self.paste_path())
        self.path_input.bind("<Control-V>", lambda _: self.paste_path())
        self.path_input.bind("<Shift-Insert>", lambda _: self.paste_path())
        self.path_input.bind("<FocusIn>", self.clear_selection)
        self.name_input.bind("<FocusIn>", self.clear_selection)
        self.name_input.bind("<Return>", lambda _: self.submit())
        root.bind("<Button-1>", self._background_clicked, add="+")
        root.bind_class(str(root), "<B1-Motion>", self._panel_drag_motion, add="+")
        root.bind_class(str(root), "<ButtonRelease-1>", self._panel_drag_release_event, add="+")
        root.bind("<Escape>", lambda _: self.clear_form())
        root.bind("<Control-n>", lambda _: self.begin_add())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.apply_panel_layout()
        self.refresh()
        next((self.trees[kind] for kind in self.store.panel_order
              if self.store.panel_visibility[kind]), self.trees["folder"]).focus_set()

        self.native_dnd = NativeDragDrop(root, self.add_dropped_paths)
        if store.shortcut_warnings:
            self.status_var.set("部分旧快捷方式尚未保存副本，可右键「重新导入快捷方式…」。")
        elif self.preset_warning:
            self.status_var.set(self.preset_warning)

    def add_dropped_paths(self, paths):
        self.cancel_drag()
        known = {path_key(path) for entry in self.store.entries
                 for path in (entry.path, self.store.effective_path(entry))}
        added, skipped, errors = 0, 0, []
        for value in paths:
            try:
                path = normalize_target(value)
                if path_key(path) in known:
                    skipped += 1
                    continue
                self.store.save_entry(path)
                known.add(path_key(path))
                added += 1
            except (EntryError, OSError) as exc:
                errors.append(f"{value}：{exc}")
        self.clear_selection()
        self.refresh()
        summary = f"已添加 {added} 个入口，保存原路径。"
        if skipped:
            summary += f" 已跳过 {skipped} 个已有入口。"
        if errors:
            summary += f" {len(errors)} 个未添加：{errors[0]}"
        self.status_var.set(summary)
        return bool(added or skipped)

    def show_form(self, mode="path"):
        self.form_mode = mode
        for widget in (self.path_label, self.path_input):
            widget.grid() if mode == "path" else widget.grid_remove()
        for widget in (self.name_label, self.name_input):
            widget.grid() if mode == "alias" else widget.grid_remove()
        for button in (self.choose_file_button, self.choose_folder_button):
            if mode == "alias":
                button.pack_forget()
            else:
                button.pack(side="left", before=self.save_button, padx=(0, 8))
        self.form_actions.pack(side="left", padx=(8, 0))
        self.form_frame.grid()
        self.add_button.state(["disabled"])
        self.root.update_idletasks()

    def begin_add(self):
        if self.form_frame.winfo_manager():
            self.path_input.focus_set()
            return
        self.clear_form()
        self.show_form()
        self.status_var.set("选择或粘贴文件路径、快捷方式路径或网页地址，然后点击「保存」。")
        self.path_input.focus_set()

    def paste_path(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.status_var.set("剪贴板中没有可粘贴的文本。")
            return "break"
        if not text:
            self.status_var.set("剪贴板中没有可粘贴的文本。")
            return "break"
        self.path_input.focus_set()
        self.clear_selection()
        if self.path_input.selection_present():
            self.path_input.delete("sel.first", "sel.last")
        self.path_input.insert("insert", text)
        self.status_var.set("路径已粘贴，点击「保存修改」保存。" if self.editing_id
                            else "路径已粘贴，点击「保存」完成添加。")
        return "break"

    def select_path(self):
        self.path_input.focus_set()
        self.path_input.selection_range(0, "end")
        self.path_input.icursor("end")

    def _show_path_menu(self, event):
        self.path_input.focus_set()
        state = "normal" if self.path_input.selection_present() else "disabled"
        self.path_menu.entryconfigure(0, state=state)
        self.path_menu.entryconfigure(1, state=state)
        try:
            self.path_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.path_menu.grab_release()
        return "break"

    def clear_selection(self, _event=None, except_tree=None):
        for tree in self.trees.values():
            if tree is except_tree:
                continue
            selection = tree.selection()
            if selection:
                tree.selection_remove(*selection)
            tree.focus("")

    def _activate_tree(self, tree):
        self.clear_selection(except_tree=tree)

    def toggle_paths(self):
        show = self.show_paths_var.get()
        for tree in self.trees.values():
            tree.configure(displaycolumns=("path",) if show else ())
            tree.column("#0", width=190 if show else 380)
        for scrollbar in self.horizontal_scrollbars:
            scrollbar.grid() if show else scrollbar.grid_remove()

    def _update_panel_settings_button(self):
        count = sum(self.store.panel_visibility.values())
        self.panel_settings_button.configure(text=f"显示区域 ({count}/4)")

    def open_panel_settings(self):
        if self._panel_settings_dialog is not None and self._panel_settings_dialog.winfo_exists():
            self._panel_settings_dialog.lift()
            return self._panel_settings_dialog

        dialog = tk.Toplevel(self.root)
        self._panel_settings_dialog = dialog
        dialog.title("显示区域")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        variables = {kind: tk.BooleanVar(value=self.store.panel_visibility[kind])
                     for kind in ENTRY_KINDS}
        self._panel_settings_vars = variables

        body = ttk.Frame(dialog, padding=(18, 16))
        body.pack(fill="both", expand=True)
        for row, kind in enumerate(ENTRY_KINDS):
            ttk.Checkbutton(body, text=PANEL_TITLES[kind], variable=variables[kind]).grid(
                row=row, column=0, sticky="w", pady=3)
        self._panel_settings_error = tk.StringVar()
        ttk.Label(body, textvariable=self._panel_settings_error, foreground="#B42318").grid(
            row=len(ENTRY_KINDS), column=0, sticky="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=len(ENTRY_KINDS) + 1, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self._close_panel_settings).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save_panel_settings).pack(
            side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", self._close_panel_settings)
        dialog.bind("<Escape>", lambda _: self._close_panel_settings())
        dialog.bind("<Return>", lambda _: self._save_panel_settings())
        dialog.bind("<Destroy>", self._panel_settings_destroyed, add="+")
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        return dialog

    def _close_panel_settings(self):
        dialog, self._panel_settings_dialog = self._panel_settings_dialog, None
        self._panel_settings_vars = None
        if dialog is not None and dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()

    def _panel_settings_destroyed(self, event):
        if event.widget is self._panel_settings_dialog:
            self._panel_settings_dialog = None
            self._panel_settings_vars = None

    def _save_panel_settings(self):
        visibility = {kind: variable.get() for kind, variable in self._panel_settings_vars.items()}
        try:
            self.store.save_panel_visibility(visibility)
        except EntryError as exc:
            self._panel_settings_error.set(str(exc))
            return
        except OSError as exc:
            self._panel_settings_error.set(self._error_text(exc))
            return
        self._close_panel_settings()
        self.clear_selection()
        self.apply_panel_layout()
        self._update_panel_settings_button()
        self.status_var.set("显示区域已更新。")

    def apply_panel_layout(self):
        visible = [kind for kind in self.store.panel_order
                   if self.store.panel_visibility.get(kind, True)]
        count = len(visible)
        if count == 1:
            slots = [(0, 0, 2, 2)]
        elif count == 2:
            slots = [(0, 0, 2, 1), (0, 1, 2, 1)]
        elif count == 3:
            slots = [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 2)]
        else:
            slots = [(index // 2, index % 2, 1, 1) for index in range(count)]
        rows = {row for row, _, _, _ in slots} | {
            row + rowspan - 1 for row, _, rowspan, _ in slots
        }
        columns = {column for _, column, _, _ in slots} | {
            column + colspan - 1 for _, column, _, colspan in slots
        }
        for row in range(2):
            self.table.rowconfigure(row, weight=1 if row in rows else 0)
        for column in range(2):
            self.table.columnconfigure(column, weight=1 if column in columns else 0)
        for panel in self.panels.values():
            panel.grid_remove()
        for kind, (row, column, rowspan, colspan) in zip(visible, slots):
            self.panels[kind].grid(row=row, column=column, sticky="nsew",
                                   padx=(0, 8) if column == 0 else (0, 0),
                                   pady=(0, 8) if row == 0 else (0, 0),
                                   rowspan=rowspan, columnspan=colspan)

    def _panel_drag_start_event(self, kind, event):
        self._panel_drag_kind = kind
        self._panel_drag_start = (event.x_root, event.y_root)
        self._panel_drag_active = False

    def _panel_drag_release_event(self, event):
        if self._panel_drag_kind is not None:
            return self._panel_drag_release(self._panel_drag_kind, event)

    def _panel_drag_motion(self, event):
        if self._panel_drag_kind is None or self._panel_drag_start is None:
            return
        if not self._panel_drag_active:
            if max(abs(event.x_root - self._panel_drag_start[0]),
                   abs(event.y_root - self._panel_drag_start[1])) < 6:
                return
            self._panel_drag_active = True
            self.panel_headers[self._panel_drag_kind].configure(bg="#D8E1EA", fg="#64748B")
            self._panel_drag_ghost = tk.Label(
                self.root, text=f"移动区域：{PANEL_TITLES[self._panel_drag_kind]}",
                bg="#E6F1FC", fg="#174A75", padx=11, pady=6,
                highlightbackground="#2676B8", highlightthickness=2,
                relief="solid", bd=0, cursor="fleur")
        target = self._panel_at(event.x_root, event.y_root)
        self._set_panel_drag_target(target if target != self._panel_drag_kind else None)
        if self._panel_drag_ghost is not None:
            x = event.x_root - self.root.winfo_rootx() + 14
            y = event.y_root - self.root.winfo_rooty() + 14
            self._panel_drag_ghost.place(x=max(0, x), y=max(0, y))
            self._panel_drag_ghost.lift()
        self.status_var.set("将区域拖到另一区域的位置即可调整排列。")
        return "break"

    def _panel_at(self, x_root, y_root):
        return next((kind for kind, panel in self.panels.items()
                     if self.store.panel_visibility.get(kind, True)
                     and panel.winfo_rootx() <= x_root < panel.winfo_rootx() + panel.winfo_width()
                     and panel.winfo_rooty() <= y_root < panel.winfo_rooty() + panel.winfo_height()), None)

    def _set_panel_drag_target(self, target):
        if target == self._panel_drag_target:
            return
        if self._panel_drag_target is not None:
            old = self._panel_drag_target
            self.panels[old].configure(highlightbackground="#D2D9E0")
            self.panel_headers[old].configure(bg="#EEF2F5", fg="#253343")
        self._panel_drag_target = target
        if target is not None:
            self.panels[target].configure(highlightbackground="#2676B8")
            self.panel_headers[target].configure(bg="#2676B8", fg="white")

    def _reset_panel_drag_visuals(self, source):
        self._set_panel_drag_target(None)
        if self._panel_drag_ghost is not None:
            self._panel_drag_ghost.destroy()
            self._panel_drag_ghost = None
        if source is not None:
            self.panel_headers[source].configure(bg="#EEF2F5", fg="#253343", cursor="fleur")

    def _panel_drag_release(self, kind, event):
        source = self._panel_drag_kind
        active = self._panel_drag_active
        self._panel_drag_kind = None
        self._panel_drag_start = None
        self._panel_drag_active = False
        if source is None:
            return
        self.panel_headers[source].configure(cursor="fleur")
        if not active:
            self._reset_panel_drag_visuals(source)
            return
        target = self._panel_drag_target or self._panel_at(event.x_root, event.y_root)
        self._reset_panel_drag_visuals(source)
        if target is None or target == source:
            self.status_var.set("区域顺序未改变。")
            return "break"
        order = list(self.store.panel_order)
        first, second = order.index(source), order.index(target)
        order[first], order[second] = order[second], order[first]
        try:
            self.store.save_panel_order(order)
        except (EntryError, OSError) as exc:
            self.status_var.set("区域顺序保存失败。" + self._error_text(exc))
            return "break"
        self.apply_panel_layout()
        self.status_var.set("区域位置已保存。")
        return "break"

    def _tree_clicked(self, event):
        self.cancel_drag()
        tree = event.widget
        self._activate_tree(tree)
        row_id = tree.identify_row(event.y)
        if not row_id:
            self.clear_selection()
            if tree.identify_region(event.x, event.y) == "nothing":
                return "break"
        else:
            kind = next(kind for kind, candidate in self.trees.items() if candidate is tree)
            body_top = next((y for y in range(min(80, tree.winfo_height())) if tree.identify_row(y)), 28)
            self._drag_state = DragState(tree, kind, row_id, tree.get_children(),
                                         event.x, event.y, body_top)

    def _move_drag_preview(self, state, x, y):
        tree = state.tree
        if state.ghost is not None:
            ghost_x = tree.winfo_rootx() - self.root.winfo_rootx() + x + 14
            ghost_y = tree.winfo_rooty() - self.root.winfo_rooty() + y + 14
            state.ghost.place(x=max(0, min(ghost_x, self.root.winfo_width() - state.ghost.winfo_reqwidth() - 4)),
                              y=max(0, min(ghost_y, self.root.winfo_height() - state.ghost.winfo_reqheight() - 4)))
            state.ghost.lift()
        if not 0 <= x < tree.winfo_width():
            state.target_index = None
            if state.indicator is not None:
                state.indicator.place_forget()
            return
        y = max(state.body_top, min(y, tree.winfo_height() - 1))
        target = tree.identify_row(y)
        rows = [row for row in state.original_ids if row != state.entry_id]
        if target and target != state.entry_id:
            index = rows.index(target)
            box = tree.bbox(target)
            after = bool(box and y >= box[1] + box[3] / 2)
            if after:
                index += 1
            marker_y = box[1] + (box[3] if after else 0) if box else y
        elif target == state.entry_id:
            index = state.original_ids.index(state.entry_id)
            box = tree.bbox(state.entry_id)
            marker_y = box[1] if box else y
        else:
            index = len(rows)
            box = tree.bbox(state.original_ids[-1])
            marker_y = box[1] + box[3] if box else y
        state.target_index = index
        if state.indicator is not None:
            state.indicator.place(x=0, y=max(state.body_top, min(marker_y, tree.winfo_height() - 3)),
                                  relwidth=1, height=3)
            state.indicator.lift()

    def _start_drag_visuals(self, state):
        tree = state.tree
        state.source_tags = tuple(tree.item(state.entry_id, "tags"))
        tree.item(state.entry_id, tags=(*state.source_tags, "drag-source"))
        tree.selection_remove(state.entry_id)
        title = str(tree.item(state.entry_id, "text"))
        if len(title) > 32:
            title = title[:31] + "…"
        state.ghost = tk.Label(self.root, text=title, bg="#EAF2FF", fg="#255A91",
                               font=("Microsoft YaHei UI", 10), padx=9, pady=4,
                               highlightbackground="#6A9CD0", highlightthickness=1, bd=0)
        state.indicator = tk.Frame(tree, bg="#3688DC", height=3, bd=0)

    def _drag_motion(self, event):
        state = self._drag_state
        if state is None or event.widget is not state.tree:
            return
        if not state.active:
            if max(abs(event.x - state.start_x), abs(event.y - state.start_y)) < 5:
                return "break"
            state.active = True
            state.tree.configure(cursor="fleur")
            self._start_drag_visuals(state)
        root_x = state.tree.winfo_rootx() + event.x - self.root.winfo_rootx()
        root_y = state.tree.winfo_rooty() + event.y - self.root.winfo_rooty()
        if self.native_dnd is not None and not (0 <= root_x < self.root.winfo_width()
                                                and 0 <= root_y < self.root.winfo_height()):
            if state.kind != "file":
                self.cancel_drag()
                self.status_var.set("已取消排序，请在原列表内拖动并松开鼠标。")
                return "break"
            return self._drag_out(state)
        state.x, state.y = event.x, event.y
        self._move_drag_preview(state, event.x, event.y)
        inside = 0 <= event.x < state.tree.winfo_width()
        state.scroll_direction = (-1 if event.y < state.body_top + 18 else
                                  1 if event.y > state.tree.winfo_height() - 24 else 0) if inside else 0
        if state.scroll_direction and state.scroll_job is None:
            state.scroll_job = self.root.after(90, self._drag_scroll)
        elif not state.scroll_direction and state.scroll_job is not None:
            self.root.after_cancel(state.scroll_job)
            state.scroll_job = None
        self.status_var.set("拖动调整顺序，松开鼠标保存。" if inside else "请在原列表内松开鼠标完成排序。")
        return "break"

    def _drag_scroll(self):
        state = self._drag_state
        if state is None:
            return
        state.scroll_job = None
        if state.active and state.scroll_direction:
            state.tree.yview_scroll(state.scroll_direction, "units")
            self.root.update_idletasks()
            self._move_drag_preview(state, state.x, state.y)
            state.scroll_job = self.root.after(90, self._drag_scroll)

    def _drag_out(self, state):
        entry = self.store.get(state.entry_id)
        self.cancel_drag()
        if entry.kind != "file" or os.path.isdir(entry.path):
            self.status_var.set("只有文件支持拖出复制。文件夹可使用右键「复制文件夹」。")
            return "break"
        if not os.path.exists(entry.path):
            self.status_var.set("原路径目前不可用，无法拖出复制。")
            return "break"
        self.status_var.set("拖到桌面或文件夹后松开以复制，原路径保持不变。")
        try:
            action = self.native_dnd.export(state.tree, entry.path)
            self.status_var.set("已交给资源管理器复制，原文件和入口路径保持不变。" if action == "copy"
                                else "已取消拖出，原文件和入口保持不变。")
        except tk.TclError as exc:
            self.status_var.set(f"拖出未完成，原文件和入口保持不变。{exc}")
        self._last_drag_finished = time.monotonic()
        return "break"

    def _reset_drag(self, restore=False):
        state, self._drag_state = self._drag_state, None
        if state is None:
            return
        if state.scroll_job is not None:
            self.root.after_cancel(state.scroll_job)
        state.tree.configure(cursor="")
        if state.ghost is not None:
            state.ghost.destroy()
        if state.indicator is not None:
            state.indicator.destroy()
        if state.active:
            state.tree.item(state.entry_id, tags=state.source_tags)
            state.tree.selection_set(state.entry_id)
        if restore:
            state.tree.set_children("", *state.original_ids)
        if state.active:
            self._last_drag_finished = time.monotonic()

    def cancel_drag(self):
        self._reset_drag(restore=True)

    def _drag_release(self, event):
        state = self._drag_state
        if state is None or event.widget is not state.tree:
            return
        if not state.active:
            self._reset_drag()
            return
        valid = (0 <= event.x < state.tree.winfo_width()
                 and state.body_top <= event.y < state.tree.winfo_height())
        if not valid:
            self.cancel_drag()
            self.status_var.set("已取消排序。")
            return "break"
        self._move_drag_preview(state, event.x, event.y)
        ordered_ids = [row for row in state.original_ids if row != state.entry_id]
        ordered_ids.insert(state.target_index, state.entry_id)
        self._reset_drag()
        if ordered_ids == list(state.original_ids):
            self.status_var.set("顺序未改变。")
            return "break"
        try:
            self.store.reorder(state.kind, ordered_ids)
            state.tree.set_children("", *ordered_ids)
            self.status_var.set("顺序已保存。")
        except (EntryError, OSError) as exc:
            state.tree.set_children("", *state.original_ids)
            self.status_var.set("顺序保存失败，已恢复原顺序。" + self._error_text(exc))
        return "break"

    def close(self):
        if self.desktop is not None and self.desktop.available:
            self.desktop.hide()
        else:
            self.quit()

    def quit(self):
        if self._copy_future is not None:
            messagebox.showinfo("正在复制", "文件正在复制，请完成后再关闭。", parent=self.root)
            return
        if self.clipboard_panel is not None and self.clipboard_panel.winfo_exists():
            if not self.clipboard_panel.flush():
                self.clipboard_panel.show()
                return
        self.shortcut_launch.shutdown()
        self.cancel_drag()
        if self._copy_executor is not None:
            self._copy_executor.shutdown(wait=False)
        if self.desktop is not None:
            self.desktop.close()
        self.root.destroy()

    def open_clipboard(self):
        if self.clipboard_panel is None or not self.clipboard_panel.winfo_exists():
            self.clipboard_panel = ClipboardPanel(
                self.root, self.workspace, on_visibility_change=self._clipboard_visibility_changed)
        else:
            self.clipboard_panel.show()
        return self.clipboard_panel

    def toggle_clipboard(self):
        panel = self.clipboard_panel
        if panel is not None and panel.winfo_exists() and panel.state() != "withdrawn":
            if panel.hide():
                self.status_var.set("剪贴板已关闭。")
            return panel
        return self.open_clipboard()

    def _clipboard_visibility_changed(self, visible):
        self.clipboard_button.configure(text="关闭剪贴板" if visible else "打开剪贴板")

    def toggle_floating_ball(self):
        if self.desktop is not None and self.desktop.available:
            self.desktop.toggle_floating_ball()

    def update_floating_ball_button(self, enabled=None):
        if enabled is None:
            enabled = self.workspace.settings["floating_ball"]
        self.floating_ball_button.configure(
            text="关闭悬浮球" if enabled else "打开悬浮球",
            state="normal" if self.desktop is not None and self.desktop.available else "disabled")

    def open_collaborative_launch(self):
        self.shortcut_launch.begin()

    def _configure_entry_menu(self, kind):
        self.entry_menu.delete(0, "end")
        if kind == "web":
            self.entry_menu.add_command(label="修改显示名称…", command=self.edit_name_selected)
            self.entry_menu.add_command(label="复制网页链接", command=self.copy_selected)
            self.entry_menu.add_separator()
            self.entry_menu.add_command(label="移除入口", command=self.remove_selected)
        else:
            title = "修改显示名称…" if kind in ("folder", "shortcut") else "重命名原文件…"
            self.entry_menu.add_command(label=title, command=self.edit_name_selected)
            self.entry_menu.add_command(label="打开文件所在位置", command=self.open_parent_selected)
            self.entry_menu.add_separator()
        if kind == "folder":
            self.entry_menu.add_command(label="移除入口", command=self.remove_selected)
            self.entry_menu.add_separator()
            self.entry_menu.add_command(label="复制文件夹 (Ctrl+C)", command=self.copy_selected)
            self.entry_menu.add_command(label="粘贴 (Ctrl+V)", command=self.paste_files_selected)
        elif kind in ("file", "shortcut"):
            self.entry_menu.add_command(label="复制文件 (Ctrl+C)", command=self.copy_selected)
            self.entry_menu.add_separator()
            self.entry_menu.add_command(label="移除入口", command=self.remove_selected)
            if kind == "file":
                self.entry_menu.add_command(label="删除原文件…", command=self.delete_source_selected,
                                            foreground="#B42318")
        if kind == "shortcut":
            self.entry_menu.add_command(label="重新导入快捷方式…", command=self.repair_shortcut_selected)
            self.entry_menu.add_separator()
            self.entry_menu.add_command(label="协同开启…", command=self.open_collaborative_launch)

    def _background_clicked(self, event):
        if event.widget is self.root or isinstance(event.widget, (ttk.Frame, ttk.Label)):
            self.clear_selection()

    def _show_entry_menu(self, event):
        self.cancel_drag()
        tree = event.widget
        row_id = tree.identify_row(event.y)
        if not row_id:
            self.entry_menu.unpost()
            self.clear_selection()
            return "break"
        self._activate_tree(tree)
        tree.focus_set()
        tree.selection_set(row_id)
        tree.focus(row_id)
        self._configure_entry_menu(self.store.get(row_id).kind)
        try:
            self.entry_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.entry_menu.grab_release()
        return "break"

    def choose_file(self):
        self._chosen(filedialog.askopenfilename(parent=self.root, title="选择文件或快捷方式"))

    def choose_folder(self):
        self._chosen(filedialog.askdirectory(parent=self.root, title="选择文件夹", mustexist=True))

    def _chosen(self, path):
        if path:
            normalized = os.path.normpath(path)
            self.path_var.set(normalized)
            self.status_var.set("点击「保存修改」完成编辑。" if self.editing_id else "点击「保存」添加入口。")

    def submit(self):
        try:
            existing = self.store.get(self.editing_id) if self.editing_id else None
            title = ""
            if self.form_mode == "alias":
                if existing is None or existing.kind not in ("folder", "web", "shortcut"):
                    raise EntryError("文件夹、快捷方式和网页链接可以修改显示名称。")
                title = self.name_var.get()
            elif existing:
                raise EntryError("不再提供修改路径，请按原路径重新添加入口。")
            path = existing.path if existing else self.path_var.get()
            entry = self.store.save_entry(path, title, existing.pinned if existing else False, self.editing_id)
        except (EntryError, OSError) as exc:
            self.status_var.set(self._error_text(exc))
            return
        self.clear_form()
        self.refresh()
        self.status_var.set(f"已保存：{entry.title}。共 {len(self.store.entries)} 个入口。")
        if entry.kind == "shortcut":
            self.status_var.set(f"已收藏「{entry.title}」，可删除桌面上的快捷方式，辅助器仍能打开。")

    def clear_form(self):
        self.cancel_drag()
        self.clear_selection()
        self.editing_id = None
        self.path_var.set("")
        self.name_var.set("")
        self.save_button.configure(text="保存")
        self.form_actions.pack_forget()
        self.form_frame.grid_remove()
        self.add_button.state(["!disabled"])
        self.status_var.set(f"共 {len(self.store.entries)} 个入口。")
        self._focus_visible_tree()

    def _focus_visible_tree(self):
        next((self.trees[kind] for kind in self.store.panel_order
              if self.store.panel_visibility.get(kind, True)), self.trees["folder"]).focus_set()

    def refresh(self, selected_id=None):
        self.cancel_drag()
        for tree in self.trees.values():
            if selected_id is None and tree.selection():
                selected_id = tree.selection()[0]
            tree.delete(*tree.get_children())
        path_widths = {kind: 250 for kind in ENTRY_KINDS}
        for entry in self.store.query(sort="manual"):
            tree = self.trees[entry.kind]
            local_path = self.store.effective_path(entry)
            tree.insert("", "end", iid=entry.id, text=self.display_name(entry), values=(local_path,),
                        image=self.icons.get(local_path, entry.kind))
            path_widths[entry.kind] = max(path_widths[entry.kind], self.list_font.measure(local_path) + 20)
        for kind, tree in self.trees.items():
            tree.column("path", width=path_widths[kind])
        for tree in self.trees.values():
            if selected_id and tree.exists(selected_id):
                tree.selection_set(selected_id)
                tree.focus(selected_id)
                tree.see(selected_id)
        self.status_var.set(f"共 {len(self.store.entries)} 个入口。")

    def _selected(self):
        for tree in self.trees.values():
            selection = tree.selection()
            if selection:
                return self.store.get(selection[0])
        self.status_var.set("请先在列表中选择一个入口。")
        return None

    @staticmethod
    def display_name(entry):
        if entry.kind in ("folder", "web", "shortcut"):
            return entry.title
        return Path(entry.path).stem if entry.kind == "shortcut" else Path(entry.path).name or entry.path

    def open_parent_selected(self):
        entry = self._selected()
        if entry is None:
            return
        if entry.kind == "web":
            self.status_var.set("网页链接没有本地文件所在位置。")
            return
        try:
            parent = self.store.open_parent(entry.id)
            self.status_var.set(f"已打开文件所在位置：{parent}")
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法打开文件所在位置", self._error_text(exc), parent=self.root)

    def copy_selected(self, event=None):
        entry = self._selected()
        if entry is None:
            return "break"
        try:
            if entry.kind == "web":
                write_text(self.root, entry.path)
                self.status_var.set("网页链接已复制。")
            else:
                write_files(self.root, [self.store.effective_path(entry)])
                self.status_var.set("已复制，请选择目标文件夹后按 Ctrl+V，或右键「粘贴」。")
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法复制", self._error_text(exc), parent=self.root)
        return "break"

    def paste_files_selected(self, event=None):
        entry = self._selected()
        if entry is None:
            return "break"
        if self._copy_future is not None:
            self.status_var.set("正在复制，请等待本次完成。")
            return "break"
        try:
            if entry.kind != "folder":
                raise EntryError("请先在文件夹列表中选择要粘贴到的目标文件夹。")
            paths = read_files(self.root)
            if not paths:
                raise EntryError("剪贴板中没有文件或文件夹。请先复制，再选择目标文件夹粘贴。")
            if self._copy_executor is None:
                self._copy_executor = ThreadPoolExecutor(max_workers=1)
            self._copy_future = self._copy_executor.submit(copy_files, paths, entry.path)
            self.status_var.set(f"正在复制到「{self.display_name(entry)}」…")
            self._copy_poll_job = self.root.after(100, self._poll_copy)
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法粘贴", self._error_text(exc), parent=self.root)
        return "break"

    def _poll_copy(self):
        self._copy_poll_job = None
        if not self._copy_future.done():
            self._copy_poll_job = self.root.after(100, self._poll_copy)
            return
        future, self._copy_future = self._copy_future, None
        try:
            result = future.result()
            errors = list(result.errors)
            for path in result.paths:
                try:
                    self.store.save_entry(path)
                except (EntryError, OSError) as exc:
                    errors.append(f"已复制到 {path}，但入口添加失败：{exc}")
            self.refresh()
            self.status_var.set(f"已复制 {len(result.paths)} 个项目。同名项目会自动另存为副本。")
            if errors:
                messagebox.showerror("部分复制未完成", "\n\n".join(errors), parent=self.root)
        except (EntryError, OSError) as exc:
            messagebox.showerror("复制失败", self._error_text(exc), parent=self.root)

    def edit_name_selected(self):
        entry = self._selected()
        if entry is not None:
            self.edit_selected("name") if entry.kind in ("folder", "web", "shortcut") else self.rename_source_selected()

    def _double_click(self, event):
        if ((self._drag_state is not None and self._drag_state.active)
                or time.monotonic() - self._last_drag_finished < 0.4):
            return "break"
        self.cancel_drag()
        tree = event.widget
        row_id = tree.identify_row(event.y)
        if row_id:
            self._activate_tree(tree)
            tree.selection_set(row_id)
            self.open_selected()

    def open_selected(self):
        entry = self._selected()
        if entry is None:
            return
        try:
            self.store.open_entry(entry.id)
            self.status_var.set(f"已打开：{entry.title}")
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法打开", self._error_text(exc), parent=self.root)

    def edit_selected(self, field=None):
        entry = self._selected()
        if entry is None:
            return
        if field == "path":
            self.status_var.set("已取消修改路径。入口始终使用磁盘上的实际路径。")
            return
        if entry.kind not in ("folder", "web", "shortcut"):
            self.rename_source_selected()
            return
        self.editing_id = entry.id
        self.clear_selection()
        self.path_var.set(entry.path)
        self.name_var.set(entry.title)
        self.save_button.configure(text="保存修改")
        self.show_form("alias")
        self.status_var.set("只修改列表中的显示名称，不会更改目标地址。留空可恢复默认名称。")
        self.name_input.focus_set()
        self.name_input.selection_range(0, "end")

    def rename_source_selected(self):
        self.cancel_drag()
        entry = self._selected()
        if entry is None:
            return
        try:
            target = self.store.prepare_source_rename(entry.id, (application_root(),))
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法重命名", self._error_text(exc), parent=self.root)
            return
        editing_before = self.store.get(self.editing_id) if self.editing_id else None
        dialog = RenameDialog(self.root, target, lambda name: self.store.rename_source(
            entry.id, name, target, (application_root(),)))
        self.root.wait_window(dialog)
        # A storage failure may have prevented rollback; reflect the actual path in memory.
        if editing_before and self.path_var.get() == editing_before.path:
            self.path_var.set(self.store.get(editing_before.id).path)
        if dialog.result is None:
            self.refresh(entry.id)
            return
        if not dialog.result:
            self.status_var.set("名称未变化。")
            return
        if self.editing_id in dialog.result:
            self.clear_form()
        self.refresh(entry.id)
        self.status_var.set(f"已重命名：{Path(self.store.get(entry.id).path).name}。入口路径已同步更新。")

    def repair_shortcut_selected(self):
        entry = self._selected()
        if entry is None or entry.kind != "shortcut":
            return
        path = filedialog.askopenfilename(parent=self.root, title="重新导入快捷方式", filetypes=[("Windows 快捷方式", "*.lnk")])
        if not path:
            return
        try:
            self.store.repair_shortcut(entry.id, path)
            self.refresh(entry.id)
            self.status_var.set("快捷方式已独立收藏，原有协同开启预设仍可使用。")
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法导入", self._error_text(exc), parent=self.root)

    def remove_selected(self):
        entry = self._selected()
        if entry is None:
            return
        if not messagebox.askyesno(
                "移除入口",
                f"移除「{entry.title}」？\n\n只清除辅助器中的入口记录，不会改动磁盘上的原文件。",
                parent=self.root):
            return
        self._remove_entry(entry)

    def _remove_entry(self, entry):
        try:
            self.store.remove(entry.id)
            if self.editing_id == entry.id:
                self.clear_form()
            self.refresh()
            self.status_var.set(f"已移除入口。共 {len(self.store.entries)} 个入口。")
        except (EntryError, OSError) as exc:
            messagebox.showerror("移除失败", self._error_text(exc), parent=self.root)

    def delete_selected(self, event=None):
        entry = self._selected()
        if entry is not None:
            if entry.kind in ("folder", "web", "shortcut"):
                self.remove_selected()
            else:
                self.delete_source_selected()
        return "break"

    def _remove_unavailable_file(self, entry):
        if messagebox.askyesno("清理失效入口", f"原路径当前不可用：\n{entry.path}\n\n是否移除此失效入口？\n只清理辅助器记录，不会操作磁盘上的其他内容。", parent=self.root):
            self._remove_entry(entry)

    def delete_source_selected(self):
        self.cancel_drag()
        entry = self._selected()
        if entry is None:
            return
        try:
            target = self.store.prepare_source_deletion(entry.id, (application_root(),))
        except FileNotFoundError:
            self._remove_unavailable_file(entry)
            return
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法删除", self._error_text(exc), parent=self.root)
            return
        if not confirm_source_deletion(self.root, target, entry.title):
            self.status_var.set("已取消删除，原文件和文件夹保持不变。")
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            removed_ids = self.store.delete_source(entry.id, target)
            if self.editing_id in removed_ids:
                self.clear_form()
            self.refresh()
            self.status_var.set(f"已完成删除：{entry.title}。入口已同步移除。")
        except (EntryError, OSError) as exc:
            if not any(item.id == entry.id for item in self.store.entries) and self.editing_id == entry.id:
                self.clear_form()
            self.refresh()
            messagebox.showerror("删除未完成", self._error_text(exc), parent=self.root)
        finally:
            self.root.configure(cursor="")

    @staticmethod
    def _error_text(exc):
        if isinstance(exc, EntryError):
            return str(exc)
        return f"操作失败，请检查路径和数据目录的写入权限。\n{exc}"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_data_directory(executable_root=None, *, frozen=None, local_app_data=None) -> Path:
    root = Path(executable_root) if executable_root is not None else application_root()
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if not frozen:
        return root / "data"

    if local_app_data is None:
        local_app_data = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    user_data = Path(local_app_data) / "文件管理辅助器"
    legacy_data = root / "data"
    if user_data.exists() or not legacy_data.is_dir():
        return user_data

    user_data.parent.mkdir(parents=True, exist_ok=True)
    staging = user_data.with_name(f".{user_data.name}.migration-{uuid.uuid4().hex}")
    try:
        shutil.copytree(legacy_data, staging)
        try:
            os.rename(staging, user_data)
        except OSError:
            if not user_data.is_dir():
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return user_data


def main():
    parser = argparse.ArgumentParser(description="文件管理辅助器")
    parser.add_argument("--data-dir", type=Path, help="指定入口数据保存目录")
    args = parser.parse_args()
    data_dir = (args.data_dir or default_data_directory()).resolve()
    root = tk.Tk()
    root.withdraw()
    mutex = None
    if sys.platform == "win32":
        import hashlib
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\FileShortcuts-" + hashlib.sha256(str(data_dir).casefold().encode()).hexdigest())
        if not mutex:
            messagebox.showerror("文件管理辅助器", "无法创建应用锁，请重试。", parent=root)
            root.destroy()
            return
        if ctypes.get_last_error() == 183:
            from .desktop import request_restore
            if not request_restore(data_dir):
                messagebox.showinfo("文件管理辅助器", "程序已经在运行，请从系统托盘显示主界面。", parent=root)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
            root.destroy()
            return
    try:
        app = App(root, EntryStore(data_dir))
        root.deiconify()
        if sys.platform == "win32":
            from .desktop import DesktopIntegration
            root.update_idletasks()
            try:
                app.desktop = DesktopIntegration(app)
                app.update_floating_ball_button()
            except OSError as exc:
                app.status_var.set(f"系统托盘暂不可用，关闭窗口将直接退出。{exc}")
        root.mainloop()
    except (EntryError, OSError) as exc:
        messagebox.showerror("启动失败", str(exc), parent=root)
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
