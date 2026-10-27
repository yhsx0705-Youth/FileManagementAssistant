"""Per-user install data location and legacy-data migration tests."""

import tempfile
import unittest
from pathlib import Path

from quick_access.app import default_data_directory
from quick_access.storage import EntryStore


class InstallDataTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parent.parent / ".qa"
        qa.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=qa)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_development_run_keeps_data_next_to_project(self):
        self.assertEqual(default_data_directory(self.root, frozen=False), self.root / "data")

    def test_installed_run_uses_per_user_local_app_data(self):
        install = self.root / "Programs" / "文件管理辅助器"
        local = self.root / "LocalAppData"
        result = default_data_directory(install, frozen=True, local_app_data=local)
        self.assertEqual(result, local / "文件管理辅助器")
        self.assertFalse(result.exists())

    def test_legacy_entries_are_copied_to_user_data_without_removing_source(self):
        install = self.root / "Programs" / "文件管理辅助器"
        legacy = install / "data"
        source = self.root / "原文件.txt"
        source.write_text("fixture", encoding="utf-8")
        old_store = EntryStore(legacy)
        entry = old_store.save_entry(str(source))

        local = self.root / "LocalAppData"
        result = default_data_directory(install, frozen=True, local_app_data=local)
        self.assertEqual(EntryStore(result).get(entry.id), entry)
        self.assertEqual(EntryStore(legacy).get(entry.id), entry)

    def test_existing_user_data_takes_precedence_over_legacy_copy(self):
        install = self.root / "Programs" / "文件管理辅助器"
        source = self.root / "原文件.txt"
        source.write_text("fixture", encoding="utf-8")
        old_store = EntryStore(install / "data")
        old_store.save_entry(str(source), "旧记录")

        local = self.root / "LocalAppData"
        existing_store = EntryStore(local / "文件管理辅助器")
        existing_store.save_entry(str(source), "新记录")
        result = default_data_directory(install, frozen=True, local_app_data=local)
        self.assertEqual(EntryStore(result).entries[0].title, "新记录")


if __name__ == "__main__":
    unittest.main()
