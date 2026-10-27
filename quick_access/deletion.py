"""Validated targets and Windows Recycle Bin operations. No permanent-delete fallback."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .storage import EntryError, normalize_path


@dataclass(frozen=True)
class DeleteTarget:
    path: str
    identity: tuple[int, int, int, int, int]
    is_directory: bool
    is_link: bool

    @property
    def kind_label(self) -> str:
        if self.is_link:
            return "链接"
        return "文件夹" if self.is_directory else "文件"


def inspect_target(path: str, protected_paths=(), *, action="删除") -> DeleteTarget:
    path = normalize_path(path)
    target = Path(path)
    if target.parent == target:
        raise EntryError(f"不能{action}磁盘根目录或共享根目录。")
    details = os.lstat(path)
    is_link = os.path.islink(path) or getattr(os.path, "isjunction", lambda _: False)(path)
    # Keep the last component of a link intact: recycling a link must not delete its target.
    actual = target.parent.resolve() / target.name if is_link else target.resolve()
    for protected in protected_paths:
        if Path(protected).resolve().is_relative_to(actual):
            raise EntryError(f"不能{action}正在使用的程序目录、数据目录或它们的上级文件夹。")
    return DeleteTarget(path, (details.st_dev, details.st_ino, stat.S_IFMT(details.st_mode),
                               details.st_mtime_ns, details.st_size),
                        stat.S_ISDIR(details.st_mode), is_link)


def check_target_unchanged(target: DeleteTarget, *, action="删除") -> None:
    current = inspect_target(target.path)
    if current != target:
        raise EntryError(f"确认期间目标已发生变化，本次{action}已取消。请核对后重新操作。")


def notify_shell_deleted(path: str) -> None:
    """Refresh Explorer views after a path has been removed from disk."""
    if sys.platform != "win32":
        return
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SHChangeNotify.argtypes = [wintypes.LONG, wintypes.UINT,
                                         ctypes.c_void_p, ctypes.c_void_p]
        shell.SHChangeNotify.restype = None
        flags = 0x0005 | 0x1000  # SHCNF_PATHW | SHCNF_FLUSH
        item_path = ctypes.create_unicode_buffer(path)
        parent_path = ctypes.create_unicode_buffer(str(Path(path).parent))
        shell.SHChangeNotify(0x00000004, flags, ctypes.cast(item_path, ctypes.c_void_p), None)
        shell.SHChangeNotify(0x00001000, flags, ctypes.cast(parent_path, ctypes.c_void_p), None)
    except (AttributeError, OSError):
        # The deletion already succeeded; a shell refresh failure must not report it as failed.
        return


class _RecycleGuard:
    """IFileOperationProgressSink: veto any fallback to permanent deletion."""

    def __init__(self):
        self.blocked = False
        self.result = 0
        self.references = 1
        pointer = ctypes.c_void_p
        dword, hresult = wintypes.DWORD, wintypes.LONG
        signatures = [
            (hresult, pointer, ctypes.POINTER(pointer)),  # QueryInterface
            (wintypes.ULONG,), (wintypes.ULONG,),  # AddRef, Release
            (hresult,), (hresult, hresult),  # StartOperations, FinishOperations
            (hresult, dword, pointer, pointer),  # PreRenameItem
            (hresult, dword, pointer, pointer, hresult, pointer),
            (hresult, dword, pointer, pointer, pointer),  # PreMoveItem
            (hresult, dword, pointer, pointer, pointer, hresult, pointer),
            (hresult, dword, pointer, pointer, pointer),  # PreCopyItem
            (hresult, dword, pointer, pointer, pointer, hresult, pointer),
            (hresult, dword, pointer),  # PreDeleteItem
            (hresult, dword, pointer, hresult, pointer),
            (hresult, dword, pointer, pointer),  # PreNewItem
            (hresult, dword, pointer, pointer, pointer, dword, hresult, pointer),
            (hresult, wintypes.UINT, wintypes.UINT),  # UpdateProgress
            (hresult,), (hresult,), (hresult,),  # Reset/Pause/ResumeTimer
        ]
        accepted = {uuid.UUID(value).bytes_le for value in (
            "00000000-0000-0000-C000-000000000046", "04B0F1A7-9490-44BC-96E1-4296A31252E2")}

        def query(_, iid, output):
            if ctypes.string_at(iid, 16) in accepted:
                output[0] = ctypes.cast(ctypes.pointer(self.instance), pointer)
                addref(None)
                return 0
            output[0] = None
            return -2147467262  # E_NOINTERFACE

        def addref(_):
            self.references += 1
            return self.references

        def release(_):
            self.references -= 1
            return self.references

        def finish(_, result):
            if result < 0:
                self.result = result
            return 0

        def predelete(_, flags, item):
            # Windows clears this flag when it intends to destroy rather than recycle.
            if not flags & 0x80:  # TSF_DELETE_RECYCLE_IF_POSSIBLE
                self.blocked = True
                return -2147467260  # E_ABORT
            return 0

        def postdelete(_, flags, item, result, created):
            return finish(None, result)

        handlers = {0: query, 1: addref, 2: release, 4: finish, 11: predelete, 12: postdelete}
        self.callbacks = [ctypes.WINFUNCTYPE(signature[0], pointer, *signature[1:])(
            handlers.get(index, lambda *_: 0)) for index, signature in enumerate(signatures)]
        self.table = (pointer * len(self.callbacks))(*[ctypes.cast(callback, pointer) for callback in self.callbacks])

        class Interface(ctypes.Structure):
            _fields_ = [("vtable", ctypes.POINTER(pointer))]

        self.instance = Interface(self.table)

    @property
    def pointer(self):
        return ctypes.byref(self.instance)


def recycle_path(path: str) -> None:
    if sys.platform != "win32" or tuple(sys.getwindowsversion()[:2]) < (6, 2):
        raise EntryError("此功能需要支持回收站操作的 Windows 8 或更新版本。")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    def check(result):
        if result < 0:
            raise OSError(result, "Windows 无法将目标移入回收站：" + ctypes.FormatError(result), path)

    def method(pointer, index, result_type, *argument_types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[index])

    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = wintypes.LONG
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = wintypes.LONG
    shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                 ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = wintypes.LONG

    check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED; IFileOperation requires STA.
    operation, item = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = guid("3AD05575-8857-4850-9277-11B85BDB8E09")
        iid_operation = guid("947AAB5F-0A5C-4C13-B4D6-4BF7836FC9F8")
        iid_item = guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")
        check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                   ctypes.byref(iid_operation), ctypes.byref(operation)))
        # Recycle rather than permanently delete. Stop on errors; never retry using unlink/rmtree.
        # FOFX_ADDUNDORECORD | FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE |
        # FOF_NOERRORUI | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NO_CONNECTED_ELEMENTS.
        flags = 0x20000000 | 0x00080000 | 0x00100000 | 0x0400 | 0x0010 | 0x0004 | 0x2000
        check(method(operation, 5, wintypes.LONG, wintypes.DWORD)(operation, flags))
        check(shell.SHCreateItemFromParsingName(path, None, ctypes.byref(iid_item), ctypes.byref(item)))
        guard = _RecycleGuard()
        check(method(operation, 18, wintypes.LONG, ctypes.c_void_p, ctypes.c_void_p)(operation, item, guard.pointer))
        result = method(operation, 21, wintypes.LONG)(operation)
        if guard.blocked:
            raise EntryError("此目标无法移入回收站，已停止删除，原入口已保留。")
        check(result)
        check(guard.result)
        aborted = wintypes.BOOL()
        check(method(operation, 22, wintypes.LONG, ctypes.POINTER(wintypes.BOOL))(operation, ctypes.byref(aborted)))
        if aborted.value:
            raise EntryError("Windows 已取消删除，入口已保留。")
    finally:
        if item.value:
            method(item, 2, wintypes.ULONG)(item)
        if operation.value:
            method(operation, 2, wintypes.ULONG)(operation)
        ole.CoUninitialize()
