"""Drag/drop keeps entry paths tied to the original objects, never to copies."""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quick_access.app import App
from quick_access.dragdrop import NativeDragDrop
from quick_access.storage import EntryStore


class NativeDragDropTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.file = self.directory / "中文 {带括号} 文件.txt"
        self.file.write_text("unchanged original", encoding="utf-8")
        self.folder = self.directory / "中文 文件夹"
        self.folder.mkdir()
        (self.folder / "子文件.txt").write_text("child", encoding="utf-8")
        self.store = EntryStore(self.directory / "data")
        self.root = tk.Tk()
        self.app = App(self.root, self.store)
        self.root.update()

    def tearDown(self):
        self.app.cancel_drag()
        self.root.destroy()
        self.temporary.cleanup()

    def test_native_drop_parses_spaces_braces_unicode_and_saves_original_paths(self):
        paths = (str(self.file), str(self.folder))
        payload = self.root.tk.call("format", "%s", paths)
        self.assertEqual(self.app.native_dnd.drop(payload), "link")
        self.assertEqual({entry.path for entry in self.store.entries}, set(paths))
        self.assertEqual({entry.kind for entry in self.store.entries}, {"file", "folder"})
        self.assertEqual({entry.path for entry in EntryStore(self.store.directory).entries}, set(paths))
        self.assertEqual(self.file.read_text(encoding="utf-8"), "unchanged original")
        self.assertTrue((self.folder / "子文件.txt").exists())

    def test_duplicate_drop_preserves_folder_alias_and_order(self):
        entry = self.store.save_entry(str(self.folder), "表面名称")
        self.store.reorder("folder", [entry.id])
        before = self.store.path.read_bytes()
        payload = self.root.tk.call("format", "%s", (str(self.folder), str(self.folder)))
        self.assertEqual(self.app.native_dnd.drop(payload), "link")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.get(entry.id).title, "表面名称")

    def test_missing_targets_are_not_added_and_partial_success_is_reported(self):
        self.assertTrue(self.app.add_dropped_paths((str(self.file), str(self.directory / "missing.txt"))))
        self.assertEqual(len(self.store.entries), 1)
        self.assertIn("1 个未添加", self.app.status_var.get())
        self.assertEqual(self.store.entries[0].path, str(self.file))

    def test_empty_drop_is_refused_and_drop_onto_our_export_is_refused(self):
        self.assertEqual(self.app.native_dnd.drop(""), "refuse_drop")
        self.app.native_dnd.exporting = True
        self.assertEqual(self.app.native_dnd.accept(), "refuse_drop")
        self.assertEqual(self.app.native_dnd.drop(str(self.file)), "refuse_drop")
        self.assertEqual(self.store.entries, [])

    def test_drop_targets_include_lists_inputs_and_window_background(self):
        self.assertTrue(self.root.tk.call("info", "commands", "_DoDragDrop"))
        for widget in (self.root, self.app.path_input, self.app.trees["file"], self.app.trees["folder"]):
            self.assertTrue(self.root.tk.call("bind", widget._w, "<<Drop>>"))
        self.assertEqual(self.app.native_dnd.accept(), "link")

    def test_drag_out_only_exports_files_and_never_reorders_or_changes_any_stored_path(self):
        shortcut = self.directory / "应用.lnk"
        shortcut.write_bytes(b"shortcut fixture")
        self.app.add_dropped_paths((str(self.file), str(self.folder), str(shortcut), "https://example.com"))
        self.root.update()
        before = self.store.path.read_bytes()
        for kind in ("file", "folder", "shortcut", "web"):
            tree = self.app.trees[kind]
            entry_id = tree.get_children()[0]
            x, y, width, height = tree.bbox(entry_id)
            self.app._tree_clicked(SimpleNamespace(widget=tree, x=30, y=y + 2))
            with patch.object(self.app.native_dnd, "export", return_value="copy") as export:
                with patch.object(self.store, "reorder") as reorder:
                    self.app._drag_motion(SimpleNamespace(widget=tree, x=self.root.winfo_width() * 2, y=y + 2))
            if kind == "file":
                export.assert_called_once_with(tree, self.store.get(entry_id).path)
            else:
                export.assert_not_called()
                self.assertTrue(tree.bind("<B1-Motion>"))
            reorder.assert_not_called()
            self.assertIsNone(self.app._drag_state)
            self.assertEqual(self.store.path.read_bytes(), before)
            if kind != "web":
                self.assertTrue(Path(self.store.get(entry_id).path).exists())

    def test_folder_export_is_refused_even_if_called_directly(self):
        fake_root = SimpleNamespace(tk=Mock())
        with patch.object(self.app.native_dnd, "root", fake_root):
            result = self.app.native_dnd.export(self.app.trees["folder"], str(self.folder))
        self.assertEqual(result, "refuse_drop")
        fake_root.tk.call.assert_not_called()

    def test_path_menu_is_gone_for_files_and_folders(self):
        for kind in ("file", "folder"):
            self.app._configure_entry_menu(kind)
            labels = [self.app.entry_menu.entrycget(i, "label")
                      for i in range(self.app.entry_menu.index("end") + 1)
                      if self.app.entry_menu.type(i) != "separator"]
            self.assertNotIn("修改路径", labels)


class ExportProtocolTests(unittest.TestCase):
    def test_export_offers_copy_only_and_cannot_accept_its_own_drop(self):
        drag = NativeDragDrop.__new__(NativeDragDrop)
        drag.root = SimpleNamespace(tk=Mock())
        drag.exporting = False
        drag.on_drop = Mock()
        widget = SimpleNamespace(_w=".file_list")
        path = "D:\\原目录\\文件.txt"

        def transfer(*args):
            self.assertEqual(drag.accept(), "refuse_drop")
            self.assertEqual(drag.drop(path), "refuse_drop")
            return "copy"

        drag.root.tk.call.side_effect = transfer
        self.assertEqual(drag.export(widget, path), "copy")
        drag.root.tk.call.assert_called_once_with("_DoDragDrop", ".file_list", ("copy",),
                                                 ("CF_HDROP",), ((path,),), 1)
        drag.on_drop.assert_not_called()
        self.assertFalse(drag.exporting)

    def test_export_error_always_clears_the_active_flag(self):
        drag = NativeDragDrop.__new__(NativeDragDrop)
        drag.root = SimpleNamespace(tk=Mock())
        drag.exporting = False
        drag.root.tk.call.side_effect = tk.TclError("cancelled")
        with self.assertRaises(tk.TclError):
            drag.export(SimpleNamespace(_w=".list"), "D:\\文件.txt")
        self.assertFalse(drag.exporting)


if __name__ == "__main__":
    unittest.main()
