"""Actual file edits are tested exclusively on disposable workspace fixtures."""

import os
import struct
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quick_access.app import App
from quick_access.copying import copy_files
from quick_access.deletion import inspect_target
from quick_access.file_clipboard import hdrop_payload
from quick_access.rename_dialog import RenameDialog
from quick_access.storage import EntryError, EntryStore


class FileActionFixture:
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.file = self.directory / "合同.txt"
        self.file.write_text("original content", encoding="utf-8")
        self.folder = self.directory / "资料.v1"
        self.folder.mkdir()
        self.store = EntryStore(self.directory / "data")
        self.entry = self.store.save_entry(str(self.file))
        self.folder_entry = self.store.save_entry(str(self.folder))

    def tearDown(self):
        self.temporary.cleanup()


class FileActionTests(FileActionFixture, unittest.TestCase):
    def test_actual_rename_preserves_content_and_persisted_identity_and_order(self):
        self.store.reorder("file", [self.entry.id])
        before = self.store.get(self.entry.id)
        target = self.store.prepare_source_rename(self.entry.id)
        self.assertEqual(self.store.rename_source(self.entry.id, "新合同.txt", target), [self.entry.id])
        destination = self.directory / "新合同.txt"
        self.assertFalse(self.file.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "original content")
        loaded = EntryStore(self.store.directory).get(self.entry.id)
        self.assertEqual(loaded.path, str(destination))
        self.assertEqual(loaded.title, destination.name)
        self.assertEqual(loaded.position, before.position)
        self.assertEqual(loaded.created_at, before.created_at)
        self.assertEqual(self.store.get(self.folder_entry.id), self.folder_entry)

    def test_folder_rename_cannot_be_bypassed_by_direct_execution(self):
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(EntryError, "不允许重命名原文件夹"):
            self.store.prepare_source_rename(self.folder_entry.id)
        with self.assertRaisesRegex(EntryError, "不允许重命名原文件夹"):
            self.store.rename_source(self.folder_entry.id, "不允许的改名", inspect_target(str(self.folder)))
        self.assertTrue(self.folder.exists())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_existing_name_never_overwrites_other_file(self):
        destination = self.directory / "已有.txt"
        destination.write_text("other content", encoding="utf-8")
        with self.assertRaisesRegex(EntryError, "已存在这个名称"):
            self.store.rename_source(self.entry.id, destination.name)
        self.assertEqual(destination.read_text(encoding="utf-8"), "other content")
        self.assertTrue(self.file.exists())

    def test_invalid_names_cannot_change_source_or_record(self):
        before = self.store.path.read_bytes()
        for name in ("", " ", ".", "..", "../escape.txt", "子/文件.txt", "子\\文件.txt",
                     'bad:name.txt', 'bad?.txt', 'bad*.txt', 'bad".txt', "bad<.txt", "bad>.txt",
                     "bad|.txt", "bad\n.txt", "bad\x00.txt", "结尾.", "结尾 ",
                     "CON.txt", "com1.log", "LPT².txt", "NUL", "x" * 256):
            with self.subTest(name=name), self.assertRaises(EntryError):
                self.store.rename_source(self.entry.id, name)
        self.assertTrue(self.file.exists())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_target_changed_while_input_open_is_rejected(self):
        target = self.store.prepare_source_rename(self.entry.id)
        self.file.write_text("different content", encoding="utf-8")
        with self.assertRaisesRegex(EntryError, "已发生变化"):
            self.store.rename_source(self.entry.id, "new.txt", target)
        self.assertTrue(self.file.exists())

    def test_case_only_rename_and_unchanged_name(self):
        source = self.directory / "Report.txt"
        source.write_text("case fixture", encoding="utf-8")
        entry = self.store.save_entry(str(source))
        self.store.rename_source(entry.id, "REPORT.txt")
        self.assertIn("REPORT.txt", os.listdir(self.directory))
        self.assertNotIn("Report.txt", os.listdir(self.directory))
        with patch("quick_access.storage.os.rename") as rename:
            self.assertEqual(self.store.rename_source(entry.id, "REPORT.txt"), [])
        rename.assert_not_called()

    def test_failed_file_operation_preserves_source_and_record(self):
        before = self.store.path.read_bytes()
        with patch("quick_access.storage.os.rename", side_effect=OSError("locked file")):
            with self.assertRaises(OSError):
                self.store.rename_source(self.entry.id, "new.txt")
        self.assertTrue(self.file.exists())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_record_save_rolls_back_filename(self):
        before = self.store.path.read_bytes()
        with patch("quick_access.storage.os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(EntryError, "已恢复原文件名称"):
                self.store.rename_source(self.entry.id, "new.txt")
        self.assertTrue(self.file.exists())
        self.assertFalse((self.directory / "new.txt").exists())
        self.assertEqual(self.store.get(self.entry.id), self.entry)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_copy_preserves_source_and_existing_names_and_handles_multiple_files(self):
        existing = self.folder / self.file.name
        existing.write_text("existing destination", encoding="utf-8")
        other = self.directory / "另一个.pdf"
        other.write_bytes(b"pdf fixture")
        result = copy_files([str(self.file), str(other)], str(self.folder))
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.paths), 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing destination")
        self.assertEqual((self.folder / "合同 - 副本.txt").read_text(encoding="utf-8"), "original content")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "original content")
        second = copy_files([str(self.file)], str(self.folder))
        self.assertEqual(Path(second.paths[0]).name, "合同 - 副本 (2).txt")
        self.assertEqual(list(self.folder.glob(".文件复制-*")), [])

    def test_copy_to_same_parent_creates_a_copy(self):
        result = copy_files([str(self.file)], str(self.directory))
        self.assertEqual(result.errors, [])
        self.assertEqual(Path(result.paths[0]).name, "合同 - 副本.txt")
        self.assertTrue(self.file.exists())

    def test_folder_copy_and_missing_file_are_handled_independently(self):
        child = self.folder / "子文件夹" / "内容.txt"
        child.parent.mkdir()
        child.write_text("nested original", encoding="utf-8")
        result = copy_files([str(self.folder), str(self.directory / "missing.txt")], str(self.directory))
        self.assertEqual(len(result.paths), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(Path(result.paths[0]).name, "资料.v1 - 副本")
        self.assertEqual((Path(result.paths[0]) / "子文件夹" / "内容.txt").read_text(encoding="utf-8"), "nested original")
        self.assertEqual(child.read_text(encoding="utf-8"), "nested original")
        self.assertTrue(self.folder.exists())

    def test_folder_cannot_be_copied_into_itself_or_its_children(self):
        child = self.folder / "子目录"
        child.mkdir()
        for destination in (self.folder, child):
            result = copy_files([str(self.folder)], str(destination))
            self.assertEqual(result.paths, [])
            self.assertIn("不能将文件夹复制到自身", result.errors[0])
            self.assertEqual(list(destination.glob(".文件夹复制-*")), [])

    def test_copy_failure_cleans_partial_temporary_file(self):
        with patch("quick_access.copying.shutil.copy2", side_effect=OSError("disk full")):
            result = copy_files([str(self.file)], str(self.folder))
        self.assertEqual(result.paths, [])
        self.assertIn("disk full", result.errors[0])
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertTrue(self.file.exists())

    def test_copy_destination_must_be_folder(self):
        with self.assertRaises(EntryError):
            copy_files([str(self.file)], str(self.file))

    def test_parent_open_for_file_folder_missing_file_and_root(self):
        opened = []
        self.store.open_parent(self.entry.id, opened.append)
        self.store.open_parent(self.folder_entry.id, opened.append)
        self.assertEqual(opened, [str(self.directory), str(self.directory)])
        self.file.rename(self.directory / "moved.txt")
        self.store.open_parent(self.entry.id, opened.append)
        self.assertEqual(opened[-1], str(self.directory))
        root = self.store.save_entry(self.directory.anchor)
        with self.assertRaisesRegex(EntryError, "根目录"):
            self.store.open_parent(root.id, opened.append)

    def test_file_clipboard_payload_is_unicode_and_double_terminated(self):
        paths = [str(self.file), str(self.directory / "带 空格.pdf")]
        payload = hdrop_payload(paths)
        self.assertEqual(struct.unpack("<IiiII", payload[:20]), (20, 0, 0, 0, 1))
        self.assertEqual(payload[20:].decode("utf-16-le"), "\0".join(paths) + "\0\0")


class FileActionInterfaceTests(FileActionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.app = App(self.root, self.store)
        self.root.update()
        self.app.trees["file"].selection_set(self.entry.id)

    def tearDown(self):
        if self.app._copy_poll_job:
            self.root.after_cancel(self.app._copy_poll_job)
        if self.app._copy_executor:
            self.app._copy_executor.shutdown(wait=True)
        self.app.cancel_drag()
        self.root.destroy()
        super().tearDown()

    def test_rename_dialog_preserves_extension_and_cancel_never_invokes_backend(self):
        target = self.store.prepare_source_rename(self.entry.id)
        rename = Mock(return_value=[self.entry.id])
        dialog = RenameDialog(self.root, target, rename)
        self.assertEqual(dialog.name_var.get(), "合同")
        self.assertEqual(dialog.suffix, ".txt")
        dialog.name_var.set("新合同")
        dialog.save_button.invoke()
        rename.assert_called_once_with("新合同.txt")
        cancelled = RenameDialog(self.root, target, rename)
        cancelled.cancel_button.invoke()
        self.assertEqual(rename.call_count, 1)
        self.assertTrue(self.file.exists())

    def test_rename_dialog_errors_stay_open(self):
        target = self.store.prepare_source_rename(self.entry.id)
        rename = Mock(side_effect=EntryError("同名文件"))
        dialog = RenameDialog(self.root, target, rename)
        dialog.name_var.set("")
        dialog.save_button.invoke()
        rename.assert_not_called()
        dialog.name_var.set("已有")
        dialog.save_button.invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertIn("同名文件", dialog.error_var.get())
        dialog.cancel()

    def test_actual_rename_refreshes_and_opens_new_file(self):
        def finish(dialog):
            dialog.name_var.set("界面改名")
            dialog.save_button.invoke()

        with patch.object(self.root, "wait_window", side_effect=finish):
            self.app.edit_name_selected()
        destination = self.directory / "界面改名.txt"
        self.assertFalse(self.file.exists())
        self.assertTrue(destination.exists())
        self.assertEqual(self.app.trees["file"].item(self.entry.id, "text"), destination.name)
        with patch("quick_access.storage.os.startfile") as opener:
            self.app.open_selected()
        opener.assert_called_once_with(str(destination))

    def test_folder_display_name_changes_only_the_surface_and_can_restore(self):
        self.app.trees["file"].selection_remove(self.entry.id)
        self.app.trees["folder"].selection_set(self.folder_entry.id)
        self.app.edit_name_selected()
        self.root.update()
        self.assertTrue(self.app.name_input.winfo_ismapped())
        self.assertFalse(self.app.path_input.winfo_ismapped())
        self.app.name_var.set("自定义资料名称")
        self.app.submit()
        self.assertTrue(self.folder.exists())
        self.assertEqual(self.store.get(self.folder_entry.id).path, str(self.folder))
        self.assertEqual(self.app.trees["folder"].item(self.folder_entry.id, "text"), "自定义资料名称")
        self.app.trees["folder"].selection_set(self.folder_entry.id)
        self.app.edit_name_selected()
        self.app.name_var.set("")
        self.app.submit()
        self.assertEqual(self.store.get(self.folder_entry.id).title, self.folder.name)

    def test_file_alias_is_not_shown_and_add_form_has_only_path(self):
        self.store.save_entry(str(self.file), "旧的文件别名", entry_id=self.entry.id)
        self.app.refresh()
        self.assertEqual(self.app.trees["file"].item(self.entry.id, "text"), self.file.name)
        self.app.begin_add()
        self.root.update()
        self.assertTrue(self.app.path_input.winfo_ismapped())
        self.assertFalse(self.app.name_input.winfo_ismapped())

    def test_folder_menu_never_offers_actual_folder_edits(self):
        self.app._configure_entry_menu("folder")
        labels = [self.app.entry_menu.entrycget(i, "label") for i in range(self.app.entry_menu.index("end") + 1)
                  if self.app.entry_menu.type(i) != "separator"]
        self.assertIn("修改显示名称…", labels)
        self.assertIn("打开文件所在位置", labels)
        self.assertIn("复制文件夹 (Ctrl+C)", labels)
        self.assertFalse(any("删除原" in label or "重命名原" in label for label in labels))

    def test_file_copy_and_folder_paste_work_and_add_the_new_shortcut(self):
        with patch("quick_access.app.write_files") as clipboard:
            self.app.copy_selected()
        clipboard.assert_called_once_with(self.root, [str(self.file)])
        self.app.trees["file"].selection_remove(self.entry.id)
        self.app.trees["folder"].selection_set(self.folder_entry.id)
        with patch("quick_access.app.read_files", return_value=[str(self.file)]):
            self.app.paste_files_selected()
        deadline = time.monotonic() + 5
        while self.app._copy_future is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertIsNone(self.app._copy_future)
        copied = self.folder / self.file.name
        self.assertEqual(copied.read_text(encoding="utf-8"), "original content")
        self.assertTrue(self.file.exists())
        self.assertIn(str(copied), [entry.path for entry in self.store.entries])

    def test_folder_copy_is_available_alongside_internal_reordering(self):
        self.app.trees["file"].selection_remove(self.entry.id)
        self.app.trees["folder"].selection_set(self.folder_entry.id)
        with patch("quick_access.app.write_files") as clipboard:
            self.app.copy_selected()
        clipboard.assert_called_once_with(self.root, [str(self.folder)])
        self.assertTrue(self.app.trees["folder"].bind("<B1-Motion>"))
        self.app._configure_entry_menu("folder")
        labels = [self.app.entry_menu.entrycget(i, "label")
                  for i in range(self.app.entry_menu.index("end") + 1)
                  if self.app.entry_menu.type(i) != "separator"]
        self.assertIn("复制文件夹 (Ctrl+C)", labels)

    def test_parent_action_uses_parent_for_both_lists(self):
        with patch("quick_access.storage.os.startfile") as opener:
            self.app.open_parent_selected()
            self.app.trees["file"].selection_remove(self.entry.id)
            self.app.trees["folder"].selection_set(self.folder_entry.id)
            self.app.open_parent_selected()
        self.assertEqual([call.args[0] for call in opener.call_args_list], [str(self.directory)] * 2)


if __name__ == "__main__":
    unittest.main()
