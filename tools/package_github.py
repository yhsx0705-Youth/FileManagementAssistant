"""Create public release assets from explicit allowlists, never local user data."""

import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def archive(destination, files, prefix):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for source, name in files:
            bundle.write(source, f"{prefix}/{name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    version = args.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a numeric x.y.z version")
    output = ROOT / "github-release" / f"v{version}"
    output.mkdir(parents=True, exist_ok=False)
    installer_name = f"FileManagementAssistant-Setup-{version}-x64.exe"
    installer = ROOT / "release" / version / installer_name
    executable = args.stage.resolve() / "文件管理辅助器.exe"
    if not installer.is_file() or not executable.is_file():
        raise FileNotFoundError("Build installer and application first")
    shutil.copy2(installer, output / installer_name)
    # All write operations below target the newly created release directory.
    guide = output / "使用与卸载说明.txt"
    guide.write_text(f"""文件管理辅助器 v{version} · Windows x64

安装版：双击 FileManagementAssistant-Setup-{version}-x64.exe，按向导安装。
安装包内置卸载程序，安装后会自动生成安装目录中的 Uninstall.exe。
卸载可从 Windows 设置 > 应用、开始菜单卸载入口或安装目录运行 Uninstall.exe。
本压缩包的 Uninstall.cmd 也可以打开当前用户已安装版本的卸载向导。
卸载默认保留收藏和设置，可在卸载向导中自行勾选删除用户数据。

免安装版：解压后运行 文件管理辅助器.exe，无需安装 Python。
免安装版移除方式：退出程序后删除解压的程序文件夹。
两种版本共用 %LOCALAPPDATA%\\文件管理辅助器 中的收藏与设置。
卸载和删除程序文件均不会操作收藏指向的原文件或文件夹。

源码包：用于 GitHub 仓库和后续开发，包含源码、测试、图标和构建脚本。
运行版、安装版请放 GitHub Releases，源码包解压后上传仓库。
所有发布包不包含发布者的收藏、剪贴板、备份、测试数据和本机缓存。
""", encoding="utf-8-sig")
    portable_files = [(executable, "文件管理辅助器.exe"),
                      (ROOT / "使用说明.txt", "使用说明.txt"), (guide, guide.name)]
    archive(output / f"FileManagementAssistant-Portable-{version}-x64.zip",
            portable_files, f"FileManagementAssistant-{version}")
    archive(output / f"FileManagementAssistant-Complete-{version}-x64.zip",
            [*portable_files, (installer, installer_name),
             (ROOT / "tools" / "Uninstall.cmd", "Uninstall.cmd")],
            f"FileManagementAssistant-{version}")
    names = ["main.py", "requirements.txt", "README.md", "使用说明.txt", ".gitignore",
             "build.ps1", "build-installer.ps1", "installer.nsi", "启动辅助器.bat"]
    sources = [(ROOT / name, name) for name in names]
    for folder, suffixes in (("quick_access", {".py", ".png", ".ico"}),
                             ("tests", {".py"}), ("tools", {".py", ".cmd"}),
                             ("assets", {".md", ".png"})):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in suffixes:
                sources.append((path, path.relative_to(ROOT).as_posix()))
    archive(output / f"FileManagementAssistant-Source-{version}.zip", sources,
            f"FileManagementAssistant-Source-{version}")
    notes = output / "RELEASE_NOTES.md"
    notes.write_text(f"""# 文件管理辅助器 v{version}

简洁的 Windows 文件、文件夹、快捷方式和网页入口管理工具。

- 支持拖动排序、文件复制、原文件重命名、所在位置打开和回收站删除确认。
- 快捷方式独立收藏，协同开启及预设保存。
- 剪贴板可混排文字和多张图片，支持折叠、排序、调整高度与自动保存。
- 后台托盘与悬浮球，支持贴边半球、100%–200% 大小调整。
- 主界面可开关悬浮球；关闭后需手动开启，设置重启保留。
- 最小化保留任务栏入口，主面板隐藏时剪贴板继续显示。

## 下载选择

- Setup：安装版，安装后自动生成 Uninstall.exe。
- Portable：解压即用。
- Complete：运行程序、安装程序、卸载入口和说明的完整包。
- Source：源码、测试及构建脚本。

升级保留现有收藏和设置。发布包不包含发布者个人数据。
""", encoding="utf-8")
    hashes = []
    for path in sorted(output.iterdir()):
        if path.is_file():
            hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    for bundle_path in output.glob("*.zip"):
        with zipfile.ZipFile(bundle_path) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError(f"Invalid archive: {bundle_path}")
            for name in bundle.namelist():
                if set(Path(name).parts) & {"data", "backup", ".qa", ".vendor", "__pycache__"}:
                    raise RuntimeError(f"Private path in archive: {name}")
    print(output)
    for path in sorted(output.iterdir()):
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
