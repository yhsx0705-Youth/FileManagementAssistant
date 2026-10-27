"""Portable, atomic storage for local file shortcuts."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from .targets import normalize_target, target_key, target_kind, target_title


ENTRY_KINDS = ("folder", "file", "shortcut", "web")
DEFAULT_PANEL_ORDER = ENTRY_KINDS


class EntryError(ValueError):
    """A recoverable error that can be shown to the user."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def normalize_path(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        raise EntryError("请先选择文件、文件夹，或粘贴完整路径。")
    value = os.path.expanduser(os.path.expandvars(value))
    if not os.path.isabs(value):
        raise EntryError("请输入完整路径，例如 D:\\工作资料\\合同.docx。")
    return os.path.normpath(value)


def path_key(value: str) -> str:
    return target_key(value)


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    path: str
    kind: str
    pinned: bool
    created_at: str
    last_opened: str | None = None
    open_count: int = 0
    position: int | None = None
    shortcut_copy: str | None = None


class EntryStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "entries.json"
        self.backup_path = self.directory / "entries.backup.json"
        self.entries = self._load()
        self.panel_order = self._load_panel_order()
        self.panel_visibility = self._load_panel_visibility()
        try:
            self._legacy_backup = json.loads(self.backup_path.read_text(encoding="utf-8")).get("entries", [])
        except (OSError, ValueError, AttributeError):
            self._legacy_backup = []
        self.shortcut_warnings = []
        protected = []
        for entry in self.entries:
            if entry.kind == "shortcut":
                try:
                    reference, _ = self._preserve_shortcut(entry)
                    entry = replace(entry, shortcut_copy=reference)
                except OSError as exc:
                    self.shortcut_warnings.append(f"{entry.title}：{exc}")
            protected.append(entry)
        if protected != self.entries:
            try:
                self._commit(protected)
            except OSError as exc:
                self.shortcut_warnings.append(f"快捷方式副本记录保存失败：{exc}")

    def _preserve_shortcut(self, entry, *, prefer_source=False):
        from .shortcuts import preserve_shortcut
        return preserve_shortcut(self.directory, entry, prefer_source=prefer_source)

    def shortcut_snapshot(self, entry_id):
        try:
            entry = self.get(entry_id)
        except EntryError:
            # A recently removed legacy entry may still be recoverable from the normal backup.
            try:
                try:
                    records = json.loads(self.backup_path.read_text(encoding="utf-8"))["entries"]
                except (OSError, ValueError, KeyError):
                    records = []
                records = [*records, *self._legacy_backup]
                record = next(item for item in records if item["id"] == entry_id)
                entry = Entry(**record)
                from .shortcuts import managed_path
                if entry.kind != "shortcut" or not managed_path(self.directory, entry).is_file():
                    raise ValueError
            except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
                raise EntryError("这个快捷方式已移除且没有可用的副本。") from exc
        if entry.kind != "shortcut":
            raise EntryError("协同开启预设只能包含快捷方式。")
        reference, _ = self._preserve_shortcut(entry)
        return {"entry_id": entry.id, "title": entry.title, "path": reference}

    def effective_path(self, entry) -> str:
        if entry.kind == "shortcut":
            from .shortcuts import managed_path
            saved = managed_path(self.directory, entry)
            if saved.is_file():
                return str(saved)
        return entry.path

    def repair_shortcut(self, entry_id, path):
        entry = self.get(entry_id)
        normalized = normalize_target(path)
        if entry.kind != "shortcut" or target_kind(normalized) != "shortcut" or not os.path.isfile(normalized):
            raise EntryError("请选择可用的 Windows 快捷方式（.lnk）。")
        return self.save_entry(normalized, entry.title, entry.pinned, entry.id, refresh_shortcut=True)

    def _load(self) -> list[Entry]:
        if not self.path.exists():
            return []
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(content, dict) or content.get("version") != 1:
                raise ValueError("Unsupported format")
            raw_entries = content["entries"]
            if not isinstance(raw_entries, list):
                raise ValueError("Entries must be a list")
            entries = []
            ids, paths = set(), set()
            for item in raw_entries:
                entry = Entry(**item)
                if (
                    not isinstance(entry.id, str) or not entry.id
                    or not isinstance(entry.title, str) or not entry.title.strip()
                    or not isinstance(entry.path, str)
                    or entry.kind not in ENTRY_KINDS
                    or (entry.kind == "web" and not entry.path.lower().startswith(("http://", "https://")))
                    or (entry.kind != "web" and not os.path.isabs(entry.path))
                    or type(entry.pinned) is not bool
                    or type(entry.open_count) is not int or entry.open_count < 0
                    or (entry.position is not None and (type(entry.position) is not int or entry.position < 0))
                    or not isinstance(entry.created_at, str)
                    or (entry.last_opened is not None and not isinstance(entry.last_opened, str))
                    or entry.id in ids
                ):
                    raise ValueError("Invalid entry")
                if entry.shortcut_copy is not None:
                    from .shortcuts import asset_path
                    if entry.kind != "shortcut":
                        raise ValueError("Non-shortcut asset")
                    asset_path(self.directory, entry.shortcut_copy)
                datetime.fromisoformat(entry.created_at)
                if entry.last_opened:
                    datetime.fromisoformat(entry.last_opened)
                if entry.kind in ("file", "shortcut"):
                    normalized = normalize_target(entry.path)
                    inferred_kind = target_kind(normalized)
                    if normalized != entry.path or inferred_kind != entry.kind:
                        entry = replace(entry, path=normalized, kind=inferred_kind,
                                        shortcut_copy=entry.shortcut_copy if inferred_kind == "shortcut" else None)
                key = target_key(entry.path)
                if key in paths:
                    raise ValueError("Duplicate target")
                ids.add(entry.id)
                paths.add(key)
                entries.append(entry)
            return entries
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise EntryError(
                "无法读取已保存的入口，原数据已保留。\n\n"
                f"数据文件：{self.path}\n"
                "如果存在 entries.backup.json，可先保留原文件，再用备份恢复。"
            ) from exc

    def _commit(self, entries: list[Entry]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "entries": [asdict(entry) for entry in entries],
             "panel_order": list(self.panel_order),
             "panel_visibility": dict(self.panel_visibility)},
            ensure_ascii=False, indent=2,
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory,
                prefix="entries-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(temporary, self.path)
            self.entries = entries
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _load_panel_order(self) -> list[str]:
        if not self.path.exists():
            return list(DEFAULT_PANEL_ORDER)
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
            order = content.get("panel_order", DEFAULT_PANEL_ORDER)
            if isinstance(order, list) and len(order) == len(ENTRY_KINDS) and set(order) == set(ENTRY_KINDS):
                return order
        except (ValueError, OSError, AttributeError):
            pass
        return list(DEFAULT_PANEL_ORDER)

    def _load_panel_visibility(self) -> dict[str, bool]:
        if not self.path.exists():
            return {kind: True for kind in ENTRY_KINDS}
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
            visibility = content.get("panel_visibility", {})
            if isinstance(visibility, dict):
                result = {kind: visibility.get(kind, True) is not False for kind in ENTRY_KINDS}
                if any(result.values()):
                    return result
        except (ValueError, OSError, AttributeError):
            pass
        return {kind: True for kind in ENTRY_KINDS}

    def save_panel_order(self, order) -> None:
        order = list(order)
        if len(order) != len(ENTRY_KINDS) or set(order) != set(ENTRY_KINDS):
            raise EntryError("区域顺序无效，请刷新后重试。")
        if order == self.panel_order:
            return
        previous = self.panel_order
        self.panel_order = order
        try:
            self._commit(self.entries)
        except OSError:
            self.panel_order = previous
            raise

    def save_panel_visibility(self, visibility) -> None:
        updated = {kind: bool(visibility.get(kind, True)) for kind in ENTRY_KINDS}
        if not any(updated.values()):
            raise EntryError("至少保留一个显示区域。")
        if updated == self.panel_visibility:
            return
        previous = self.panel_visibility
        self.panel_visibility = updated
        try:
            self._commit(self.entries)
        except OSError:
            self.panel_visibility = previous
            raise

    def get(self, entry_id: str) -> Entry:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise EntryError("这个入口已被移除，请刷新后重试。")

    def save_entry(
        self, path: str, title: str = "", pinned: bool = False,
        entry_id: str | None = None, *, refresh_shortcut: bool = False,
    ) -> Entry:
        normalized = normalize_target(path)
        existing = self.get(entry_id) if entry_id else None
        # A disconnected disk may temporarily make an existing shortcut unavailable.
        same_path = existing is not None and path_key(normalized) == path_key(existing.path)
        exists = os.path.exists(normalized)
        kind = target_kind(normalized)
        if not exists and kind != "web" and same_path:
            kind = existing.kind
        if kind != "web" and not exists and not same_path:
            raise EntryError("找不到这个路径，请检查路径或连接对应磁盘后再试。")
        duplicate = next((entry for entry in self.entries
                          if path_key(normalized) in (path_key(entry.path), path_key(self.effective_path(entry)))
                          and entry.id != entry_id), None)
        if duplicate:
            raise EntryError(f"这个路径已经添加为「{duplicate.title}」，无需重复添加。")
        title = title.strip() or target_title(normalized, kind)
        if len(title) > 80:
            raise EntryError("入口名称最多支持 80 个字符。")
        if "\n" in title or "\r" in title:
            raise EntryError("入口名称不能包含换行。")
        positions = [item.position for item in self.entries if item.kind == kind and item.position is not None]
        next_position = max(positions) + 1 if positions else None
        if existing:
            entry = replace(existing, title=title, path=normalized, kind=kind, pinned=bool(pinned))
            if not same_path or kind != "shortcut" or refresh_shortcut:
                entry = replace(entry, shortcut_copy=None)
            if existing.kind != kind:
                entry = replace(entry, position=next_position)
            entries = [entry if item.id == existing.id else item for item in self.entries]
        else:
            entry = Entry(uuid.uuid4().hex, title, normalized, kind, bool(pinned), now(), position=next_position)
            entries = [*self.entries, entry]
        created_copy = None
        if kind == "shortcut":
            reference, created_copy = self._preserve_shortcut(entry, prefer_source=refresh_shortcut)
            entry = replace(entry, shortcut_copy=reference)
            entries = [entry if item.id == entry.id else item for item in entries]
        try:
            self._commit(entries)
        except OSError:
            if created_copy is not None:
                created_copy.unlink(missing_ok=True)
            raise
        return entry

    def remove(self, entry_id: str) -> None:
        entry = self.get(entry_id)
        self._commit([entry for entry in self.entries if entry.id != entry_id])
        if entry.kind == "shortcut":
            from .shortcuts import collect_unused_shortcuts
            collect_unused_shortcuts(self.directory)

    def prepare_source_rename(self, entry_id: str, protected_paths=()):
        from .deletion import inspect_target
        entry = self.get(entry_id)
        if entry.kind == "shortcut":
            raise EntryError("快捷方式已独立收藏，请修改显示名称。")
        path = entry.path
        if os.path.isdir(path):
            raise EntryError("文件夹只允许修改显示名称，不允许重命名原文件夹。")
        return inspect_target(path, (self.directory, *protected_paths), action="重命名")

    def rename_source(self, entry_id: str, name: str, target=None, protected_paths=()) -> list[str]:
        from .deletion import check_target_unchanged
        from .renaming import validate_source_name
        name = validate_source_name(name)
        entry = self.get(entry_id)
        current = self.prepare_source_rename(entry_id, protected_paths)
        if target is not None and current != target:
            raise EntryError("输入期间目标或入口路径已发生变化，请取消后重新操作。")
        source = Path(entry.path)
        if source.name == name:
            return []
        destination = source.with_name(name)
        if os.path.lexists(destination):
            # Case-only changes are valid, but never replace a different item.
            if path_key(str(destination)) != path_key(str(source)) or not os.path.samefile(source, destination):
                raise EntryError("同一位置已存在这个名称的文件或文件夹，请换一个名称。")
        renamed = replace(entry, path=str(destination), title=name)
        updated = [renamed if item.id == entry_id else item for item in self.entries]
        keys = [path_key(item.path) for item in updated]
        if len(set(keys)) != len(keys):
            raise EntryError("改名后的路径已存在于入口列表中，请先修改或移除那个入口。")
        check_target_unchanged(current, action="重命名")
        # os.rename on Windows refuses to overwrite an existing destination, including races.
        os.rename(source, destination)
        try:
            self._commit(updated)
        except OSError as exc:
            try:
                check_target_unchanged(replace(current, path=str(destination)), action="恢复名称")
                os.rename(destination, source)
            except (EntryError, OSError) as rollback_error:
                self.entries = updated
                raise EntryError(
                    f"原文件已重命名，但入口记录保存失败，且无法恢复原名称。\n"
                    f"当前路径：{destination}\n请保留此路径，重启后修改入口路径。"
                ) from rollback_error
            raise EntryError("入口记录保存失败，已恢复原文件名称，本次重命名未生效。") from exc
        return [entry_id]

    def prepare_source_deletion(self, entry_id: str, protected_paths=()):
        from .deletion import inspect_target
        entry = self.get(entry_id)
        if entry.kind == "shortcut":
            raise EntryError("快捷方式只需移除入口，不会删除桌面快捷方式或目标程序。")
        path = entry.path
        if os.path.isdir(path):
            raise EntryError("文件夹只做入口管理，不允许删除原文件夹。")
        return inspect_target(path, (self.directory, *protected_paths))

    def delete_source(self, entry_id: str, target, recycler=None) -> list[str]:
        from .deletion import check_target_unchanged, notify_shell_deleted, recycle_path
        entry = self.get(entry_id)
        if entry.kind == "shortcut":
            raise EntryError("快捷方式只需移除入口，不会删除桌面快捷方式或目标程序。")
        if path_key(entry.path) != path_key(target.path):
            raise EntryError("入口路径已变化，本次删除已取消。")
        if target.is_directory or os.path.isdir(entry.path):
            raise EntryError("文件夹只做入口管理，不允许删除原文件夹。")
        try:
            check_target_unchanged(target)
        except FileNotFoundError:
            # Already removed while the confirmation was open: finish deleting the entry.
            pass
        else:
            try:
                (recycler or recycle_path)(target.path)
            except OSError:
                try:
                    os.lstat(target.path)
                except FileNotFoundError:
                    pass
                else:
                    raise
        try:
            os.lstat(target.path)
        except FileNotFoundError:
            exists = False
        else:
            exists = True
        if exists:
            raise EntryError("Windows 未完成删除，原入口已保留。")
        notify_shell_deleted(target.path)
        removed_ids = [entry_id]
        remaining = [item for item in self.entries if item.id not in removed_ids]
        try:
            self._commit(remaining)
        except OSError as exc:
            self.entries = remaining
            raise EntryError("原文件已删除，当前列表已移除入口，但入口记录保存失败。请重启后清理失效入口。") from exc
        return removed_ids

    def open_parent(self, entry_id: str, opener=None) -> str:
        entry = self.get(entry_id)
        if entry.kind == "web":
            raise EntryError("网页链接没有本地文件所在位置。")
        local_path = self.effective_path(entry)
        parent = Path(local_path).parent
        if parent == Path(entry.path):
            raise EntryError("此路径已经是根目录，没有上一级。")
        if not parent.is_dir():
            raise EntryError("上一级文件夹目前不可用，请检查磁盘是否连接。")
        (opener or os.startfile)(str(parent))
        return str(parent)

    def reorder(self, kind: str, entry_ids: list[str]) -> None:
        if kind not in ENTRY_KINDS:
            raise EntryError("只能在同一入口区域内调整顺序。")
        current = self.query(kind, sort="manual")
        current_ids = [entry.id for entry in current]
        if len(entry_ids) != len(current_ids) or set(entry_ids) != set(current_ids):
            raise EntryError("入口列表已变化，请刷新后重新排序。")
        if entry_ids == current_ids:
            return
        positions = {entry_id: index for index, entry_id in enumerate(entry_ids)}
        self._commit([replace(entry, position=positions[entry.id]) if entry.kind == kind else entry
                      for entry in self.entries])

    def toggle_pin(self, entry_id: str) -> Entry:
        entry = replace(self.get(entry_id), pinned=not self.get(entry_id).pinned)
        self._commit([entry if item.id == entry_id else item for item in self.entries])
        return entry

    def open_entry(self, entry_id: str, opener: Callable[[str], None] | None = None) -> Entry:
        entry = self.get(entry_id)
        local_path = self.effective_path(entry)
        if entry.kind == "shortcut" and not os.path.isfile(local_path):
            raise EntryError("此快捷方式尚未保存独立副本，原快捷方式已不存在。\n\n请右键选择「重新导入快捷方式…」，重新选择一次快捷方式。以后可删除桌面上的快捷方式。")
        if entry.kind != "web" and not os.path.exists(local_path):
            raise EntryError("这个路径目前不可用。文件可能已移动，或所在磁盘尚未连接。\n\n可清理失效入口，再从实际位置重新拖入。")
        if opener is None:
            if entry.kind == "web":
                import webbrowser
                opener = webbrowser.open
            else:
                opener = os.startfile
        opener(local_path)
        updated = replace(entry, last_opened=now(), open_count=entry.open_count + 1)
        try:
            self._commit([updated if item.id == entry_id else item for item in self.entries])
        except OSError as exc:
            raise EntryError("已打开目标，但最近使用记录保存失败。请检查数据目录的写入权限。") from exc
        return updated

    def query(self, view: str = "all", text: str = "", sort: str = "created") -> list[Entry]:
        result = self.entries[:]
        if view == "pinned":
            result = [entry for entry in result if entry.pinned]
        elif view in ENTRY_KINDS:
            result = [entry for entry in result if entry.kind == view]
        elif view == "recent":
            result = [entry for entry in result if entry.last_opened]
        terms = text.casefold().split()
        result = [entry for entry in result
                  if all(term in f"{entry.title} {entry.path}".casefold() for term in terms)]
        if view == "recent" or sort == "recent":
            result.sort(key=lambda entry: (entry.last_opened or "", entry.created_at), reverse=True)
        elif sort == "name":
            result.sort(key=lambda entry: entry.title.casefold())
        elif sort == "manual":
            # Old records retain their existing display order until their first drag.
            result.sort(key=lambda entry: entry.created_at, reverse=True)
            result.sort(key=lambda entry: not entry.pinned)
            result.sort(key=lambda entry: (entry.position is None,
                                           entry.position if entry.position is not None else 0))
        else:
            result.sort(key=lambda entry: entry.created_at, reverse=True)
        if view != "recent" and sort != "manual":
            result.sort(key=lambda entry: not entry.pinned)
        return result
