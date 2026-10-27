"""Normalize filesystem targets and web links used by shortcut entries."""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

WEB_KINDS = ("http", "https")
_BARE_WEB_ADDRESS = re.compile(r"^[^\s\\/:]+\.[^\s\\/:]+(?::\d+)?(?:/.*)?$")
_WEB_TLDS = {
    "ac", "ai", "app", "art", "au", "biz", "ca", "cat", "cc", "ch", "cn", "co", "com",
    "de", "dev", "edu", "es", "eu", "fr", "gov", "hk", "id", "ie", "info", "in", "int",
    "io", "it", "jp", "kr", "me", "mil", "mobi", "museum", "mx", "name", "net", "nl",
    "no", "nz", "online", "org", "ph", "pro", "ru", "se", "sg", "shop", "site", "tech",
    "tk", "top", "tv", "tw", "uk", "us", "vn", "wiki", "xyz", "中国", "公司", "网络",
}


def _is_bare_web_address(value: str) -> bool:
    match = _BARE_WEB_ADDRESS.fullmatch(value)
    if not match:
        return False
    host = value.split("/", 1)[0].rsplit(":", 1)[0].casefold()
    suffix = host.rsplit(".", 1)[-1]
    return host.startswith("www.") or suffix in _WEB_TLDS


def _raise_entry_error(message: str, cause=None):
    from .storage import EntryError
    error = EntryError(message)
    if cause is not None:
        raise error from cause
    raise error


def normalize_web_url(value: str) -> str:
    value = value.strip().strip("\"'")
    if _is_bare_web_address(value):
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in WEB_KINDS or not parsed.hostname:
            raise ValueError("unsupported web address")
        parsed.port
    except ValueError as exc:
        _raise_entry_error("请输入有效的 http 或 https 网页地址。", exc)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/",
                       parsed.query, parsed.fragment))


def is_web_address(value: str) -> bool:
    value = value.strip().strip("\"'")
    return value.lower().startswith(("http://", "https://")) or _is_bare_web_address(value)


def _url_from_shortcut(path: str) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        content = raw.decode("utf-16")
    else:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("mbcs" if os.name == "nt" else "cp1252")
    try:
        parser.read_string(content)
        return normalize_web_url(parser.get("InternetShortcut", "URL"))
    except (configparser.Error, UnicodeError) as exc:
        _raise_entry_error("无法读取此网页快捷方式中的网址。", exc)


def normalize_target(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if is_web_address(value):
        return normalize_web_url(value)
    from .storage import normalize_path
    path = normalize_path(value)
    if Path(path).suffix.lower() == ".url" and os.path.isfile(path):
        try:
            return _url_from_shortcut(path)
        except ValueError:
            return path
    return path


def target_kind(value: str) -> str:
    if is_web_address(value):
        return "web"
    path = Path(value)
    if os.path.isdir(value):
        return "folder"
    if path.suffix.lower() in (".lnk", ".url"):
        return "shortcut"
    return "file"


def target_key(value: str) -> str:
    if is_web_address(value):
        parsed = urlsplit(normalize_web_url(value))
        return urlunsplit((parsed.scheme, parsed.netloc.casefold(), parsed.path,
                           parsed.query, parsed.fragment))
    return os.path.normcase(os.path.normpath(value))


def target_title(value: str, kind: str) -> str:
    if kind == "web":
        return urlsplit(value).hostname or value
    path = Path(value)
    if kind == "shortcut":
        return path.stem or path.name or value
    return path.name or value
