import tkinter as tk
import unittest
from unittest.mock import patch

from quick_access.icons import IconCache, _png


class IconTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_pdf_and_word_are_distinct_without_system_associations(self):
        cache = IconCache(self.root)
        with patch("quick_access.icons.shell_icon", return_value=None):
            pdf = cache.get("合同.pdf", "file")
            word = cache.get("合同.docx", "file")
            folder = cache.get("资料", "folder")
        self.assertEqual((pdf.width(), pdf.height()), (16, 16))
        self.assertNotEqual(pdf.get(1, 7), word.get(1, 7))
        self.assertNotEqual(pdf.get(1, 7), folder.get(1, 7))

    def test_icons_are_cached_by_type_including_case(self):
        cache = IconCache(self.root)
        with patch("quick_access.icons.shell_icon", return_value=None) as native:
            first = cache.get("A.PDF", "file")
            calls = native.call_count
            second = cache.get("B.pdf", "file")
        self.assertIs(first, second)
        self.assertEqual(native.call_count, calls)

    def test_system_icon_failure_keeps_all_file_types_usable(self):
        cache = IconCache(self.root)
        with patch("quick_access.icons.shell_icon", side_effect=OSError("unavailable")):
            for path, kind in (("file.pdf", "file"), ("file.docx", "file"),
                               ("file.unknown", "file"), ("folder", "folder"),
                               ("tool.lnk", "shortcut"), ("https://example.com", "web")):
                self.assertEqual(cache.get(path, kind).width(), 16)

    def test_shortcuts_use_the_actual_path_and_have_separate_cache_entries(self):
        cache = IconCache(self.root)
        native = (_png(bytes((255, 0, 0, 255)) * 256, 16), 23)
        with patch("quick_access.icons.shell_icon", return_value=native) as shell:
            first = cache.get("D:/工具/编辑器.lnk", "shortcut")
            second = cache.get("D:/工具/浏览器.lnk", "shortcut")
            again = cache.get("D:/工具/编辑器.lnk", "shortcut")
        shell.assert_any_call(".lnk", path="D:/工具/编辑器.lnk")
        shell.assert_any_call(".lnk", path="D:/工具/浏览器.lnk")
        self.assertIsNot(first, second)
        self.assertIs(first, again)

    def test_all_websites_share_a_round_globe_without_shell_dependency(self):
        cache = IconCache(self.root)
        with patch("quick_access.icons.shell_icon") as shell:
            first = cache.get("https://example.com", "web")
            second = cache.get("https://example.org/a", "web")
        shell.assert_not_called()
        self.assertIs(first, second)
        self.assertTrue(first.transparency_get(0, 0))
        self.assertFalse(first.transparency_get(8, 8))


if __name__ == "__main__":
    unittest.main()
