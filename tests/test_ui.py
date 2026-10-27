"""Verify the simple window's add/edit/open/remove workflow."""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quick_access.app import App
from quick_access.storage import EntryError, EntryStore


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.document = self.directory / "中文合同.txt"
        self.document.write_text("UI test", encoding="utf-8")
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = App(self.root, EntryStore(self.directory / "data"))
        self.root.update()
        self.assertEqual(self.app.developer_label.cget("text"), "开发者：云鹤sx")
        self.assertTrue(self.app.developer_label.winfo_ismapped())
        self.assertEqual(self.app.developer_label.pack_info()["side"], "right")
        self.assertIn("9", str(self.app.developer_label.cget("font")))

    def tearDown(self):
        self.app.cancel_drag()
        self.root.destroy()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def test_add_validation_and_inline_edit(self):
        self.app.begin_add()
        self.app.submit()
        self.assertIn("请先选择", self.app.status_var.get())
        self.assertTrue(self.app.form_frame.winfo_ismapped())
        self.app.path_input.insert(0, str(self.document))
        self.assertFalse(self.app.name_input.winfo_ismapped())
        self.app.submit()
        self.root.update()
        entry = self.app.store.entries[0]
        self.assertEqual(entry.title, self.document.name)
        self.assertEqual(self.app.trees["file"].item(entry.id, "text"), self.document.name)
        self.assertEqual(self.app.trees["file"].item(entry.id, "values"), (str(self.document),))
        self.assertEqual(self.app.trees["file"].selection(), ())
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.assertIsNone(self.app.editing_id)
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.app.begin_add()
        self.app.path_var.set(str(self.document))
        self.app.submit()
        self.assertIn("已经添加", self.app.status_var.get())
        self.assertEqual(len(self.app.store.entries), 1)

    def test_open_remove_and_keep_original(self):
        self.app._chosen(str(self.document))
        self.app.submit()
        entry = self.app.store.entries[0]
        self.app.trees["file"].selection_set(entry.id)
        with patch("quick_access.storage.os.startfile") as opener:
            self.app.open_selected()
            opener.assert_called_once_with(str(self.document))
        self.assertEqual(self.app.store.get(entry.id).open_count, 1)
        self.app.trees["file"].selection_set(entry.id)
        with patch("quick_access.app.messagebox.askyesno", return_value=True):
            self.app.remove_selected()
        self.assertEqual(self.app.store.entries, [])
        self.assertEqual(self.app.trees["file"].get_children(), ())
        self.assertTrue(self.document.exists())
        self.assertIsNone(self.app.editing_id)

    def test_paste_quoted_explorer_path_and_save(self):
        self.app.begin_add()
        self.app.path_var.set("replace this")
        self.app.select_path()
        with patch.object(self.root, "clipboard_get", return_value=f'"{self.document}"'):
            self.app.paste_path()
        self.assertEqual(self.app.path_var.get(), f'"{self.document}"')
        self.app.submit()
        self.assertEqual(self.app.store.entries[0].path, str(self.document))
        self.assertEqual(self.app.trees["file"].selection(), ())

    def test_add_form_is_on_demand_and_cancel_does_not_save_draft(self):
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.app.add_button.invoke()
        self.root.update()
        self.assertTrue(self.app.path_input.winfo_ismapped())
        self.assertFalse(self.app.name_input.winfo_ismapped())
        self.app.path_var.set(str(self.document))
        self.app.name_var.set("未保存的名称")
        self.app.cancel_button.invoke()
        self.root.update()
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.assertEqual(self.app.store.entries, [])
        self.app.add_button.invoke()
        self.assertEqual(self.app.path_var.get(), "")
        self.assertEqual(self.app.name_var.get(), "")

    def test_empty_clipboard_keeps_existing_path(self):
        self.app.path_var.set(str(self.document))
        with patch.object(self.root, "clipboard_get", side_effect=tk.TclError("empty")):
            self.app.paste_path()
        self.assertEqual(self.app.path_var.get(), str(self.document))
        self.assertIn("没有可粘贴", self.app.status_var.get())

    def test_blank_list_click_clears_selection_and_cannot_open_previous_row(self):
        self.app._chosen(str(self.document))
        self.app.submit()
        entry = self.app.store.entries[0]
        self.app.trees["file"].selection_set(entry.id)
        self.root.update()
        self.app.trees["file"].event_generate("<Button-1>", x=20, y=160)
        self.root.update()
        self.assertEqual(self.app.trees["file"].selection(), ())
        with patch("quick_access.storage.os.startfile") as opener:
            self.app.open_selected()
            opener.assert_not_called()

    def test_focusing_input_clears_list_selection(self):
        self.app._chosen(str(self.document))
        self.app.submit()
        entry = self.app.store.entries[0]
        for field in (self.app.path_input, self.app.name_input):
            self.app.trees["file"].selection_set(entry.id)
            field.event_generate("<FocusIn>")
            self.root.update()
            self.assertEqual(self.app.trees["file"].selection(), ())

    def context_entries(self):
        first = self.app.store.save_entry(str(self.document), "合同")
        folder = self.directory / "资料文件夹"
        folder.mkdir()
        second = self.app.store.save_entry(str(folder), "资料")
        self.app.refresh()
        self.root.update()
        self.app.trees["file"].selection_set(first.id)
        tree = self.app.trees["folder"]
        x, y, width, height = tree.bbox(second.id)
        event = SimpleNamespace(widget=tree, x=x + 10, y=y + height // 2, x_root=50, y_root=50)
        return first, second, event

    def test_context_rename_targets_right_clicked_row(self):
        first, second, event = self.context_entries()
        with patch.object(self.app.entry_menu, "tk_popup") as popup:
            self.app._show_entry_menu(event)
            popup.assert_called_once_with(event.x_root, event.y_root)
        self.assertEqual(self.app.trees["folder"].selection(), (second.id,))
        self.assertEqual(self.app.trees["file"].selection(), ())
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.app.entry_menu.invoke(0)
        self.assertEqual(self.app.editing_id, second.id)
        self.assertTrue(self.app.form_frame.winfo_ismapped())
        self.assertTrue(self.app.name_input.selection_present())
        self.app.name_var.set("新资料名称")
        self.app.submit()
        self.assertEqual(self.app.store.get(second.id).title, "新资料名称")
        self.assertEqual(self.app.store.get(second.id).path, second.path)
        self.assertEqual(self.app.store.get(first.id), first)
        self.assertEqual(self.app.trees["file"].selection(), ())

    def test_folder_alias_cannot_change_the_hidden_path(self):
        first, second, event = self.context_entries()
        with patch.object(self.app.entry_menu, "tk_popup"):
            self.app._show_entry_menu(event)
        self.app.entry_menu.invoke(0)
        self.assertEqual(self.app.editing_id, second.id)
        self.assertFalse(self.app.path_input.winfo_ismapped())
        new_folder = self.directory / "新路径"
        new_folder.mkdir()
        self.app.path_var.set(str(new_folder))
        self.app.submit()
        updated = self.app.store.get(second.id)
        self.assertEqual(updated.path, second.path)
        self.assertEqual(updated.title, second.title)
        self.assertEqual(self.app.store.get(first.id), first)
        self.assertTrue(Path(second.path).is_dir())

    def test_context_delete_only_removes_right_clicked_shortcut(self):
        first, second, event = self.context_entries()
        with patch.object(self.app.entry_menu, "tk_popup"):
            self.app._show_entry_menu(event)
        with patch("quick_access.app.messagebox.askyesno", return_value=True):
            self.app.entry_menu.invoke(3)
        self.assertEqual(self.app.store.entries, [first])
        self.assertTrue(Path(second.path).is_dir())
        self.assertTrue(self.document.exists())

    def test_right_click_blank_space_clears_selection_without_menu(self):
        self.context_entries()
        event = SimpleNamespace(widget=self.app.trees["folder"], x=20, y=180, x_root=50, y_root=50)
        with patch.object(self.app.entry_menu, "tk_popup") as popup:
            self.app._show_entry_menu(event)
            popup.assert_not_called()
        self.assertEqual(self.app.trees["file"].selection(), ())
        with patch("quick_access.app.messagebox.askyesno") as confirm:
            self.app.entry_menu.invoke(3)
            confirm.assert_not_called()
        self.assertEqual(len(self.app.store.entries), 2)

    def test_files_and_folders_have_separate_lists_and_icons(self):
        first, second, _ = self.context_entries()
        self.assertEqual(self.app.trees["file"].get_children(), (first.id,))
        self.assertEqual(self.app.trees["folder"].get_children(), (second.id,))
        self.assertTrue(self.app.trees["file"].item(first.id, "image"))
        self.assertTrue(self.app.trees["folder"].item(second.id, "image"))
        self.app.trees["file"].selection_set(first.id)
        self.app.trees["folder"].event_generate("<Button-1>", x=35, y=35)
        self.root.update()
        self.assertEqual(self.app.trees["file"].selection(), ())
        self.assertEqual(self.app.trees["folder"].selection(), (second.id,))

    def test_four_entry_panels_show_files_folders_shortcuts_and_web_links(self):
        folder = self.directory / "界面目录"
        folder.mkdir()
        shortcut = self.directory / "工具.lnk"
        shortcut.write_text("fixture", encoding="utf-8")
        web_shortcut = self.directory / "帮助.url"
        web_shortcut.write_text("[InternetShortcut]\nURL=https://example.com/help\n", encoding="utf-8")
        entries = [self.app.store.save_entry(str(folder)),
                   self.app.store.save_entry(str(self.document)),
                   self.app.store.save_entry(str(shortcut)),
                   self.app.store.save_entry(str(web_shortcut))]
        self.app.refresh()
        self.root.update()
        self.assertEqual(set(self.app.trees), {"folder", "file", "shortcut", "web"})
        for entry in entries:
            self.assertTrue(self.app.trees[entry.kind].exists(entry.id))
        self.assertEqual(self.app.trees["shortcut"].item(entries[2].id, "text"), "工具")
        self.assertEqual(self.app.trees["web"].item(entries[3].id, "text"), "example.com")

    def test_panels_can_be_hidden_and_dragged_to_a_new_persisted_position(self):
        self.assertTrue(self.root.tk.call("bind", str(self.root), "<B1-Motion>"))
        self.assertTrue(self.root.tk.call("bind", str(self.root), "<ButtonRelease-1>"))
        self.root.update()
        dialog = self.app.open_panel_settings()
        self.app._panel_settings_vars["shortcut"].set(False)
        self.assertTrue(self.app.store.panel_visibility["shortcut"])
        self.assertEqual(self.app.panels["shortcut"].winfo_manager(), "grid")
        self.app._save_panel_settings()
        self.assertEqual(self.app.panels["shortcut"].winfo_manager(), "")
        self.assertFalse(dialog.winfo_exists())
        self.assertFalse(EntryStore(self.app.store.directory).panel_visibility["shortcut"])

        self.root.update()
        source = "folder"
        header = self.app.panel_headers[source]
        start_x = header.winfo_rootx() + header.winfo_width() // 2
        start_y = header.winfo_rooty() + header.winfo_height() // 2
        target = self.app.panels["web"]
        drop_x = target.winfo_rootx() + target.winfo_width() // 2
        drop_y = target.winfo_rooty() + target.winfo_height() // 2
        self.app._panel_drag_start_event(source, SimpleNamespace(x_root=start_x, y_root=start_y))
        self.app._panel_drag_motion(SimpleNamespace(x_root=drop_x, y_root=drop_y))
        self.assertEqual(target.cget("highlightbackground"), "#2676B8")
        self.assertIsNotNone(self.app._panel_drag_ghost)
        self.app._panel_drag_release(source, SimpleNamespace(x_root=drop_x, y_root=drop_y))
        self.assertEqual(self.app.store.panel_order, ["web", "file", "shortcut", "folder"])
        reloaded = EntryStore(self.app.store.directory)
        self.assertEqual(reloaded.panel_order, self.app.store.panel_order)
        self.assertFalse(reloaded.panel_visibility["shortcut"])

    def test_last_visible_panel_cannot_be_hidden(self):
        self.app.store.save_entry(str(self.document))
        before = self.app.store.path.read_bytes()
        self.app.open_panel_settings()
        for variable in self.app._panel_settings_vars.values():
            variable.set(False)
        self.app._save_panel_settings()
        self.assertIn("至少保留一个", self.app._panel_settings_error.get())
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual(sum(self.app.store.panel_visibility.values()), 4)

    def test_visible_panel_layout_expands_to_fill_empty_slots(self):
        visibility = {"folder": True, "file": True, "shortcut": False, "web": False}
        self.app.store.save_panel_visibility(visibility)
        self.app.apply_panel_layout()
        folder_grid = self.app.panels["folder"].grid_info()
        file_grid = self.app.panels["file"].grid_info()
        self.assertEqual(folder_grid["rowspan"], 2)
        self.assertEqual(file_grid["rowspan"], 2)
        self.assertEqual(folder_grid["column"], 0)
        self.assertEqual(file_grid["column"], 1)

        self.app.store.save_panel_visibility({"folder": True, "file": False,
                                              "shortcut": False, "web": False})
        self.app.apply_panel_layout()
        self.assertEqual(self.app.panels["folder"].grid_info()["rowspan"], 2)
        self.assertEqual(self.app.panels["folder"].grid_info()["columnspan"], 2)

    def test_web_entry_opens_copies_and_only_offers_link_actions(self):
        entry = self.app.store.save_entry("https://example.com/help")
        self.app.refresh()
        self.app.trees["web"].selection_set(entry.id)
        with patch("webbrowser.open") as opener:
            self.app.open_selected()
        opener.assert_called_once_with(entry.path)
        with patch("quick_access.app.write_text") as copy_text:
            self.app.copy_selected()
        copy_text.assert_called_once_with(self.root, entry.path)
        self.app._configure_entry_menu("web")
        labels = [self.app.entry_menu.entrycget(i, "label")
                  for i in range(self.app.entry_menu.index("end") + 1)
                  if self.app.entry_menu.type(i) != "separator"]
        self.assertEqual(labels, ["修改显示名称…", "复制网页链接", "移除入口"])

    def test_web_clipboard_contention_is_shown_as_a_copy_error(self):
        entry = self.app.store.save_entry("https://example.com/help")
        self.app.refresh()
        self.app.trees["web"].selection_set(entry.id)
        with patch("quick_access.app.write_text", side_effect=EntryError("系统剪贴板正忙")):
            with patch("quick_access.app.messagebox.showerror") as error:
                self.app.copy_selected()
        self.assertEqual(error.call_args.args[0], "无法复制")
        self.assertIn("系统剪贴板正忙", error.call_args.args[1])

    def test_paths_hidden_by_default_and_can_be_shown_without_losing_data(self):
        first, second, _ = self.context_entries()
        self.assertFalse(self.app.show_paths_var.get())
        for tree in self.app.trees.values():
            self.assertFalse(tree["displaycolumns"])
        self.app.show_paths_var.set(True)
        self.app.toggle_paths()
        self.root.update()
        for entry in (first, second):
            tree = self.app.trees[entry.kind]
            self.assertEqual(tree["displaycolumns"], ("path",))
            self.assertEqual(tree.item(entry.id, "values"), (entry.path,))
            self.assertGreaterEqual(tree.column("path", "width"), self.app.list_font.measure(entry.path))
        self.app.show_paths_var.set(False)
        self.app.toggle_paths()
        self.assertEqual(len(self.app.store.entries), 2)
        for tree in self.app.trees.values():
            self.assertFalse(tree["displaycolumns"])

    def test_existing_entry_path_edit_is_not_available(self):
        self.app._chosen(str(self.document))
        self.app.submit()
        entry = self.app.store.entries[0]
        self.app.trees["file"].selection_set(entry.id)
        self.app.edit_selected("path")
        self.assertFalse(self.app.form_frame.winfo_ismapped())
        self.assertIn("已取消修改路径", self.app.status_var.get())
        self.assertEqual(self.app.store.get(entry.id).path, str(self.document))
        self.assertTrue(self.app.trees["file"].exists(entry.id))
        self.assertTrue(self.document.exists())

    def drag_entries(self, kind="file"):
        for index in range(3):
            if kind == "web":
                self.app.store.save_entry(f"https://example.com/order/{index}")
                continue
            name = (f"排序文件{index}.txt" if kind == "file" else
                    f"排序应用{index}.lnk" if kind == "shortcut" else f"排序文件夹{index}")
            path = self.directory / name
            if kind == "folder":
                path.mkdir()
            else:
                path.write_text(str(index), encoding="utf-8")
            self.app.store.save_entry(str(path))
        self.app.refresh()
        self.root.update()
        tree = self.app.trees[kind]
        return tree, tree.get_children()

    def drag_point(self, tree, row_id, bottom=False):
        x, y, width, height = tree.bbox(row_id)
        return SimpleNamespace(widget=tree, x=35, y=y + height - 2 if bottom else y + 1)

    def start_drag(self, tree, row_id):
        event = self.drag_point(tree, row_id)
        self.app._tree_clicked(event)
        tree.selection_set(row_id)

    def test_drag_to_bottom_persists_for_all_entry_types(self):
        for kind in ("file", "folder", "shortcut", "web"):
            with self.subTest(kind=kind):
                tree, rows = self.drag_entries(kind)
                target = self.drag_point(tree, rows[-1], bottom=True)
                self.start_drag(tree, rows[0])
                with patch("quick_access.storage.os.startfile") as opener:
                    self.app._drag_motion(target)
                    self.app._drag_release(target)
                    opener.assert_not_called()
                expected = rows[1:] + rows[:1]
                self.assertEqual(tree.get_children(), expected)
                loaded = EntryStore(self.app.store.directory)
                self.assertEqual(tuple(e.id for e in loaded.query(kind, sort="manual")), expected)
                if kind != "web":
                    self.assertTrue(all(Path(loaded.get(entry_id).path).exists() for entry_id in rows))

    def test_other_lists_can_drag_to_top_without_opening_or_changing_targets(self):
        for kind in ("folder", "shortcut", "web"):
            with self.subTest(kind=kind):
                tree, rows = self.drag_entries(kind)
                before = {ident: (self.app.store.get(ident).path, self.app.store.get(ident).title) for ident in rows}
                target = self.drag_point(tree, rows[0])
                self.start_drag(tree, rows[-1])
                with patch("quick_access.storage.os.startfile") as opener, patch("webbrowser.open") as browser:
                    self.app._drag_motion(target)
                    self.app._drag_release(target)
                opener.assert_not_called()
                browser.assert_not_called()
                self.assertEqual(tree.get_children(), rows[-1:] + rows[:-1])
                self.assertEqual({ident: (self.app.store.get(ident).path, self.app.store.get(ident).title) for ident in rows}, before)

    def test_other_lists_cancel_cross_panel_sort_and_escape_restores_order(self):
        for kind in ("folder", "shortcut", "web"):
            with self.subTest(kind=kind):
                tree, rows = self.drag_entries(kind)
                original = self.app.store.path.read_bytes()
                target = self.drag_point(tree, rows[-1], bottom=True)
                self.start_drag(tree, rows[0])
                self.app._drag_motion(target)
                target.x = tree.winfo_width() + 15
                self.app._drag_release(target)
                self.assertEqual(tree.get_children(), rows)
                self.assertEqual(self.app.store.path.read_bytes(), original)
                self.start_drag(tree, rows[0])
                target = self.drag_point(tree, rows[-1], bottom=True)
                self.app._drag_motion(target)
                self.app.clear_form()
                self.assertIsNone(self.app._drag_state)
                self.assertEqual(tree.get_children(), rows)
                self.assertEqual(self.app.store.path.read_bytes(), original)

    def test_drag_to_top_previews_without_saving_until_release(self):
        tree, rows = self.drag_entries()
        original_file = self.app.store.path.read_bytes()
        target = self.drag_point(tree, rows[0])
        self.start_drag(tree, rows[-1])
        self.app._drag_motion(target)
        self.assertEqual(tree.get_children(), rows)
        self.assertEqual(self.app._drag_state.target_index, 0)
        self.root.update_idletasks()
        self.assertTrue(self.app._drag_state.ghost.winfo_ismapped())
        self.assertTrue(self.app._drag_state.indicator.winfo_ismapped())
        self.assertEqual(self.app.store.path.read_bytes(), original_file)
        self.app._drag_release(target)
        self.assertEqual(tuple(e.id for e in self.app.store.query("file", sort="manual")),
                         rows[-1:] + rows[:-1])

    def test_plain_click_or_tiny_movement_does_not_reorder(self):
        tree, rows = self.drag_entries()
        self.start_drag(tree, rows[0])
        point = self.drag_point(tree, rows[0])
        point.y += 2
        with patch.object(self.app.store, "reorder") as reorder:
            self.app._drag_motion(point)
            self.app._drag_release(point)
            reorder.assert_not_called()
        self.assertEqual(tree.get_children(), rows)
        self.assertIsNone(self.app._drag_state)

    def test_drop_outside_source_list_cancels_preview(self):
        tree, rows = self.drag_entries()
        original_file = self.app.store.path.read_bytes()
        target = self.drag_point(tree, rows[-1], bottom=True)
        self.start_drag(tree, rows[0])
        self.app._drag_motion(target)
        self.assertEqual(self.app._drag_state.target_index, len(rows) - 1)
        target.x = tree.winfo_width() + 15
        self.app._drag_release(target)
        self.assertEqual(tree.get_children(), rows)
        self.assertEqual(self.app.store.path.read_bytes(), original_file)
        self.assertIsNone(self.app._drag_state)

    def test_cancel_stops_edge_scrolling_and_restores_order(self):
        tree, rows = self.drag_entries()
        self.start_drag(tree, rows[0])
        point = SimpleNamespace(widget=tree, x=35, y=tree.winfo_height() - 2)
        self.app._drag_motion(point)
        job = self.app._drag_state.scroll_job
        self.assertIsNotNone(job)
        self.app.clear_form()
        self.assertIsNone(self.app._drag_state)
        self.assertNotIn(job, self.root.tk.call("after", "info"))
        self.assertEqual(tree.get_children(), rows)
        self.assertEqual(tuple(e.id for e in self.app.store.query("file", sort="manual")), rows)

    def test_holding_drag_at_bottom_scrolls_long_list(self):
        tree, _ = self.drag_entries()
        for index in range(3, 30):
            path = self.directory / f"排序文件{index}.txt"
            path.write_text(str(index), encoding="utf-8")
            self.app.store.save_entry(str(path))
        self.app.refresh()
        self.root.update()
        rows = tree.get_children()
        self.assertEqual(tree.yview()[0], 0)
        self.start_drag(tree, rows[0])
        self.app._drag_motion(SimpleNamespace(widget=tree, x=35, y=tree.winfo_height() - 2))
        self.root.after(150, self.root.quit)
        self.root.mainloop()
        self.assertGreater(tree.yview()[0], 0)
        self.app.cancel_drag()
        self.assertEqual(tree.get_children(), rows)

    def test_failed_drag_save_restores_view_and_data(self):
        tree, rows = self.drag_entries()
        original_file = self.app.store.path.read_bytes()
        before_entries = self.app.store.entries[:]
        target = self.drag_point(tree, rows[-1], bottom=True)
        self.start_drag(tree, rows[0])
        self.app._drag_motion(target)
        with patch("quick_access.storage.os.replace", side_effect=OSError("Disk full")):
            self.app._drag_release(target)
        self.assertEqual(tree.get_children(), rows)
        self.assertEqual(self.app.store.path.read_bytes(), original_file)
        self.assertEqual(self.app.store.entries, before_entries)
        self.assertIn("顺序保存失败", self.app.status_var.get())
        self.assertEqual(str(tree["cursor"]), "")

    def test_drag_does_not_trigger_double_click_open(self):
        tree, rows = self.drag_entries()
        target = self.drag_point(tree, rows[-1], bottom=True)
        self.start_drag(tree, rows[0])
        with patch("quick_access.storage.os.startfile") as opener:
            self.app._drag_motion(target)
            self.app._double_click(target)
            self.app._drag_release(target)
            self.app._double_click(target)
            opener.assert_not_called()

    def test_drag_hints_follow_pointer_and_are_removed_after_drop(self):
        tree, rows = self.drag_entries()
        self.start_drag(tree, rows[0])
        first_point = self.drag_point(tree, rows[1])
        self.app._drag_motion(first_point)
        self.root.update_idletasks()
        state = self.app._drag_state
        ghost, indicator = state.ghost, state.indicator
        first_ghost_y = ghost.winfo_y()
        first_line_y = indicator.winfo_y()
        last_point = self.drag_point(tree, rows[-1], bottom=True)
        self.app._drag_motion(last_point)
        self.root.update_idletasks()
        self.assertGreater(ghost.winfo_y(), first_ghost_y)
        self.assertGreater(indicator.winfo_y(), first_line_y)
        self.assertEqual(tree.get_children(), rows)
        self.app._drag_release(last_point)
        self.assertFalse(ghost.winfo_exists())
        self.assertFalse(indicator.winfo_exists())
        self.assertNotIn("drag-source", tree.item(rows[0], "tags"))
        self.assertEqual(tree.get_children(), rows[1:] + rows[:1])


if __name__ == "__main__":
    unittest.main()
