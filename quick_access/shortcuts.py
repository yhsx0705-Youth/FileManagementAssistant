"""Keep complete Shell links, including Store-app IDs and launch arguments."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath


def asset_path(directory, reference):
    if not isinstance(reference, str) or "\\" in reference:
        raise ValueError("快捷方式副本路径无效。")
    parts = PurePosixPath(reference).parts
    if (len(parts) != 3 or parts[0] != "shortcuts" or any(part in ("", ".", "..") or ":" in part for part in parts)
            or PurePosixPath(reference).is_absolute() or PurePosixPath(reference).suffix.lower() not in (".lnk", ".url")):
        raise ValueError("快捷方式副本路径无效。")
    root = Path(directory).resolve()
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError("快捷方式副本必须保存在辅助器数据目录中。")
    return path


def managed_path(directory, entry):
    if entry.shortcut_copy is not None:
        return asset_path(directory, entry.shortcut_copy)
    # Never interpret an entry ID as a filesystem path.
    identity = hashlib.sha256((entry.id + "\0" + os.path.normcase(entry.path)).encode("utf-8")).hexdigest()
    return Path(directory) / "shortcuts" / identity / Path(entry.path).name


def preserve_shortcut(directory, entry, *, prefer_source=False):
    previous = managed_path(directory, entry)
    if entry.shortcut_copy is not None and previous.is_file() and not prefer_source:
        return entry.shortcut_copy, None
    root = Path(directory).resolve()
    staging = root / "shortcuts"
    if not staging.resolve().is_relative_to(root):
        raise OSError("快捷方式保存目录不在辅助器数据目录中。")
    staging.mkdir(parents=True, exist_ok=True)
    source = entry.path if prefer_source or not previous.is_file() else previous
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="shortcut-", suffix=".tmp", dir=staging)
        os.close(descriptor)
        temporary = Path(name)
        # Copy the whole .lnk instead of extracting an .exe: packaged apps can use a PIDL.
        shutil.copy2(source, temporary)
        digest = hashlib.sha256()
        with temporary.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        reference = f"shortcuts/{digest.hexdigest()}/{Path(entry.path).name}"
        destination = asset_path(root, reference)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            return reference, None
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            return reference, None
        return reference, destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect_unused_shortcuts(directory):
    """Remove only verified, unreferenced immutable cache files; never source links."""
    root = Path(directory).resolve()
    cache = root / "shortcuts"
    if not cache.is_dir() or not cache.resolve().is_relative_to(root):
        return
    references = set()
    try:
        for name in ("entries.json", "entries.backup.json", "workspace.json", "workspace.backup.json"):
            path = root / name
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != 1:
                return
            values = [entry.get("shortcut_copy") for entry in data.get("entries", [])]
            values += [item.get("path") for preset in data.get("presets", []) for item in preset.get("launch_items", [])]
            for reference in values:
                if reference is not None:
                    asset_path(root, reference)
                    references.add(reference.casefold())
        for folder in cache.iterdir():
            if (not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(root)
                    or len(folder.name) != 64 or any(letter not in "0123456789abcdef" for letter in folder.name)):
                continue
            for path in folder.iterdir():
                if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root)
                        or path.suffix.lower() not in (".lnk", ".url")):
                    continue
                reference = path.relative_to(root).as_posix()
                if reference.casefold() in references:
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                # Old versions used a different directory hash. Preserve those legacy backups.
                if digest.hexdigest() == folder.name:
                    path.unlink()
            try:
                folder.rmdir()
            except OSError:
                pass
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Unknown metadata or a busy cache must never break add/remove or delete more files.
        return
