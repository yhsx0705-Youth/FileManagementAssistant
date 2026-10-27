"""Collected shortcuts survive deletion of their desktop originals."""

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from dataclasses import replace, asdict
from unittest.mock import Mock, patch

from quick_access.app import App
from quick_access.launcher import SequentialLauncher
from quick_access.shortcuts import managed_path
from quick_access.storage import EntryError, EntryStore


class PreservedShortcutTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.directory = Path(self.temporary.name)
        self.link = self.directory / "桌面" / "ChatGPT.lnk"
        self.link.parent.mkdir()
        self.payload = b"\x4c\x00\x00\x00Shell link PIDL / arguments / working directory\x00"
        self.link.write_bytes(self.payload)
        self.store = EntryStore(self.directory / "data")

    def tearDown(self):
        self.temporary.cleanup()

    def test_add_preserves_full_link_and_open_survives_original_deletion_and_restart(self):
        entry = self.store.save_entry(str(self.link))
        saved = Path(self.store.effective_path(entry))
        self.assertNotEqual(saved, self.link)
        self.assertEqual(saved.read_bytes(), self.payload)
        self.assertEqual(self.link.read_bytes(), self.payload)
        self.link.unlink()
        loaded = EntryStore(self.store.directory)
        opened = []
        loaded.open_entry(entry.id, opened.append)
        self.assertEqual(opened, [str(saved)])
        self.assertEqual(loaded.get(entry.id).open_count, 1)
        self.assertEqual(loaded.shortcut_warnings, [])

    def test_existing_legacy_entry_is_protected_on_startup_without_changing_id_or_preset(self):
        self.store.directory.mkdir()
        self.store.path.write_text(json.dumps({"version": 1, "entries": [{
            "id": "legacy", "title": "ChatGPT", "path": str(self.link), "kind": "shortcut",
            "pinned": False, "created_at": "2026-01-01T00:00:00+08:00"}]}), encoding="utf-8")
        loaded = EntryStore(self.store.directory)
        self.assertEqual(Path(loaded.effective_path(loaded.get("legacy"))).read_bytes(), self.payload)
        self.assertEqual(loaded.get("legacy").path, str(self.link))

    def test_missing_legacy_link_reports_reimport_and_keeps_entry(self):
        entry = self.store.save_entry(str(self.link))
        Path(self.store.effective_path(entry)).unlink()
        self.link.unlink()
        loaded = EntryStore(self.store.directory)
        with self.assertRaisesRegex(EntryError, "重新导入"):
            loaded.open_entry(entry.id, lambda _: self.fail("must not open"))
        self.assertEqual(len(loaded.entries), 1)
        self.assertEqual(len(loaded.shortcut_warnings), 1)

    def test_reimport_from_another_folder_keeps_entry_id_and_presets(self):
        entry = self.store.save_entry(str(self.link))
        self.link.unlink()
        new_link = self.directory / "ChatGPT.lnk"
        new_link.write_bytes(b"new target and arguments")
        repaired = self.store.repair_shortcut(entry.id, str(new_link))
        self.assertEqual(repaired.id, entry.id)
        self.assertEqual(Path(self.store.effective_path(repaired)).read_bytes(), b"new target and arguments")

    def test_failed_import_and_failed_record_save_leave_no_entry_or_partial_copy(self):
        with patch("quick_access.shortcuts.shutil.copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                self.store.save_entry(str(self.link))
        self.assertEqual(self.store.entries, [])
        with patch("quick_access.storage.os.replace", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                self.store.save_entry(str(self.link))
        self.assertEqual(self.store.entries, [])
        self.assertEqual(list(self.store.directory.rglob("*.lnk")), [])
        self.assertEqual(list(self.store.directory.rglob("*.tmp")), [])

    def test_remove_never_deletes_original_and_preserved_copy_can_restore_backup_record(self):
        entry = self.store.save_entry(str(self.link))
        saved = Path(self.store.effective_path(entry))
        self.store.remove(entry.id)
        self.assertTrue(self.link.exists())
        self.assertTrue(saved.exists())
        with self.assertRaisesRegex(EntryError, "移除"):
            self.store.get(entry.id)

    def test_saved_copy_cannot_be_imported_twice_and_names_do_not_collide(self):
        first = self.store.save_entry(str(self.link))
        with self.assertRaises(EntryError):
            self.store.save_entry(self.store.effective_path(first))
        other = self.directory / "ChatGPT.lnk"
        other.write_bytes(b"different shortcut")
        second = self.store.save_entry(str(other))
        self.assertNotEqual(self.store.effective_path(first), self.store.effective_path(second))

    def test_shortcut_delete_and_rename_never_modify_desktop_or_application(self):
        entry = self.store.save_entry(str(self.link))
        with self.assertRaises(EntryError):
            self.store.prepare_source_rename(entry.id)
        with self.assertRaises(EntryError):
            self.store.prepare_source_deletion(entry.id)
        self.assertEqual(self.link.read_bytes(), self.payload)

    def test_deleted_desktop_link_still_opens_in_saved_sequence(self):
        entry = self.store.save_entry(str(self.link))
        self.link.unlink()
        root = tk.Tk()
        root.withdraw()
        try:
            launcher = SequentialLauncher(root, self.store, Mock(), Mock())
            with patch("quick_access.storage.os.startfile") as opener:
                launcher.start([entry.id])
                root.update()
            opener.assert_called_once_with(self.store.effective_path(entry))
            self.assertFalse(launcher.running)
        finally:
            root.destroy()

    def test_ui_alias_copy_and_delete_key_manage_collected_entry_only(self):
        entry = self.store.save_entry(str(self.link))
        self.link.unlink()
        root = tk.Tk()
        try:
            app = App(root, self.store)
            root.update()
            tree = app.trees["shortcut"]
            tree.selection_set(entry.id)
            app.edit_name_selected()
            app.name_var.set("我的 ChatGPT")
            app.submit()
            self.assertEqual(tree.item(entry.id, "text"), "我的 ChatGPT")
            tree.selection_set(entry.id)
            with patch("quick_access.app.write_files") as clipboard:
                app.copy_selected()
            clipboard.assert_called_once_with(root, [self.store.effective_path(entry)])
            with patch("quick_access.app.messagebox.askyesno", return_value=True):
                app.delete_selected()
            self.assertEqual(self.store.entries, [])
        finally:
            root.destroy()

    def test_repeated_add_remove_reuses_one_shortcut_copy(self):
        paths = set()
        for _ in range(20):
            entry = self.store.save_entry(str(self.link))
            paths.add(self.store.effective_path(entry))
            with self.assertRaises(EntryError):
                self.store.save_entry(f'"{self.link}"')
            self.store.remove(entry.id)
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(list(self.store.directory.rglob("*.lnk"))), 1)
        self.assertEqual(EntryStore(self.store.directory).entries, [])
        self.assertEqual(self.link.read_bytes(), self.payload)
        self.assertEqual(list(self.store.directory.rglob("*.tmp")), [])

    def test_legacy_copy_migrates_even_when_desktop_source_has_been_deleted(self):
        entry = self.store.save_entry(str(self.link))
        legacy = replace(entry, shortcut_copy=None)
        old_path = managed_path(self.store.directory, legacy)
        old_path.parent.mkdir(parents=True)
        old_path.write_bytes(self.payload)
        Path(self.store.effective_path(entry)).unlink()
        self.link.unlink()
        record = asdict(legacy)
        record.pop("shortcut_copy")
        self.store.path.write_text(json.dumps({"version": 1, "entries": [record]}), encoding="utf-8")
        loaded = EntryStore(self.store.directory)
        self.assertIsNotNone(loaded.get(entry.id).shortcut_copy)
        self.assertEqual(Path(loaded.effective_path(loaded.get(entry.id))).read_bytes(), self.payload)
        self.assertTrue(old_path.exists())

    def test_repeated_add_remove_of_other_entry_types_keeps_records_small_and_sources_unchanged(self):
        document = self.directory / "文件.txt"
        document.write_text("unchanged", encoding="utf-8")
        folder = self.directory / "文件夹"
        folder.mkdir()
        for source in (str(document), str(folder), "https://example.com/repeat"):
            for _ in range(15):
                entry = self.store.save_entry(source)
                with self.assertRaises(EntryError):
                    self.store.save_entry(source)
                self.store.remove(entry.id)
            self.assertEqual(EntryStore(self.store.directory).entries, [])
        self.assertEqual(document.read_text(encoding="utf-8"), "unchanged")
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(self.store.directory.rglob("*.lnk")), [])
        self.assertEqual(list(self.store.directory.rglob("*.tmp")), [])

    def test_unused_changed_versions_are_cleaned_but_latest_backup_is_retained(self):
        for index in range(12):
            self.link.write_bytes(f"target and arguments version {index}".encode())
            entry = self.store.save_entry(str(self.link))
            self.store.remove(entry.id)
        copies = list(self.store.directory.rglob("*.lnk"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), b"target and arguments version 11")
        self.assertEqual(self.link.read_bytes(), b"target and arguments version 11")
