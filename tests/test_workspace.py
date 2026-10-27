"""Persistence and end-to-end UI coverage for cards and ordered shortcut opening."""

import copy
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quick_access.app import App
from quick_access.launcher import SequentialLauncher, ordered_shortcuts
from quick_access.storage import EntryError, EntryStore
from quick_access.workspace import WorkspaceStore
from quick_access.clipboard_panel import MAX_CARD_HEIGHT, MIN_CARD_HEIGHT, dock_rect


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.workspace = WorkspaceStore(self.directory)

    def tearDown(self):
        self.temporary.cleanup()

    def test_notes_presets_and_collapsed_state_survive_restart_independently(self):
        notes = [{"id": "card", "title": "常用文本", "text": "中文\n第二行",
                  "collapsed": True, "height": 215}]
        self.workspace.save_notes(notes)
        preset = self.workspace.save_preset("办公", ["second", "first"])
        notes[0]["text"] = "修改内容"
        self.workspace.save_notes(notes)
        loaded = WorkspaceStore(self.directory)
        self.assertEqual(loaded.notes, notes)
        self.assertEqual(loaded.presets, [preset])
        self.assertTrue(self.directory.joinpath("workspace.backup.json").exists())
        self.assertFalse(self.directory.joinpath("entries.json").exists())

    def test_legacy_card_height_is_optional_and_new_height_is_validated(self):
        legacy = [{"id": "card", "title": "旧卡片", "text": "内容", "collapsed": False}]
        self.workspace.save_notes(legacy)
        self.assertEqual(WorkspaceStore(self.directory).notes, legacy)
        for height in (89, 481, 120.5, True):
            invalid = [{**legacy[0], "height": height}]
            with self.assertRaises(ValueError):
                self.workspace.save_notes(invalid)

    def test_clipboard_docks_flush_to_right_and_falls_back_to_left_near_screen_edge(self):
        work = (0, 0, 1920, 1040)
        self.assertEqual(dock_rect((100, 80, 1100, 800), 330, work), (1106, 80, 330, 720))
        self.assertEqual(dock_rect((920, 80, 1920, 800), 330, work), (584, 80, 330, 720))
        self.assertEqual(dock_rect((-1600, -1000, -600, -200), 330, (-1920, -1080, 0, 0)),
                         (-594, -1000, 330, 800))
        self.assertEqual(dock_rect((100, 80, 1600, 800), 500, work, min_width=300),
                         (1606, 80, 314, 720))

    def test_failed_save_preserves_disk_and_memory(self):
        self.workspace.save_preset("办公", ["first"])
        before, presets = self.workspace.path.read_bytes(), copy.deepcopy(self.workspace.presets)
        with patch("quick_access.workspace.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.workspace.save_preset("学习", ["second"])
        self.assertEqual(self.workspace.presets, presets)
        self.assertEqual(self.workspace.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_duplicate_names_and_empty_presets_are_rejected(self):
        self.workspace.save_preset("办公", ["first"])
        for name, ids in (("办公", ["second"]), ("", ["first"]), ("空预设", [])):
            with self.assertRaises(EntryError):
                self.workspace.save_preset(name, ids)

    def test_corrupt_workspace_is_reported_without_overwriting(self):
        self.workspace.path.write_text('{"version": 1, "notes": 1}', encoding="utf-8")
        before = self.workspace.path.read_bytes()
        with self.assertRaises(EntryError):
            WorkspaceStore(self.directory)
        self.assertEqual(self.workspace.path.read_bytes(), before)

    def test_numbering_respects_manual_order_and_excludes_unchecked_items(self):
        self.assertEqual(ordered_shortcuts([("a", True, "20"), ("b", False, ""), ("c", True, "1")]), ["c", "a"])
        for rows in ([("a", True, "1"), ("b", True, "1")], [("a", True, "0")], [("a", True, "x")], []):
            with self.assertRaises(EntryError):
                ordered_shortcuts(rows)

    def test_docking_setting_survives_card_and_preset_saves(self):
        self.workspace.set_edge_docking(False)
        self.workspace.save_preset("办公", ["first"])
        self.workspace.save_notes([])
        self.assertFalse(WorkspaceStore(self.directory).settings["edge_docking"])

    def test_ball_size_persists_with_other_settings_and_failed_save_rolls_back(self):
        self.assertEqual(self.workspace.settings["ball_size"], 44)
        self.workspace.set_ball_size(88)
        self.workspace.set_edge_docking(False)
        self.workspace.set_floating_ball(False)
        self.workspace.save_notes([])
        loaded = WorkspaceStore(self.directory)
        self.assertEqual(loaded.settings, {"ball_size": 88, "edge_docking": False,
                                          "floating_ball": False})
        with patch.object(self.workspace, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.workspace.set_ball_size(55)
        self.assertEqual(self.workspace.settings["ball_size"], 88)
        with patch.object(self.workspace, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.workspace.set_floating_ball(True)
        self.assertFalse(self.workspace.settings["floating_ball"])
        for invalid in (43, 89, True, 66.5):
            with self.assertRaises(EntryError):
                self.workspace.set_ball_size(invalid)

    def test_older_workspace_settings_default_floating_ball_to_enabled(self):
        self.workspace.path.write_text(
            '{"version": 1, "notes": [], "presets": [], "settings": {"edge_docking": false}}',
            encoding="utf-8")
        self.assertTrue(WorkspaceStore(self.directory).settings["floating_ball"])


class FeatureInterfaceTests(unittest.TestCase):
    def setUp(self):
        WorkspaceTests.setUp(self)
        self.store = EntryStore(self.directory / "data")
        self.shortcuts = []
        for name in ("浏览器.lnk", "编辑器.lnk", "终端.lnk"):
            path = self.directory / name
            path.write_bytes(b"test shortcut")
            self.shortcuts.append(self.store.save_entry(str(path)))
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = App(self.root, self.store)
        self.root.update()

    def tearDown(self):
        self.app.shortcut_launch.shutdown()
        self.root.destroy()
        WorkspaceTests.tearDown(self)
        self.assertEqual(self.errors, [])

    def test_card_add_edit_fold_copy_and_reopen(self):
        panel = self.app.open_clipboard()
        panel.add_button.invoke()
        self.root.update()
        ident = panel.records[0]["id"]
        card = panel.cards[ident]
        card["title"].set("常用命令")
        card["text"].insert("1.0", "中文文本\n第二行")
        panel.toggle_card(ident)
        self.root.update()
        self.assertFalse(card["content"].winfo_ismapped())
        with patch("quick_access.clipboard_panel.write_text") as clipboard:
            panel.copy_card(ident)
        clipboard.assert_called_once_with(panel, "中文文本\n第二行")
        self.assertIn("已复制", panel.status_var.get())
        panel.hide()
        self.assertEqual(panel.state(), "withdrawn")
        self.assertIs(self.app.open_clipboard(), panel)
        loaded = WorkspaceStore(self.store.directory)
        self.assertEqual(loaded.notes[0], {"id": ident, "title": "常用命令", "text": "中文文本\n第二行",
                                           "collapsed": True, "height": 150})

    def test_card_text_box_resizes_vertically_and_height_survives_restart(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        self.root.update()
        ident = panel.records[0]["id"]
        card = panel.cards[ident]
        start = card["content"].winfo_height()
        grip = card["grip"]
        panel._resize_start(SimpleNamespace(y_root=100, widget=grip), ident)
        panel._resize_motion(SimpleNamespace(y_root=190, widget=grip))
        self.root.update_idletasks()
        self.assertEqual(card["content"].winfo_height(), min(MAX_CARD_HEIGHT, start + 90))
        panel._resize_end(SimpleNamespace(widget=grip))
        loaded = WorkspaceStore(self.store.directory)
        self.assertEqual(loaded.notes[0]["height"], min(MAX_CARD_HEIGHT, start + 90))
        panel._resize_start(SimpleNamespace(y_root=200, widget=grip), ident)
        panel._resize_motion(SimpleNamespace(y_root=-500, widget=grip))
        panel._resize_end(SimpleNamespace(widget=grip))
        self.assertEqual(WorkspaceStore(self.store.directory).notes[0]["height"], MIN_CARD_HEIGHT)

    def test_clipboard_cards_drag_to_reorder_and_persist(self):
        panel = self.app.open_clipboard()
        for _ in range(3):
            panel.add_card()
            self.root.update()
        original = [item["id"] for item in panel.records]
        source = original[0]
        handle = panel.cards[source]["drag_handle"]
        start_y = handle.winfo_rooty() + 2
        last = panel.cards[original[-1]]["frame"]
        end_y = last.winfo_rooty() + last.winfo_height() + 10
        panel._drag_start(SimpleNamespace(y_root=start_y, widget=handle), source)
        panel._drag_motion(SimpleNamespace(y_root=end_y, widget=handle))
        self.assertIsNotNone(panel._drag_indicator)
        panel._drag_end(SimpleNamespace(y_root=end_y, widget=handle))
        expected = [original[1], original[2], original[0]]
        self.assertEqual([item["id"] for item in panel.records], expected)
        self.assertEqual([item["id"] for item in WorkspaceStore(self.store.directory).notes], expected)
        self.assertIn("顺序已保存", panel.status_var.get())

    def test_clipboard_has_a_prominent_scrollbar_and_plain_handle_click_does_not_reorder(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        self.root.update()
        ident = panel.records[0]["id"]
        handle = panel.cards[ident]["drag_handle"]
        panel._drag_start(SimpleNamespace(y_root=100, widget=handle), ident)
        panel._drag_end(SimpleNamespace(y_root=100, widget=handle))
        self.assertEqual([item["id"] for item in panel.records], [ident])
        self.assertGreaterEqual(int(panel.scroller.scrollbar.cget("width")), 16)

    def test_card_close_flushes_last_keystroke_without_waiting_for_autosave(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        panel.cards[panel.records[0]["id"]]["text"].insert("1.0", "最后输入")
        panel.hide()
        self.assertEqual(WorkspaceStore(self.store.directory).notes[0]["text"], "最后输入")

    def test_multiple_inline_images_and_text_survive_restart_and_copy(self):
        from PIL import Image
        from quick_access.clipboard_panel import ClipboardPanel
        panel = self.app.open_clipboard()
        panel.add_card()
        ident = panel.records[0]["id"]
        text = panel.cards[ident]["text"]
        text.insert("insert", "第一段\n")
        with patch("quick_access.clipboard_panel.ImageGrab.grabclipboard",
                   return_value=Image.new("RGB", (640, 480), "red")):
            self.assertEqual(panel._paste_event(None, ident), "break")
            text.insert("insert", "两张图片之间\n")
            self.assertEqual(panel._paste_event(None, ident), "break")
        self.assertEqual(len(panel.records), 1)
        self.assertEqual(len(text.image_names()), 2)
        saved = WorkspaceStore(self.store.directory).notes[0]
        self.assertEqual(sum("image" in part for part in saved["content"]), 2)
        self.assertIn("两张图片之间", saved["text"])
        panel.hide()
        panel.destroy()
        reopened = ClipboardPanel(self.root, WorkspaceStore(self.store.directory))
        self.assertEqual(len(reopened.cards[ident]["text"].image_names()), 2)
        self.assertEqual(reopened.cards[ident]["text"].cget("state"), "normal")
        with patch("quick_access.file_clipboard.write_rich_content") as writer:
            reopened.copy_card(ident)
        writer.assert_called_once()
        reopened.toggle_card(ident)
        self.assertTrue(WorkspaceStore(self.store.directory).notes[-1]["collapsed"])
        reopened.destroy()
        self.app.clipboard_panel = None

    def test_text_paste_passes_through_and_failed_save_keeps_pasted_image_in_editor(self):
        from PIL import Image
        panel = self.app.open_clipboard()
        panel.add_card()
        ident = panel.records[0]["id"]
        with patch("quick_access.clipboard_panel.ImageGrab.grabclipboard", return_value=None):
            self.assertIsNone(panel._paste_event(None, ident))
        with patch("quick_access.clipboard_panel.ImageGrab.grabclipboard",
                   return_value=Image.new("RGB", (10, 10))), patch.object(
                       panel.workspace, "save_notes", side_effect=OSError("disk full")):
            panel._paste_event(None, ident)
        self.assertEqual(len(panel.cards[ident]["text"].image_names()), 1)
        self.assertIn("保存失败", panel.status_var.get())
        self.assertNotIn("content", WorkspaceStore(self.store.directory).notes[0])
        self.assertTrue(panel.flush())
        self.assertIn("content", WorkspaceStore(self.store.directory).notes[0])

    def test_inline_image_deletion_persists_without_returning_on_reopen(self):
        from PIL import Image
        panel = self.app.open_clipboard()
        panel.add_card()
        ident = panel.records[0]["id"]
        with patch("quick_access.clipboard_panel.ImageGrab.grabclipboard",
                   return_value=Image.new("RGB", (10, 10))):
            panel._paste_event(None, ident)
        text = panel.cards[ident]["text"]
        text.delete("1.0", "1.1")
        panel.flush()
        self.assertNotIn("content", WorkspaceStore(self.store.directory).notes[0])

    def test_legacy_single_image_becomes_editable_and_virtual_paste_appends_second(self):
        import base64
        import io
        from PIL import Image
        from quick_access.clipboard_panel import ClipboardPanel
        stream = io.BytesIO()
        image = Image.new("RGB", (12, 12))
        image.save(stream, "PNG")
        self.app.workspace.save_notes([{"id": "legacy", "title": "旧图片", "text": "",
                                        "collapsed": False,
                                        "image": base64.b64encode(stream.getvalue()).decode()}])
        panel = self.app.open_clipboard()
        text = panel.cards["legacy"]["text"]
        text.mark_set("insert", "end-1c")
        with patch("quick_access.clipboard_panel.ImageGrab.grabclipboard", return_value=image):
            text.event_generate("<<Paste>>")
        panel.flush()
        saved = WorkspaceStore(self.store.directory).notes[0]
        self.assertNotIn("image", saved)
        self.assertEqual(sum("image" in part for part in saved["content"]), 2)

    def test_main_clipboard_button_toggles_open_and_close_in_place(self):
        button = self.app.clipboard_button
        self.assertEqual(button.cget("text"), "打开剪贴板")
        button.invoke()
        panel = self.app.clipboard_panel
        self.assertEqual(button.cget("text"), "关闭剪贴板")
        panel.add_card()
        panel.cards[panel.records[0]["id"]]["text"].insert("1.0", "保留卡片")

        button.invoke()
        self.assertEqual(panel.state(), "withdrawn")
        self.assertEqual(button.cget("text"), "打开剪贴板")
        self.assertEqual(WorkspaceStore(self.store.directory).notes[0]["text"], "保留卡片")

        button.invoke()
        self.assertIs(self.app.clipboard_panel, panel)
        self.assertEqual(panel.state(), "normal")
        self.assertEqual(button.cget("text"), "关闭剪贴板")

    def test_floating_ball_button_reflects_saved_state_and_calls_desktop_toggle(self):
        button = self.app.floating_ball_button
        self.assertEqual(button.cget("text"), "关闭悬浮球")
        self.assertTrue(button.instate(["disabled"]))
        desktop = SimpleNamespace(available=True, toggle_floating_ball=Mock())
        self.app.desktop = desktop
        self.app.update_floating_ball_button(False)
        self.assertEqual(button.cget("text"), "打开悬浮球")
        self.assertTrue(button.instate(["!disabled"]))
        button.invoke()
        desktop.toggle_floating_ball.assert_called_once_with()

    def test_clipboard_remains_visible_and_editable_after_main_is_withdrawn(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        self.root.update()
        self.assertEqual(panel.transient(), "")
        before = panel.geometry()
        self.root.withdraw()
        self.root.update()
        self.assertEqual(panel.state(), "normal")
        self.assertTrue(panel.winfo_ismapped())
        panel.dock()
        self.assertEqual(panel.geometry(), before)
        ident = panel.records[0]["id"]
        panel.cards[ident]["text"].insert("end", "主界面关闭后继续编辑")
        self.assertTrue(panel.flush())
        self.assertEqual(WorkspaceStore(self.store.directory).notes[0]["text"], "主界面关闭后继续编辑")

    def test_save_failure_keeps_card_window_and_content(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        panel.cards[panel.records[0]["id"]]["text"].insert("1.0", "保留草稿")
        with patch.object(panel.workspace, "save_notes", side_effect=OSError("disk full")):
            panel.hide()
        self.assertNotEqual(panel.state(), "withdrawn")
        self.assertEqual(self.app.clipboard_button.cget("text"), "关闭剪贴板")
        self.assertIn("保存失败", panel.status_var.get())
        self.assertEqual(panel.collect()[0]["text"], "保留草稿")

    def test_clipboard_contention_is_reported_without_losing_card_text(self):
        panel = self.app.open_clipboard()
        panel.add_card()
        ident = panel.records[0]["id"]
        panel.cards[ident]["text"].insert("1.0", "仍保存在卡片中")
        with patch("quick_access.clipboard_panel.write_text", side_effect=EntryError("系统剪贴板正忙")):
            panel.copy_card(ident)
        self.assertIn("系统剪贴板正忙", panel.status_var.get())
        self.assertEqual(panel.cards[ident]["text"].get("1.0", "end-1c"), "仍保存在卡片中")

    def select_sequence(self):
        controller = self.app.shortcut_launch
        controller.begin()
        for number, entry in enumerate(reversed(self.shortcuts), start=1):
            controller.rows[entry.id][0].set(True)
            controller.rows[entry.id][1].set(str(number))
        return controller

    def finish_opening(self, controller):
        while controller.launcher.running:
            if controller.launcher.job is not None:
                self.root.after_cancel(controller.launcher.job)
            controller.launcher._next()

    def test_header_numbering_and_open_without_saving_or_prompt(self):
        controller = self.select_sequence()
        self.root.update()
        self.assertTrue(controller.editor.winfo_ismapped())
        self.assertFalse(self.app.trees["shortcut"].winfo_ismapped())
        self.assertFalse(controller.save_var.get())
        with patch("quick_access.storage.os.startfile") as opener, patch("quick_access.launch_dialog.messagebox.askyesno") as prompt:
            controller.action_button.invoke()
            self.finish_opening(controller)
        self.assertEqual([call.args[0] for call in opener.call_args_list], [self.store.effective_path(entry) for entry in reversed(self.shortcuts)])
        prompt.assert_not_called()
        self.assertEqual(self.app.workspace.presets, [])
        self.assertIsNone(controller.editor)
        self.root.update()
        self.assertTrue(self.app.trees["shortcut"].winfo_ismapped())

    def test_checked_preset_persists_exact_order_and_adds_header_entry(self):
        controller = self.select_sequence()
        controller.save_var.set(True)
        controller.name_var.set("开发环境")
        with patch("quick_access.storage.os.startfile"):
            controller.confirm_open()
            self.finish_opening(controller)
        self.root.update()
        preset = WorkspaceStore(self.store.directory).presets[0]
        self.assertEqual(preset["entry_ids"], [entry.id for entry in reversed(self.shortcuts)])
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertEqual(controller.preset_menu.entrycget(0, "label"), "开发环境")
        with patch("quick_access.storage.os.startfile") as opener:
            controller.preset_menu.invoke(0)
            self.finish_opening(controller)
        self.assertEqual([call.args[0] for call in opener.call_args_list], [self.store.effective_path(entry) for entry in reversed(self.shortcuts)])

    def test_duplicate_order_and_save_failure_never_open_anything(self):
        controller = self.select_sequence()
        controller.rows[self.shortcuts[0].id][1].set("1")
        with patch("quick_access.storage.os.startfile") as opener:
            controller.confirm_open()
            self.root.update()
        opener.assert_not_called()
        self.assertIn("不能重复", self.app.status_var.get())
        controller.rows[self.shortcuts[0].id][1].set("3")
        controller.save_var.set(True)
        with patch.object(self.app.workspace, "save_preset", side_effect=OSError("disk full")), patch("quick_access.storage.os.startfile") as opener:
            controller.confirm_open()
            self.root.update()
        opener.assert_not_called()
        self.assertIsNotNone(controller.editor)
        self.assertFalse(controller.launcher.running)

    def test_removed_legacy_preset_member_is_recovered_from_saved_copy(self):
        preset = self.app.workspace.save_preset("开发", [entry.id for entry in self.shortcuts])
        self.store.remove(self.shortcuts[-1].id)
        with patch("quick_access.storage.os.startfile") as opener, patch("quick_access.launch_dialog.messagebox.showerror") as error:
            self.app.shortcut_launch.run_preset(preset)
            self.finish_opening(self.app.shortcut_launch)
        self.assertEqual(opener.call_count, 3)
        error.assert_not_called()

    def test_cancel_prevents_the_remaining_shortcuts_from_opening(self):
        done = Mock()
        launcher = SequentialLauncher(self.root, self.store, Mock(), done)
        with patch("quick_access.storage.os.startfile") as opener:
            launcher.start([entry.id for entry in self.shortcuts])
            self.root.update()
            self.assertEqual(opener.call_count, 1)
            launcher.cancel()
            self.root.update()
        self.assertFalse(launcher.running)
        self.assertIsNone(launcher.job)
        self.assertEqual(done.call_args.args[0], 1)

    def test_launch_failure_stops_sequence(self):
        controller = self.select_sequence()
        with patch("quick_access.storage.os.startfile", side_effect=OSError("unavailable")) as opener:
            controller.confirm_open()
            self.finish_opening(controller)
        self.assertEqual(opener.call_count, 1)
        self.assertIn("unavailable", self.app.status_var.get())

    def test_repeated_edit_cancel_removes_scroll_bindings(self):
        controller = self.app.shortcut_launch
        before = self.root.bind("<MouseWheel>")
        for _ in range(3):
            controller.begin()
            controller.cancel_edit()
        self.assertEqual(self.root.bind("<MouseWheel>"), before)

    def test_launch_header_controls_stay_visible_without_overlapping_title_after_resize_and_repaint(self):
        controller = self.app.shortcut_launch
        header = self.app.panel_headers["shortcut"]
        for geometry in ("1000x720", "720x520", "1280x800"):
            self.root.geometry(geometry)
            for color in ("#2676B8", "#EEF2F5"):
                header.configure(bg=color)
                self.root.update()
                self.assertTrue(controller.action_button.winfo_ismapped())
                self.assertTrue(controller.preset_button.winfo_ismapped())
                title_right = header.winfo_rootx() + header.winfo_width()
                self.assertLessEqual(title_right, controller.toolbar.winfo_rootx())
                panel = self.app.panels["shortcut"]
                controls_right = controller.toolbar.winfo_rootx() + controller.toolbar.winfo_width()
                self.assertLessEqual(controls_right, panel.winfo_rootx() + panel.winfo_width())

    def test_preset_entry_remains_visible_when_empty_created_or_removed(self):
        controller = self.app.shortcut_launch
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertTrue(controller.preset_button.instate(["!disabled"]))
        self.assertEqual(controller.preset_menu.entrycget(0, "label"), "新建预设…")
        preset = self.app.workspace.save_preset("办公", [self.shortcuts[0].id])
        controller.refresh_presets()
        self.root.update()
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertTrue(controller.preset_button.instate(["!disabled"]))
        self.app.workspace.remove_preset(preset["id"])
        controller.refresh_presets()
        self.root.update()
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertTrue(controller.preset_button.instate(["!disabled"]))

    def save_independent_preset(self):
        controller = self.select_sequence()
        controller.name_var.set("开发预设")
        with patch("quick_access.storage.os.startfile") as opener:
            controller.save_only_button.invoke()
            self.root.update()
        opener.assert_not_called()
        self.assertFalse(controller.launcher.running)
        return controller, self.app.workspace.presets[0]

    def test_save_only_can_create_and_reload_a_preset_without_opening_any_program(self):
        controller, preset = self.save_independent_preset()
        loaded = WorkspaceStore(self.store.directory)
        self.assertEqual(loaded.presets[0], preset)
        self.assertEqual(preset["entry_ids"], [entry.id for entry in reversed(self.shortcuts)])
        self.assertEqual(len(preset["launch_items"]), 3)
        self.assertIsNone(controller.editor)
        self.assertIn("未打开任何程序", self.app.status_var.get())

    def test_saved_preset_survives_removing_all_entries_and_desktop_shortcuts(self):
        controller, preset = self.save_independent_preset()
        expected = [self.store.effective_path(entry) for entry in reversed(self.shortcuts)]
        for entry in self.shortcuts:
            self.store.remove(entry.id)
            Path(entry.path).unlink()
        self.app.refresh()
        with patch("quick_access.storage.os.startfile") as opener, patch("quick_access.launch_dialog.messagebox.showerror") as error:
            controller.run_preset(preset)
            self.finish_opening(controller)
        self.assertEqual([call.args[0] for call in opener.call_args_list], expected)
        error.assert_not_called()
        self.assertEqual(self.store.entries, [])
        self.assertEqual(WorkspaceStore(self.store.directory).presets[0], preset)

    def test_missing_preset_assets_are_skipped_without_blocking_the_rest(self):
        controller, preset = self.save_independent_preset()
        Path(self.store.effective_path(self.shortcuts[-1])).unlink()
        Path(self.shortcuts[-1].path).unlink()
        with patch("quick_access.storage.os.startfile") as opener, patch("quick_access.launch_dialog.messagebox.showerror") as error:
            controller.run_preset(preset)
            self.finish_opening(controller)
        self.assertEqual(opener.call_count, 2)
        error.assert_not_called()
        self.assertIn("已跳过 1 个", self.app.status_var.get())
        for entry in self.shortcuts[:-1]:
            Path(self.store.effective_path(entry)).unlink()
        with patch("quick_access.storage.os.startfile") as opener, patch("quick_access.launch_dialog.messagebox.showerror") as error:
            controller.run_preset(preset)
            self.finish_opening(controller)
        opener.assert_not_called()
        error.assert_not_called()
        self.assertIn("已打开 0", self.app.status_var.get())

    def test_saved_preset_can_be_edited_repeatedly_without_duplication_or_launching(self):
        controller, preset = self.save_independent_preset()
        self.store.remove(self.shortcuts[0].id)
        copies_before = len(list(self.store.directory.rglob("*.lnk")))
        with patch("quick_access.storage.os.startfile") as opener:
            for index in range(8):
                controller.begin(self.app.workspace.presets[0])
                controller.name_var.set(f"修改后的预设 {index}")
                controller.save_only()
        opener.assert_not_called()
        self.assertEqual(len(self.app.workspace.presets), 1)
        self.assertEqual(self.app.workspace.presets[0]["id"], preset["id"])
        self.assertEqual(len(list(self.store.directory.rglob("*.lnk"))), copies_before)

    def test_editing_original_shortcut_does_not_change_an_existing_preset_target(self):
        controller, preset = self.save_independent_preset()
        original_paths = [item["path"] for item in self.app.workspace.launch_items(preset)]
        Path(self.shortcuts[-1].path).write_bytes(b"changed launch arguments")
        self.store.repair_shortcut(self.shortcuts[-1].id, self.shortcuts[-1].path)
        self.assertNotEqual(self.store.effective_path(self.store.get(self.shortcuts[-1].id)), original_paths[0])
        with patch("quick_access.storage.os.startfile") as opener:
            controller.run_preset(preset)
            self.finish_opening(controller)
        self.assertEqual([call.args[0] for call in opener.call_args_list], original_paths)

    def test_editing_controls_and_selection_survive_refresh_and_panel_layout_changes(self):
        controller = self.select_sequence()
        expected = [(ident, enabled.get(), number.get()) for ident, (enabled, number) in controller.rows.items()]
        self.app.refresh()
        order = list(reversed(self.store.panel_order))
        self.store.save_panel_order(order)
        self.app.apply_panel_layout()
        self.root.update()
        self.assertTrue(controller.action_button.winfo_ismapped())
        self.assertTrue(controller.cancel_button.winfo_ismapped())
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertTrue(controller.editor.winfo_ismapped())
        self.assertEqual([(ident, enabled.get(), number.get()) for ident, (enabled, number) in controller.rows.items()], expected)
        controller.cancel_edit()
        self.root.update()
        self.assertTrue(controller.action_button.winfo_ismapped())
        self.assertTrue(controller.preset_button.winfo_ismapped())
        self.assertTrue(self.app.trees["shortcut"].winfo_ismapped())
