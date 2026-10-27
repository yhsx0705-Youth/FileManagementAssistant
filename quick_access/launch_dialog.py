"""Inline shortcut numbering and small, reusable launch presets."""

import tkinter as tk
import copy
import os
from tkinter import messagebox, ttk

from .launcher import SequentialLauncher, ordered_shortcuts
from .scroll_frame import ScrollFrame
from .storage import EntryError


class ShortcutLaunch:
    def __init__(self, app):
        self.app, self.root = app, app.root
        self.editor = None
        self.rows = {}
        self.hidden_widgets = []
        self.editing_preset_id = None
        self.row_snapshots = {}
        self.save_var = tk.BooleanVar(value=False)
        self.name_var = tk.StringVar()
        self.launcher = SequentialLauncher(self.root, app.store, self._progress, self._done)
        self.toolbar = tk.Frame(app.panel_heading_frames["shortcut"], bg="#EEF2F5")
        self.toolbar.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=4)
        self.action_button = ttk.Button(self.toolbar, text="协同开启", command=self.action, width=10)
        self.action_button.pack(side="left")
        self.cancel_button = ttk.Button(self.toolbar, text="取消", command=self.cancel_edit, width=5)
        self.preset_button = ttk.Menubutton(self.toolbar, text="预设 ▾", width=7)
        self.preset_menu = tk.Menu(self.preset_button, tearoff=False)
        self.preset_button.configure(menu=self.preset_menu)
        self.preset_button.pack(side="left", padx=(5, 0))
        self.refresh_presets()
        self.root.bind("<Destroy>", self._destroyed, add="+")

    def refresh_presets(self):
        self.preset_menu.delete(0, "end")
        for preset in self.app.workspace.presets:
            self.preset_menu.add_command(label=preset["name"],
                                        command=lambda item=preset: self.run_preset(item))
        if self.app.workspace.presets:
            self.preset_menu.add_separator()
        self.preset_menu.add_command(label="新建预设…", command=self.begin)
        self.preset_menu.add_command(label="管理预设…", command=self.manage_presets,
                                    state="normal" if self.app.workspace.presets else "disabled")
        self.preset_button.state(["!disabled"])

    def action(self):
        if self.launcher.running:
            self.launcher.cancel()
        elif self.editor is not None:
            self.confirm_open()
        else:
            self.begin()

    def begin(self, preset=None):
        if self.launcher.running:
            return
        if self.editor is not None:
            if preset is None:
                return
            self.cancel_edit()
        entries = self.app.store.query("shortcut", sort="manual")
        if not entries and preset is None:
            self.app.status_var.set("请先添加快捷方式，再使用协同开启。")
            return
        self.app.clear_selection()
        panel = self.app.panels["shortcut"]
        self.hidden_widgets = [widget for widget in panel.winfo_children()
                               if widget.winfo_manager() == "grid" and int(widget.grid_info()["row"]) == 1]
        for widget in self.hidden_widgets:
            widget.grid_remove()
        self.editor = ScrollFrame(panel)
        self.editor.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.rows.clear()
        self.editing_preset_id = preset["id"] if preset else None
        self.row_snapshots = {item["entry_id"]: item for item in preset.get("launch_items", [])} if preset else {}
        selected_ids = preset["entry_ids"] if preset else []
        options = ttk.Frame(self.editor.content)
        options.pack(fill="x", pady=(2, 10))
        self.save_var.set(False)
        names = {item["name"] for item in self.app.workspace.presets}
        number = 1
        while f"预设 {number}" in names:
            number += 1
        self.name_var.set(preset["name"] if preset else f"预设 {number}")
        ttk.Checkbutton(options, text="打开时保存", variable=self.save_var,
                        command=self._toggle_save).pack(side="left")
        self.save_only_button = ttk.Button(options, text="仅保存", width=6, command=self.save_only)
        self.save_only_button.pack(side="right", padx=(6, 0))
        self.name_input = ttk.Entry(options, textvariable=self.name_var, width=12)
        self.name_input.pack(side="left", fill="x", expand=True, padx=(8, 0))
        heading = ttk.Frame(self.editor.content)
        heading.pack(fill="x", pady=(0, 5))
        ttk.Label(heading, text="勾选要打开的快捷方式").pack(side="left")
        ttk.Label(heading, text="序号").pack(side="right", padx=12)
        by_id = {entry.id: entry for entry in entries}
        for ident in dict.fromkeys([*selected_ids, *(entry.id for entry in entries)]):
            entry = by_id.get(ident)
            snapshot = self.row_snapshots.get(ident)
            row = ttk.Frame(self.editor.content, padding=(0, 4))
            row.pack(fill="x")
            selected = tk.BooleanVar(value=ident in selected_ids)
            number = tk.StringVar(value=str(selected_ids.index(ident) + 1) if ident in selected_ids else "")
            ttk.Spinbox(row, from_=1, to=9999, textvariable=number, width=5).pack(side="right", padx=(5, 0))
            ttk.Checkbutton(row, variable=selected,
                            command=lambda value=ident: self._selected(value)).pack(side="left")
            if entry:
                path, title = self.app.store.effective_path(entry), self.app.display_name(entry)
            else:
                item = self.app.workspace.launch_items({"launch_items": [snapshot]})[0] if snapshot else None
                path = item["path"] if item and item["path"] else "missing.lnk"
                title = (snapshot["title"] if snapshot else "已移除的快捷方式") + "（独立预设）"
            ttk.Label(row, image=self.app.icons.get(path, "shortcut")).pack(side="left", padx=(0, 5))
            ttk.Label(row, text=title, anchor="w").pack(side="left", fill="x", expand=True)
            self.rows[ident] = (selected, number)
        self.action_button.configure(text="确定打开")
        self.cancel_button.pack(side="left", padx=(5, 0), before=self.preset_button)
        self.app.status_var.set("勾选并填写序号，点击「仅保存」可提前创建预设，不会打开程序。")

    def _toggle_save(self):
        if self.save_var.get():
            self.name_input.focus_set()
            self.name_input.selection_range(0, "end")

    def _selected(self, entry_id):
        selected, number = self.rows[entry_id]
        if selected.get() and not number.get().strip():
            used = [int(value.get()) for enabled, value in self.rows.values()
                    if enabled.get() and value.get().isdigit()]
            number.set(str(max(used, default=0) + 1))

    def cancel_edit(self):
        if self.editor is not None:
            self.editor.destroy()
            self.editor = None
            for widget in self.hidden_widgets:
                widget.grid()
            self.hidden_widgets = []
        self.cancel_button.pack_forget()
        self.editing_preset_id = None
        self.action_button.configure(text="停止开启" if self.launcher.running else "协同开启")

    def confirm_open(self):
        if self.launcher.running:
            return
        try:
            ids = self._ordered_ids()
            snapshots = self._capture(ids) if self.save_var.get() or self.editing_preset_id else None
            if self.save_var.get():
                self._persist(ids, snapshots)
            if snapshots is not None:
                self.launcher.start_items(self.app.workspace.launch_items({"launch_items": snapshots}))
            else:
                self.launcher.start(ids)
        except (EntryError, OSError) as exc:
            self.launcher.cancel()
            self.action_button.configure(text="确定打开")
            self.app.status_var.set(str(exc))
            return
        self.cancel_edit()
        self.app.status_var.set("准备依次开启…")

    def _ordered_ids(self):
        return ordered_shortcuts([(ident, selected.get(), number.get())
                                  for ident, (selected, number) in self.rows.items()])

    def _capture(self, ids):
        snapshots = []
        for ident in ids:
            previous = self.row_snapshots.get(ident)
            if previous and previous.get("path"):
                resolved = self.app.workspace.launch_items({"launch_items": [previous]})[0]
                if os.path.isfile(resolved["path"]):
                    snapshots.append(copy.deepcopy(previous))
                    continue
            snapshots.append(self.app.store.shortcut_snapshot(ident))
        return snapshots

    def _persist(self, ids, snapshots):
        preset = self.app.workspace.save_preset(self.name_var.get(), ids, self.editing_preset_id,
                                                launch_items=snapshots)
        self.refresh_presets()
        return preset

    def save_only(self):
        try:
            ids = self._ordered_ids()
            preset = self._persist(ids, self._capture(ids))
        except (EntryError, OSError) as exc:
            self.app.status_var.set(str(exc))
            return
        self.cancel_edit()
        self.app.status_var.set(f"预设「{preset['name']}」已保存，未打开任何程序。")

    def run_preset(self, preset):
        try:
            self.app.workspace.upgrade_presets(self.app.store)
            preset = next((item for item in self.app.workspace.presets if item["id"] == preset["id"]), None)
            if preset is None:
                self.app.status_var.set("这个预设已移除，请重新选择。")
                return
            self.launcher.start_items(self.app.workspace.launch_items(preset))
        except (EntryError, OSError) as exc:
            messagebox.showerror("无法协同开启", str(exc), parent=self.root)
            return
        self.cancel_edit()
        self.app.status_var.set(f"正在开启预设「{preset['name']}」…")

    def _progress(self, current, total, title):
        self.app.status_var.set(f"协同开启 {current}/{total}：{title}")

    def _done(self, count, error):
        self.action_button.configure(text="协同开启")
        self.app.status_var.set(f"已打开 {count} 个快捷方式。" + (error or "协同开启完成。"))

    def manage_presets(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("管理预设")
        dialog.transient(self.root)
        dialog.geometry("300x260")
        box = tk.Listbox(dialog, activestyle="none", exportselection=False)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        records = list(self.app.workspace.presets)
        for item in records:
            box.insert("end", item["name"])

        def remove():
            selection = box.curselection()
            if not selection:
                return
            item = records[selection[0]]
            if not messagebox.askyesno("删除预设", f"删除预设「{item['name']}」？快捷方式会保留。", parent=dialog):
                return
            try:
                self.app.workspace.remove_preset(item["id"])
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            records.pop(selection[0])
            box.delete(selection[0])
            self.refresh_presets()

        def edit():
            selection = box.curselection()
            if selection:
                preset = records[selection[0]]
                dialog.destroy()
                self.begin(preset)

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=(0, 12))
        ttk.Button(buttons, text="编辑预设", command=edit).pack(side="left", padx=4)
        ttk.Button(buttons, text="删除预设", command=remove).pack(side="left", padx=4)

    def shutdown(self):
        self.launcher.cancel()

    def _destroyed(self, event):
        if event.widget is self.root and self.launcher.job is not None:
            self.root.after_cancel(self.launcher.job)
            self.launcher.job = None
            self.launcher.running = False
