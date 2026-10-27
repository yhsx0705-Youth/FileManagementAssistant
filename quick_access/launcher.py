"""Open shortcuts in a chosen order, without blocking the Tk event loop."""

import os

from .storage import EntryError, path_key


def ordered_shortcuts(rows):
    """Validate user-assigned positive, unique sequence numbers."""
    ordered = []
    for entry_id, selected, value in rows:
        if not selected:
            continue
        try:
            number = int(value)
            if number < 1:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise EntryError("选中的快捷方式需要填写大于 0 的整数序号。") from exc
        ordered.append((number, entry_id))
    if not ordered:
        raise EntryError("请至少勾选一个快捷方式。")
    if len({number for number, _ in ordered}) != len(ordered):
        raise EntryError("启动序号不能重复，请调整后再试。")
    return [entry_id for _, entry_id in sorted(ordered)]


class SequentialLauncher:
    def __init__(self, root, store, on_progress, on_done):
        self.root, self.store = root, store
        self.on_progress, self.on_done = on_progress, on_done
        self.job = None
        self.running = False
        self.queue = []
        self.opened = 0
        self.index = 0
        self.skipped = []

    def start(self, entry_ids, interval_ms=1000):
        if self.running:
            raise EntryError("正在协同开启，请先等待完成或停止。")
        if not entry_ids or len(set(entry_ids)) != len(entry_ids):
            raise EntryError("请选择有效且不重复的快捷方式。")
        queue = []
        for entry_id in entry_ids:
            entry = self.store.get(entry_id)
            if entry.kind != "shortcut" or not os.path.isfile(self.store.effective_path(entry)):
                raise EntryError(f"快捷方式已失效，请重新选择：{entry.title}")
            queue.append({"entry_id": entry_id, "path": entry.path, "title": entry.title, "snapshot": False})
        self._begin(queue, interval_ms)

    def start_items(self, items, interval_ms=1000):
        if self.running:
            raise EntryError("正在协同开启，请先等待完成或停止。")
        if not items:
            raise EntryError("预设中没有启动项目。")
        self._begin([{**item, "snapshot": True} for item in items], interval_ms)

    def _begin(self, queue, interval_ms):
        self.queue, self.opened, self.index, self.interval_ms = queue, 0, 0, interval_ms
        self.skipped = []
        self.running = True
        self.job = self.root.after(0, self._next)

    def _next(self):
        self.job = None
        item = self.queue[self.index]
        try:
            if item["snapshot"]:
                if not item["path"] or not os.path.isfile(item["path"]):
                    raise EntryError("快捷方式副本当前不可用")
                os.startfile(item["path"])
            else:
                entry = self.store.get(item["entry_id"])
                if entry.kind != "shortcut" or path_key(entry.path) != path_key(item["path"]):
                    raise EntryError("启动期间快捷方式已变化，请重新选择。")
                self.store.open_entry(entry.id)
        except (EntryError, OSError) as exc:
            if not item["snapshot"]:
                self.running = False
                self.on_done(self.opened, str(exc))
                return
            self.skipped.append(f"{item['title']}：{exc}")
        else:
            self.opened += 1
            self.on_progress(self.opened, len(self.queue), item["title"])
        self.index += 1
        if self.index == len(self.queue):
            self.running = False
            summary = f"已跳过 {len(self.skipped)} 个不可用项目：" + "；".join(self.skipped) if self.skipped else None
            self.on_done(self.opened, summary)
        else:
            self.job = self.root.after(self.interval_ms, self._next)

    def cancel(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        was_running, self.running = self.running, False
        if was_running:
            self.on_done(self.opened, "已停止后续启动，已打开的程序会保留。")
