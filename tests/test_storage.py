import json
import itertools
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from quick_access.storage import EntryError, EntryStore, normalize_path


class EntryStoreTests(unittest.TestCase):
    def setUp(self):
        # Windows clock resolution must not decide the order of newly saved test entries.
        clock = itertools.count()
        clock_patch = patch("quick_access.storage.now", side_effect=lambda: (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=next(clock))).isoformat())
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        qa_directory = Path(__file__).resolve().parent.parent / ".qa"
        qa_directory.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa_directory)
        self.root = Path(self.temporary.name)
        self.file = self.root / "重要合同 文档.txt"
        self.file.write_text("sample", encoding="utf-8")
        self.folder = self.root / "项目资料"
        self.folder.mkdir()
        self.store = EntryStore(self.root / "data")

    def tearDown(self):
        self.temporary.cleanup()

    def test_unicode_file_and_folder_survive_restart(self):
        file_entry = self.store.save_entry(str(self.file), "重要合同", True)
        folder_entry = self.store.save_entry(str(self.folder))
        reloaded = EntryStore(self.store.directory)
        self.assertEqual(reloaded.get(file_entry.id).title, "重要合同")
        self.assertTrue(reloaded.get(file_entry.id).pinned)
        self.assertEqual(reloaded.get(file_entry.id).kind, "file")
        self.assertEqual(reloaded.get(folder_entry.id).kind, "folder")
        self.assertEqual(reloaded.get(folder_entry.id).title, "项目资料")

    def test_shortcuts_and_web_links_are_stored_and_opened_by_type(self):
        shortcut = self.root / "内部工具.lnk"
        shortcut.write_text("fixture", encoding="utf-8")
        shortcut_entry = self.store.save_entry(str(shortcut))
        self.assertEqual(shortcut_entry.kind, "shortcut")
        self.assertEqual(shortcut_entry.title, "内部工具")

        url_file = self.root / "帮助.url"
        url_file.write_text("[InternetShortcut]\nURL=https://Example.com/help\n", encoding="utf-8")
        web_entry = self.store.save_entry(str(url_file))
        self.assertEqual(web_entry.kind, "web")
        self.assertEqual(web_entry.path, "https://Example.com/help")
        self.assertTrue(url_file.exists())

        pasted = self.store.save_entry("example.org/docs")
        self.assertEqual(pasted.kind, "web")
        self.assertEqual(pasted.path, "https://example.org/docs")
        opened = []
        self.store.open_entry(pasted.id, opened.append)
        self.assertEqual(opened, ["https://example.org/docs"])
        reloaded = EntryStore(self.store.directory)
        self.assertEqual(reloaded.get(shortcut_entry.id).kind, "shortcut")
        self.assertEqual(reloaded.get(web_entry.id).path, web_entry.path)

    def test_panel_visibility_and_order_persist_without_changing_entries(self):
        entry = self.store.save_entry(str(self.file))
        order = ["web", "file", "folder", "shortcut"]
        visibility = {kind: kind in ("folder", "web")
                      for kind in ("folder", "file", "shortcut", "web")}
        self.store.save_panel_order(order)
        self.store.save_panel_visibility(visibility)
        reloaded = EntryStore(self.store.directory)
        self.assertEqual(reloaded.panel_order, order)
        self.assertEqual(reloaded.panel_visibility, visibility)
        self.assertEqual(reloaded.entries, [entry])

    def test_at_least_one_panel_must_remain_visible(self):
        with self.assertRaisesRegex(EntryError, "至少保留一个"):
            self.store.save_panel_visibility({kind: False for kind in
                                              ("folder", "file", "shortcut", "web")})

    def test_quoted_normalized_paths_cannot_be_duplicated(self):
        self.store.save_entry(str(self.file))
        with self.assertRaisesRegex(EntryError, "已经添加"):
            self.store.save_entry('"' + str(self.file.parent / "." / self.file.name) + '"')
        self.assertEqual(len(self.store.entries), 1)
        if os.name == "nt":
            with self.assertRaises(EntryError):
                self.store.save_entry(str(self.file).upper())

    def test_invalid_paths_do_not_create_data(self):
        for path in ("", "relative.txt", str(self.root / "missing.txt")):
            with self.subTest(path=path), self.assertRaises(EntryError):
                self.store.save_entry(path)
        self.assertFalse(self.store.path.exists())

    def test_paths_expand_environment_variables(self):
        with patch.dict(os.environ, {"SUDA_TEST_ROOT": str(self.root)}):
            source = "%SUDA_TEST_ROOT%" if os.name == "nt" else "$SUDA_TEST_ROOT"
            self.assertEqual(normalize_path(source + os.sep + self.file.name), str(self.file))

    def test_edit_and_remove_only_change_shortcuts(self):
        entry = self.store.save_entry(str(self.file))
        self.store.save_entry(str(self.file), "新名字", entry_id=entry.id)
        self.assertEqual(self.store.get(entry.id).title, "新名字")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "sample")
        self.store.remove(entry.id)
        self.assertTrue(self.file.exists())
        self.assertEqual(EntryStore(self.store.directory).entries, [])

    def test_temporarily_missing_entries_are_preserved_and_editable(self):
        entry = self.store.save_entry(str(self.file))
        self.file.unlink()
        self.store = EntryStore(self.store.directory)
        self.store.save_entry(entry.path, "外接磁盘合同", True, entry.id)
        self.assertEqual(self.store.get(entry.id).kind, "file")
        self.assertTrue(self.store.get(entry.id).pinned)
        with self.assertRaisesRegex(EntryError, "不可用"):
            self.store.open_entry(entry.id, lambda _: self.fail("Should not open missing path"))

    def test_successful_open_updates_recent_and_count(self):
        entry = self.store.save_entry(str(self.file))
        opened = []
        self.store.open_entry(entry.id, opened.append)
        self.assertEqual(opened, [str(self.file)])
        self.assertEqual(self.store.get(entry.id).open_count, 1)
        self.assertEqual(self.store.query("recent")[0].id, entry.id)
        self.assertEqual(EntryStore(self.store.directory).get(entry.id).open_count, 1)

    def test_failed_open_does_not_update_recent(self):
        entry = self.store.save_entry(str(self.file))
        def fail(_):
            raise OSError("No default application")
        with self.assertRaises(OSError):
            self.store.open_entry(entry.id, fail)
        self.assertEqual(self.store.query("recent"), [])
        self.assertEqual(self.store.get(entry.id).open_count, 0)

    def test_failed_atomic_save_preserves_memory_and_disk(self):
        entry = self.store.save_entry(str(self.file), "原名称")
        original = self.store.path.read_bytes()
        with patch("quick_access.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.store.save_entry(str(self.file), "新名称", entry_id=entry.id)
        self.assertEqual(self.store.get(entry.id).title, "原名称")
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.store.directory.glob("*.tmp")), [])

    def test_backup_contains_previous_successful_state(self):
        entry = self.store.save_entry(str(self.file), "原名称")
        self.store.save_entry(str(self.file), "新名称", entry_id=entry.id)
        backup = json.loads(self.store.backup_path.read_text(encoding="utf-8"))
        self.assertEqual(backup["entries"][0]["title"], "原名称")

    def test_corrupt_data_is_reported_and_never_overwritten(self):
        self.store.directory.mkdir()
        for content in ("{broken", '{"version":1,"entries":[{}]}',
                        '{"version":1,"entries":null}', '{"version":2,"entries":[]}'):
            self.store.path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(EntryError):
                EntryStore(self.store.directory)
            self.assertEqual(self.store.path.read_text(encoding="utf-8"), content)

    def test_search_matches_name_and_path_and_filters_by_kind(self):
        file_entry = self.store.save_entry(str(self.file), "客户合同")
        folder_entry = self.store.save_entry(str(self.folder), "工作目录")
        self.assertEqual([e.id for e in self.store.query(text="合同")], [file_entry.id])
        self.assertEqual([e.id for e in self.store.query(text="项目资料")], [folder_entry.id])
        self.assertEqual(self.store.query("file", "工作目录"), [])
        self.assertEqual(self.store.query(text="客户 missing"), [])

    def test_pinned_entries_are_first_and_recent_is_open_order(self):
        first = self.store.save_entry(str(self.file), "A 文件", True)
        second = self.store.save_entry(str(self.folder), "B 文件夹")
        self.assertEqual(self.store.query()[0].id, first.id)
        self.assertEqual(len(self.store.query("pinned")), 1)
        self.store.open_entry(first.id, lambda _: None)
        self.store.open_entry(second.id, lambda _: None)
        self.assertEqual(self.store.query("recent")[0].id, second.id)
        self.store.toggle_pin(first.id)
        self.assertEqual(self.store.query("pinned"), [])

    def two_files(self):
        first = self.store.save_entry(str(self.file), "第一份")
        second_path = self.root / "第二份.txt"
        second_path.write_text("second", encoding="utf-8")
        second = self.store.save_entry(str(second_path), "第二份")
        return first, second

    def test_manual_order_survives_restart_and_leaves_other_kind_unchanged(self):
        first, second = self.two_files()
        folder = self.store.save_entry(str(self.folder))
        self.store.reorder("file", [first.id, second.id])
        loaded = EntryStore(self.store.directory)
        self.assertEqual([e.id for e in loaded.query("file", sort="manual")], [first.id, second.id])
        self.assertEqual(loaded.get(folder.id), folder)
        self.assertEqual(loaded.get(first.id).path, first.path)
        self.assertEqual(loaded.get(first.id).title, first.title)
        self.assertTrue(self.file.exists())

    def test_legacy_records_without_positions_keep_previous_display_order(self):
        self.two_files()
        expected = [e.id for e in self.store.query("file")]
        content = json.loads(self.store.path.read_text(encoding="utf-8"))
        for entry in content["entries"]:
            entry.pop("position", None)
        self.store.path.write_text(json.dumps(content), encoding="utf-8")
        loaded = EntryStore(self.store.directory)
        self.assertEqual([e.id for e in loaded.query("file", sort="manual")], expected)
        self.assertTrue(all(entry.position is None for entry in loaded.entries))

    def test_new_entries_append_and_edits_and_opens_keep_manual_order(self):
        first, second = self.two_files()
        self.store.reorder("file", [first.id, second.id])
        self.store.save_entry(first.path, "重命名", entry_id=first.id)
        self.store.open_entry(second.id, lambda _: None)
        third_path = self.root / "第三份.txt"
        third_path.write_text("third", encoding="utf-8")
        third = self.store.save_entry(str(third_path))
        self.assertEqual([e.id for e in self.store.query("file", sort="manual")],
                         [first.id, second.id, third.id])

    def test_invalid_reorders_cannot_omit_duplicate_or_mix_entries(self):
        first, second = self.two_files()
        folder = self.store.save_entry(str(self.folder))
        original = self.store.path.read_bytes()
        for ids in ([first.id], [first.id, first.id], [first.id, folder.id], [first.id, "unknown"]):
            with self.subTest(ids=ids), self.assertRaises(EntryError):
                self.store.reorder("file", ids)
        with self.assertRaises(EntryError):
            self.store.reorder("invalid", [first.id, second.id])
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(len(self.store.entries), 3)

    def test_failed_reorder_preserves_disk_and_in_memory_order(self):
        self.two_files()
        # Creation timestamps can tie on Windows; always request a real order change.
        changed_order = [entry.id for entry in reversed(self.store.query("file", sort="manual"))]
        before_entries = self.store.entries[:]
        before_file = self.store.path.read_bytes()
        with patch("quick_access.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.store.reorder("file", changed_order)
        self.assertEqual(self.store.entries, before_entries)
        self.assertEqual(self.store.path.read_bytes(), before_file)

    def test_manual_order_takes_precedence_over_legacy_pin_priority(self):
        first, second = self.two_files()
        self.store.toggle_pin(second.id)
        self.store.reorder("file", [first.id, second.id])
        self.assertEqual([e.id for e in self.store.query("file", sort="manual")], [first.id, second.id])

    def test_changing_kind_appends_to_manual_destination_order(self):
        folder_one = self.store.save_entry(str(self.folder))
        folder_two_path = self.root / "第二个文件夹"
        folder_two_path.mkdir()
        folder_two = self.store.save_entry(str(folder_two_path))
        self.store.reorder("folder", [folder_one.id, folder_two.id])
        file_entry = self.store.save_entry(str(self.file))
        folder_three_path = self.root / "第三个文件夹"
        folder_three_path.mkdir()
        self.store.save_entry(str(folder_three_path), file_entry.title, entry_id=file_entry.id)
        self.assertEqual([e.id for e in self.store.query("folder", sort="manual")],
                         [folder_one.id, folder_two.id, file_entry.id])
        self.assertTrue(self.file.exists())


if __name__ == "__main__":
    unittest.main()
