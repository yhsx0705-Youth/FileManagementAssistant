"""Deletion safeguards use isolated fixtures; no user targets are operated on."""

import ctypes
import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quick_access.app import App
from quick_access.delete_dialog import DeleteDialog
from quick_access.deletion import _RecycleGuard, check_target_unchanged, inspect_target
from quick_access.storage import EntryError, EntryStore


class DeletionTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.file = self.directory / "测试文档.txt"
        self.file.write_text("fixture", encoding="utf-8")
        self.folder = self.directory / "测试文件夹"
        self.folder.mkdir()
        self.store = EntryStore(self.directory / "data")
        self.entry = self.store.save_entry(str(self.file))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def fake_recycle(path):
        # Keep the fixture intact in a renamed location, rather than permanently delete.
        source = Path(path)
        source.rename(source.with_name(source.name + ".recycled"))

    def test_file_and_folder_are_identified_from_disk(self):
        self.assertEqual(inspect_target(str(self.file)).kind_label, "文件")
        self.assertEqual(inspect_target(str(self.folder)).kind_label, "文件夹")

    def test_roots_and_program_or_data_ancestors_are_rejected(self):
        with self.assertRaisesRegex(EntryError, "根目录"):
            inspect_target(self.directory.anchor)
        for target in (self.directory, self.store.directory):
            self.store.directory.mkdir(exist_ok=True)
            with self.subTest(target=target), self.assertRaisesRegex(EntryError, "程序目录"):
                inspect_target(str(target), (self.store.directory,))
        # A normal file alongside the data remains eligible.
        self.assertEqual(self.store.prepare_source_deletion(self.entry.id).path, str(self.file))

    def test_changed_target_is_rejected_before_recycler(self):
        target = self.store.prepare_source_deletion(self.entry.id)
        self.file.write_text("changed content", encoding="utf-8")
        recycler = Mock()
        with self.assertRaisesRegex(EntryError, "已发生变化"):
            self.store.delete_source(self.entry.id, target, recycler)
        recycler.assert_not_called()
        self.assertTrue(self.file.exists())

    def test_replaced_target_is_rejected(self):
        target = inspect_target(str(self.file))
        self.file.rename(self.file.with_suffix(".old"))
        self.file.write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(EntryError, "已发生变化"):
            check_target_unchanged(target)

    def test_missing_target_after_confirmation_finishes_entry_cleanup(self):
        target = self.store.prepare_source_deletion(self.entry.id)
        self.fake_recycle(str(self.file))
        recycler = Mock()
        self.assertEqual(self.store.delete_source(self.entry.id, target, recycler), [self.entry.id])
        recycler.assert_not_called()
        self.assertEqual(self.store.entries, [])
        self.assertEqual(EntryStore(self.store.directory).entries, [])

    def test_changed_entry_path_is_rejected(self):
        target = self.store.prepare_source_deletion(self.entry.id)
        self.store.save_entry(str(self.folder), entry_id=self.entry.id)
        recycler = Mock()
        with self.assertRaisesRegex(EntryError, "入口路径已变化"):
            self.store.delete_source(self.entry.id, target, recycler)
        recycler.assert_not_called()
        self.assertTrue(self.file.exists())

    def test_recycle_failure_preserves_memory_disk_and_source(self):
        before = self.store.path.read_bytes()
        target = self.store.prepare_source_deletion(self.entry.id)
        with self.assertRaises(OSError):
            self.store.delete_source(self.entry.id, target, Mock(side_effect=OSError("recycle failure")))
        self.assertTrue(self.file.exists())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.get(self.entry.id), self.entry)

    def test_noop_recycler_cannot_remove_entry(self):
        target = self.store.prepare_source_deletion(self.entry.id)
        with patch("quick_access.deletion.notify_shell_deleted") as notify:
            with self.assertRaisesRegex(EntryError, "未完成删除"):
                self.store.delete_source(self.entry.id, target, lambda _: None)
        notify.assert_not_called()
        self.assertTrue(self.file.exists())
        self.assertEqual(self.store.get(self.entry.id), self.entry)

    def test_success_removes_only_selected_file_entry_and_persists(self):
        folder = self.store.save_entry(str(self.folder))
        target = self.store.prepare_source_deletion(self.entry.id)
        with patch("quick_access.deletion.notify_shell_deleted") as notify:
            self.assertEqual(self.store.delete_source(self.entry.id, target, self.fake_recycle),
                             [self.entry.id])
        notify.assert_called_once_with(str(self.file))
        self.assertEqual(EntryStore(self.store.directory).entries, [folder])
        self.assertTrue(self.folder.exists())

    def test_folder_deletion_is_blocked_in_preparation_and_execution(self):
        child = self.folder / "子文件.txt"
        child.write_text("child", encoding="utf-8")
        sibling = self.directory / (self.folder.name + "-其他")
        sibling.mkdir()
        parent_entry = self.store.save_entry(str(self.folder))
        child_entry = self.store.save_entry(str(child))
        sibling_entry = self.store.save_entry(str(sibling))
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(EntryError, "不允许删除原文件夹"):
            self.store.prepare_source_deletion(parent_entry.id)
        recycler = Mock()
        with self.assertRaisesRegex(EntryError, "不允许删除原文件夹"):
            self.store.delete_source(parent_entry.id, inspect_target(str(self.folder)), recycler)
        recycler.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertTrue(child.exists())
        self.assertTrue(sibling.exists())
        self.assertTrue(self.file.exists())

    def test_record_save_failure_reports_target_was_already_recycled(self):
        before = self.store.path.read_bytes()
        target = self.store.prepare_source_deletion(self.entry.id)
        with patch.object(self.store, "_commit", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(EntryError, "入口记录保存失败"):
                self.store.delete_source(self.entry.id, target, self.fake_recycle)
        self.assertFalse(self.file.exists())
        self.assertEqual(self.store.entries, [])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_native_error_after_source_disappeared_does_not_leave_an_orphan(self):
        target = self.store.prepare_source_deletion(self.entry.id)

        def completed_but_reported_error(path):
            self.fake_recycle(path)
            raise OSError("source no longer exists")

        self.store.delete_source(self.entry.id, target, completed_but_reported_error)
        self.assertEqual(self.store.entries, [])
        self.assertEqual(EntryStore(self.store.directory).entries, [])

    def test_permission_error_is_not_mistaken_for_a_missing_file(self):
        target = self.store.prepare_source_deletion(self.entry.id)
        with patch("quick_access.deletion.os.lstat", side_effect=PermissionError("access denied")):
            with self.assertRaises(PermissionError):
                self.store.delete_source(self.entry.id, target, Mock())
        self.assertEqual(self.store.get(self.entry.id), self.entry)
        self.assertTrue(self.file.exists())

    @unittest.skipUnless(os.name == "nt", "Windows COM sink")
    def test_native_guard_vetoes_permanent_delete_before_operation(self):
        guard = _RecycleGuard()
        predelete = guard.callbacks[11]
        self.assertEqual(predelete(None, 0x80, None), 0)
        self.assertFalse(guard.blocked)
        self.assertLess(predelete(None, 0, None), 0)
        self.assertTrue(guard.blocked)


class DeletionInterfaceTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.file = self.directory / "待删除测试文件.txt"
        self.file.write_text("fixture", encoding="utf-8")
        self.store = EntryStore(self.directory / "data")
        self.entry = self.store.save_entry(str(self.file), "测试入口")
        self.root = tk.Tk()
        self.app = App(self.root, self.store)
        self.root.update()
        self.app.trees["file"].selection_set(self.entry.id)
        self.target = self.store.prepare_source_deletion(self.entry.id)

    def tearDown(self):
        self.app.cancel_drag()
        self.root.destroy()
        self.temporary.cleanup()

    def test_dialog_requires_three_click_confirmations_and_no_typed_word(self):
        dialog = DeleteDialog(self.root, self.target, self.entry.title)
        dialog.confirm()
        self.assertTrue(dialog.winfo_exists())
        self.assertFalse(dialog.confirmed)
        dialog.confirm_button.invoke()
        self.assertEqual(dialog.stage, 2)
        self.assertIn("2/3", dialog.heading.cget("text"))
        dialog.confirm()
        self.assertFalse(dialog.confirmed)
        self.assertTrue(dialog.winfo_exists())
        dialog.confirm_button.invoke()
        self.assertEqual(dialog.stage, 3)
        self.assertIn("3/3", dialog.heading.cget("text"))
        self.assertFalse(dialog.confirmed)
        self.assertFalse(hasattr(dialog, "word_input"))
        dialog.confirm_button.invoke()
        self.assertTrue(dialog.confirmed)
        # Only dialog state was accepted; no deletion backend was invoked.
        self.assertTrue(self.file.exists())

    def test_cancelling_any_stage_never_confirms(self):
        for stage in (1, 2, 3):
            dialog = DeleteDialog(self.root, self.target, self.entry.title)
            for _ in range(stage - 1):
                dialog.advance()
            dialog.cancel_button.invoke()
            self.assertFalse(dialog.confirmed)
            self.assertTrue(self.file.exists())

    def test_cancelled_app_confirmation_never_invokes_backend(self):
        before = self.store.path.read_bytes()
        with patch("quick_access.app.confirm_source_deletion", return_value=False) as confirm:
            with patch.object(self.store, "delete_source") as delete:
                self.app.delete_source_selected()
        confirm.assert_called_once_with(self.root, self.target, self.entry.title)
        delete.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertTrue(self.file.exists())

    def test_confirmed_app_deletion_refreshes_list_and_clears_editor(self):
        self.app.editing_id = self.entry.id
        self.app.trees["file"].selection_set(self.entry.id)
        with patch("quick_access.app.confirm_source_deletion", return_value=True):
            with patch("quick_access.deletion.recycle_path", side_effect=DeletionTests.fake_recycle) as recycle:
                self.app.delete_source_selected()
        recycle.assert_called_once_with(str(self.file))
        self.assertEqual(self.store.entries, [])
        self.assertEqual(self.app.trees["file"].get_children(), ())
        self.assertIsNone(self.app.editing_id)
        self.assertIn("入口已同步移除", self.app.status_var.get())

    def test_backend_failure_is_visible_and_keeps_entry(self):
        with patch("quick_access.app.confirm_source_deletion", return_value=True):
            with patch("quick_access.deletion.recycle_path", side_effect=OSError("recycle failed")):
                with patch("quick_access.app.messagebox.showerror") as error:
                    self.app.delete_source_selected()
        self.assertEqual(error.call_args.args[0], "删除未完成")
        self.assertTrue(self.app.trees["file"].exists(self.entry.id))
        self.assertTrue(self.file.exists())
        self.assertEqual(self.root.cget("cursor"), "")

    def test_file_menu_separates_entry_removal_from_source_deletion(self):
        self.app._configure_entry_menu("file")
        labels = [self.app.entry_menu.entrycget(i, "label")
                  for i in range(self.app.entry_menu.index("end") + 1)
                  if self.app.entry_menu.type(i) != "separator"]
        self.assertEqual([label for label in labels if "删除" in label or "移除" in label],
                         ["移除入口", "删除原文件…"])
        self.assertEqual(str(self.app.entry_menu.entrycget(6, "foreground")), "#B42318")

    def test_removing_file_entry_does_not_change_original_file(self):
        self.app._configure_entry_menu("file")
        with patch("quick_access.app.messagebox.askyesno", return_value=True) as confirm:
            self.app.entry_menu.invoke(5)
        confirm.assert_called_once()
        self.assertEqual(self.store.entries, [])
        self.assertFalse(self.app.trees["file"].exists(self.entry.id))
        self.assertTrue(self.file.exists())

    def test_missing_file_can_be_removed_from_menu_and_survives_restart(self):
        DeletionTests.fake_recycle(str(self.file))
        with patch("quick_access.app.confirm_source_deletion") as confirm:
            with patch("quick_access.app.messagebox.askyesno", return_value=True):
                self.app.entry_menu.invoke(6)
        confirm.assert_not_called()
        self.assertEqual(self.store.entries, [])
        self.assertFalse(self.app.trees["file"].exists(self.entry.id))
        self.assertEqual(EntryStore(self.store.directory).entries, [])

    def test_cancelling_missing_file_cleanup_preserves_entry(self):
        DeletionTests.fake_recycle(str(self.file))
        before = self.store.path.read_bytes()
        with patch("quick_access.app.messagebox.askyesno", return_value=False):
            self.app.delete_selected()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertTrue(self.app.trees["file"].exists(self.entry.id))

    def test_delete_key_uses_real_file_delete_flow(self):
        with patch.object(self.app, "delete_source_selected") as delete:
            with patch.object(self.app, "remove_selected") as remove:
                self.app.delete_selected()
        delete.assert_called_once()
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
