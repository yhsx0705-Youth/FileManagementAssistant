import ctypes
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quick_access.file_clipboard import hdrop_payload, write_files, write_text
from quick_access.storage import EntryError


class FakeGlobalMemory:
    def __init__(self):
        self.buffers = {}
        self.freed = []
        self.next_handle = 1

    def GlobalAlloc(self, flags, size):
        handle = self.next_handle
        self.next_handle += 1
        self.buffers[handle] = ctypes.create_string_buffer(size)
        return handle

    def GlobalLock(self, handle):
        return ctypes.addressof(self.buffers[handle])

    def GlobalUnlock(self, _handle):
        return 1

    def GlobalFree(self, handle):
        self.freed.append(handle)
        self.buffers.pop(handle, None)
        return 0


class FileClipboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.file = Path(self.temporary.name) / "中文 文件.txt"
        self.file.write_text("fixture", encoding="utf-8")
        self.root = SimpleNamespace(winfo_id=lambda: 1234)

    def tearDown(self):
        self.temporary.cleanup()

    def test_busy_clipboard_is_retried_before_file_formats_are_written(self):
        user, kernel = Mock(), FakeGlobalMemory()
        user.OpenClipboard.side_effect = [False, False, True]
        user.EmptyClipboard.return_value = True
        user.RegisterClipboardFormatW.return_value = 0xC123
        payloads = {}

        def set_clipboard_data(format_id, handle):
            payloads[format_id] = ctypes.string_at(
                ctypes.addressof(kernel.buffers[handle]),
                ctypes.sizeof(kernel.buffers[handle]),
            )
            return handle

        user.SetClipboardData.side_effect = set_clipboard_data
        with patch("quick_access.file_clipboard._api", return_value=(user, kernel)):
            with patch("quick_access.file_clipboard.time.sleep") as sleep:
                write_files(self.root, [str(self.file)])

        self.assertEqual(user.OpenClipboard.call_count, 3)
        self.assertEqual(user.OpenClipboard.call_args_list[-1].args, (1234,))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(payloads[15], hdrop_payload([str(self.file)]))
        self.assertEqual(payloads[13].decode("utf-16-le").rstrip("\0"), str(self.file))
        self.assertEqual(len(payloads[0xC123]), 4)
        self.assertEqual(kernel.freed, [])
        user.CloseClipboard.assert_called_once_with()

    def test_optional_formats_may_fail_after_file_drop_format_succeeds(self):
        user, kernel = Mock(), FakeGlobalMemory()
        user.OpenClipboard.return_value = True
        user.EmptyClipboard.return_value = True
        user.RegisterClipboardFormatW.return_value = 0xC123
        accepted = []

        def set_clipboard_data(format_id, handle):
            if format_id == 13:
                return 0
            accepted.append(format_id)
            return handle

        user.SetClipboardData.side_effect = set_clipboard_data
        with patch("quick_access.file_clipboard._api", return_value=(user, kernel)):
            write_files(self.root, [str(self.file)])

        self.assertEqual(accepted, [15, 0xC123])
        self.assertEqual(kernel.freed, [2])
        user.CloseClipboard.assert_called_once_with()

    def test_open_failure_keeps_previous_clipboard_content_and_frees_buffers(self):
        user, kernel = Mock(), FakeGlobalMemory()
        user.OpenClipboard.return_value = False
        user.RegisterClipboardFormatW.return_value = 0xC123
        with patch("quick_access.file_clipboard._api", return_value=(user, kernel)):
            with patch("quick_access.file_clipboard.time.sleep"):
                with self.assertRaisesRegex(EntryError, "正被其他程序使用"):
                    write_files(self.root, [str(self.file)])

        user.EmptyClipboard.assert_not_called()
        user.CloseClipboard.assert_not_called()
        self.assertEqual(kernel.freed, [1, 2, 3])

    def test_text_copy_retries_tk_clipboard_contention(self):
        widget = Mock()
        widget.clipboard_clear.side_effect = [tk.TclError("busy"), None]
        with patch("quick_access.file_clipboard.time.sleep") as sleep:
            write_text(widget, "中文文本")
        self.assertEqual(widget.clipboard_clear.call_count, 2)
        widget.clipboard_append.assert_called_once_with("中文文本")
        widget.update_idletasks.assert_called_once_with()
        sleep.assert_called_once_with(0.025)

    def test_text_copy_reports_clipboard_busy_after_bounded_retries(self):
        widget = Mock()
        widget.clipboard_clear.side_effect = tk.TclError("busy")
        with patch("quick_access.file_clipboard.time.sleep"):
            with self.assertRaisesRegex(EntryError, "正被其他程序使用"):
                write_text(widget, "内容")
        self.assertEqual(widget.clipboard_clear.call_count, 5)
        widget.clipboard_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
