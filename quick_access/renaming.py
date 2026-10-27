"""Validate actual Windows file names without accepting a different location."""

from .storage import EntryError


def validate_source_name(name: str) -> str:
    if not name or not name.strip() or name in (".", ".."):
        raise EntryError("请输入新的文件或文件夹名称。")
    if any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name):
        raise EntryError('名称不能包含 <>:"/\\|?* 或换行等控制字符。')
    if name.endswith((".", " ")):
        raise EntryError("名称不能以句点或空格结尾。")
    devices = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
    devices.update(prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³")
    if name.split(".", 1)[0].rstrip(" ").upper() in devices:
        raise EntryError("这个名称是 Windows 保留名称，请换一个名称。")
    if len(name.encode("utf-16-le")) // 2 > 255:
        raise EntryError("原文件或文件夹名称最多支持 255 个字符。")
    return name
