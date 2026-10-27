"""Persist text cards and launch presets separately from file entries."""

from __future__ import annotations

import copy
import base64
import binascii
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .storage import EntryError


class WorkspaceStore:
    def __init__(self, directory):
        self.path = Path(directory) / "workspace.json"
        self.notes = []
        self.presets = []
        self.settings = {"edge_docking": True, "ball_size": 44, "floating_ball": True}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("unsupported format")
                self._validate(data["notes"], data["presets"])
                self.notes, self.presets = data["notes"], data["presets"]
                settings = data.get("settings", {})
                if not isinstance(settings, dict) or type(settings.get("edge_docking", True)) is not bool:
                    raise ValueError("invalid settings")
                size = settings.get("ball_size", 44)
                if type(size) is not int or not 44 <= size <= 88:
                    raise ValueError("invalid ball size")
                floating_ball = settings.get("floating_ball", True)
                if type(floating_ball) is not bool:
                    raise ValueError("invalid floating ball setting")
                self.settings = {"edge_docking": settings.get("edge_docking", True),
                                 "ball_size": size, "floating_ball": floating_ball}
            except (ValueError, TypeError, KeyError) as exc:
                raise EntryError(f"无法读取剪贴板与协同开启预设，原记录已保留。\n{self.path}") from exc

    def _validate(self, notes, presets):
        if not isinstance(notes, list) or not isinstance(presets, list):
            raise ValueError("invalid workspace lists")
        for records in (notes, presets):
            ids = set()
            for item in records:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids:
                    raise ValueError("invalid or duplicate id")
                ids.add(item["id"])
        for item in notes:
            if (not isinstance(item.get("title"), str) or len(item["title"]) > 80
                    or not isinstance(item.get("text"), str) or type(item.get("collapsed")) is not bool):
                raise ValueError("invalid card")
            height = item.get("height")
            parts = item.get("content", [])
            if not isinstance(parts, list):
                raise ValueError("invalid card content")
            for part in parts:
                if (not isinstance(part, dict) or len(part) != 1
                        or not ("text" in part or "image" in part)
                        or not isinstance(next(iter(part.values())), str)):
                    raise ValueError("invalid card content")
                if "image" in part:
                    try:
                        if len(part["image"]) > 48_000_000 or not base64.b64decode(
                                part["image"], validate=True).startswith(b"\x89PNG\r\n\x1a\n"):
                            raise ValueError("invalid image")
                    except binascii.Error as exc:
                        raise ValueError("invalid image") from exc
            if "image" in item:
                try:
                    encoded = item["image"]
                    if not isinstance(encoded, str) or len(encoded) > 48_000_000:
                        raise ValueError("invalid image")
                    if not base64.b64decode(encoded, validate=True).startswith(b"\x89PNG\r\n\x1a\n"):
                        raise ValueError("invalid image")
                except (binascii.Error, TypeError) as exc:
                    raise ValueError("invalid image") from exc
            if height is not None and (type(height) is not int or not 90 <= height <= 480):
                raise ValueError("invalid card height")
        names = set()
        for item in presets:
            name, ids = item.get("name"), item.get("entry_ids")
            if (not isinstance(name, str) or not name.strip() or len(name) > 80 or name in names
                    or not isinstance(ids, list) or not ids
                    or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids))):
                raise ValueError("invalid preset")
            names.add(name)
            snapshots = item.get("launch_items")
            if snapshots is not None:
                if not isinstance(snapshots, list) or len(snapshots) != len(ids):
                    raise ValueError("invalid preset launch items")
                from .shortcuts import asset_path
                for ident, snapshot in zip(ids, snapshots):
                    if (not isinstance(snapshot, dict) or snapshot.get("entry_id") != ident
                            or not isinstance(snapshot.get("title"), str) or not snapshot["title"].strip()):
                        raise ValueError("invalid preset launch item")
                    if snapshot.get("path") is not None:
                        asset_path(self.path.parent, snapshot["path"])

    def _save(self, notes, presets):
        self._validate(notes, presets)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="workspace-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "notes": notes, "presets": presets, "settings": self.settings}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name("workspace.backup.json"))
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.notes, self.presets = copy.deepcopy(notes), copy.deepcopy(presets)

    def save_notes(self, notes):
        self._save(notes, self.presets)

    def set_edge_docking(self, enabled):
        previous = self.settings
        self.settings = {**previous, "edge_docking": bool(enabled)}
        try:
            self._save(self.notes, self.presets)
        except OSError:
            self.settings = previous
            raise

    def set_ball_size(self, size):
        if type(size) is not int or not 44 <= size <= 88:
            raise EntryError("悬浮球大小须在 44–88 之间。")
        previous = self.settings
        self.settings = {**previous, "ball_size": size}
        try:
            self._save(self.notes, self.presets)
        except OSError:
            self.settings = previous
            raise

    def set_floating_ball(self, enabled):
        previous = self.settings
        self.settings = {**previous, "floating_ball": bool(enabled)}
        try:
            self._save(self.notes, self.presets)
        except OSError:
            self.settings = previous
            raise

    def save_preset(self, name, entry_ids, preset_id=None, *, launch_items=None):
        name = name.strip()
        if not name or len(name) > 80 or "\n" in name or "\r" in name:
            raise EntryError("请填写 1–80 字的预设名称。")
        if not entry_ids or len(entry_ids) != len(set(entry_ids)):
            raise EntryError("请至少选择一个快捷方式，每个快捷方式只能出现一次。")
        if any(item["name"] == name and item["id"] != preset_id for item in self.presets):
            raise EntryError("已有同名预设，请选择该预设修改，或使用其他名称。")
        if preset_id is not None and not any(item["id"] == preset_id for item in self.presets):
            raise EntryError("这个预设已不存在，请重新创建。")
        record = {"id": preset_id or uuid.uuid4().hex, "name": name, "entry_ids": list(entry_ids)}
        if launch_items is not None:
            record["launch_items"] = copy.deepcopy(launch_items)
        presets = [record if item["id"] == preset_id else item for item in self.presets]
        if preset_id is None:
            presets.append(record)
        self._save(self.notes, presets)
        from .shortcuts import collect_unused_shortcuts
        collect_unused_shortcuts(self.path.parent)
        return record

    def upgrade_presets(self, store):
        presets = copy.deepcopy(self.presets)
        for preset in presets:
            previous = {item["entry_id"]: item for item in preset.get("launch_items", [])}
            snapshots = []
            for ident in preset["entry_ids"]:
                item = previous.get(ident)
                if item is None or item.get("path") is None:
                    try:
                        item = store.shortcut_snapshot(ident)
                    except (EntryError, OSError):
                        item = {"entry_id": ident, "title": "已移除的快捷方式", "path": None}
                snapshots.append(item)
            preset["launch_items"] = snapshots
        if presets != self.presets:
            self._save(self.notes, presets)

    def launch_items(self, preset):
        from .shortcuts import asset_path
        return [{**item, "path": str(asset_path(self.path.parent, item["path"])) if item["path"] else None}
                for item in preset["launch_items"]]

    def remove_preset(self, preset_id):
        self._save(self.notes, [item for item in self.presets if item["id"] != preset_id])
        from .shortcuts import collect_unused_shortcuts
        collect_unused_shortcuts(self.path.parent)
