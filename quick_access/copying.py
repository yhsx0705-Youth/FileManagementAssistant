"""Copy files and folders without overwriting destinations or changing sources."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .storage import EntryError, normalize_path


@dataclass
class CopyResult:
    paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _check_folder_links(directory, names):
    for name in names:
        if getattr(os.path, "isjunction", lambda _: False)(Path(directory) / name):
            raise EntryError("目录包含系统连接，请在资源管理器中复制该目录。")
    return ()


def copy_files(paths, destination: str) -> CopyResult:
    destination = Path(normalize_path(destination)).resolve()
    if not destination.is_dir():
        raise EntryError("请在文件夹列表中选择一个可用的目标文件夹。")
    result = CopyResult()
    for value in dict.fromkeys(paths):
        temporary = None
        temporary_directory = None
        try:
            source = Path(normalize_path(value))
            is_directory = source.is_dir()
            if not is_directory and not source.is_file():
                raise EntryError("找不到这个文件或文件夹，请检查原路径。")
            if is_directory and destination.is_relative_to(source.resolve()):
                raise EntryError("不能将文件夹复制到自身或它的子文件夹中。")
            # Stage beside the destination, then publish the complete file with a no-overwrite rename.
            if is_directory:
                temporary_directory = Path(tempfile.mkdtemp(prefix=".文件夹复制-", dir=destination))
                temporary = temporary_directory / "内容"
                shutil.copytree(source, temporary, symlinks=True, ignore=_check_folder_links)
            else:
                descriptor, temporary_name = tempfile.mkstemp(prefix=".文件复制-", suffix=".tmp", dir=destination)
                os.close(descriptor)
                temporary = Path(temporary_name)
                shutil.copy2(source, temporary)
            number = 0
            while True:
                suffix = "" if number == 0 else " - 副本" if number == 1 else f" - 副本 ({number})"
                name = source.name if not suffix else (source.name + suffix if is_directory
                                                       else source.stem + suffix + source.suffix)
                target = destination / name
                if os.path.lexists(target):
                    number += 1
                    continue
                try:
                    os.rename(temporary, target)
                    temporary = None
                    result.paths.append(str(target))
                    break
                except FileExistsError:
                    number += 1
        except (EntryError, OSError) as exc:
            result.errors.append(f"{value}\n{exc}")
        finally:
            if temporary_directory is not None:
                # This directory was created exclusively by this copy and must remain under the chosen destination.
                if (temporary_directory.parent.resolve() == destination and not temporary_directory.is_symlink()
                        and not getattr(os.path, "isjunction", lambda _: False)(temporary_directory)):
                    try:
                        shutil.rmtree(temporary_directory)
                    except OSError as exc:
                        result.errors.append(f"临时副本清理失败：{temporary_directory}\n{exc}")
            elif temporary is not None and temporary.parent.resolve() == destination:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    result.errors.append(f"临时副本清理失败：{temporary}\n{exc}")
    return result
