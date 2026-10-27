"""A small right-side window of user-created, collapsible text cards."""

import copy
import base64
import io
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk
from PIL import Image, ImageGrab, ImageTk

from .file_clipboard import write_text, write_image
from .scroll_frame import ScrollFrame
from .storage import EntryError


DEFAULT_CARD_HEIGHT = 150
MIN_CARD_HEIGHT = 90
MAX_CARD_HEIGHT = 480
CLIPBOARD_GAP = 6


def dock_rect(main, panel_width, work, gap=CLIPBOARD_GAP, min_width=300):
    """Place a panel near the main window with a small, deliberate gap."""
    left, top, right, bottom = main
    wl, wt, wr, wb = work
    height = max(1, min(bottom, wb) - max(top, wt))
    y = max(wt, min(top, wb - height))
    right_space = max(0, wr - right - gap)
    left_space = max(0, left - wl - gap)
    if right_space >= min_width:
        width = min(panel_width, right_space)
        return right + gap, y, width, height
    if left_space >= min_width:
        width = min(panel_width, left_space)
        return left - gap - width, y, width, height
    if right_space >= left_space:
        return right + gap, y, max(1, right_space), height
    width = max(1, left_space)
    return left - gap - width, y, width, height


class ClipboardPanel(tk.Toplevel):
    def __init__(self, parent, workspace, on_visibility_change=None):
        super().__init__(parent)
        self.withdraw()
        self.parent, self.workspace = parent, workspace
        self.on_visibility_change = on_visibility_change
        self.title("剪贴板")
        self.minsize(300, 320)
        self.records = copy.deepcopy(workspace.notes)
        self.cards = {}
        self._save_job = self._dock_job = None
        self._resize_state = None
        self._drag_state = None
        self._drag_indicator = None
        self._rendering = False
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Label(bar, text="剪贴板", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.add_button = ttk.Button(bar, text="＋", width=3, command=self.add_card)
        self.add_button.pack(side="right")
        self.scroller = ScrollFrame(self, prominent=True)
        self.scroller.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="拖动卡片左侧手柄排序；滚轮浏览。")
        ttk.Label(self, textvariable=self.status_var, padding=8, wraplength=300).pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self._parent_binding = parent.bind("<Configure>", self._parent_moved, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.render()
        self.show()

    def show(self):
        self.deiconify()
        self.update_idletasks()
        self.dock()
        self.lift()
        if self.on_visibility_change is not None:
            self.on_visibility_change(True)

    def dock(self):
        self._dock_job = None
        if self.parent.state() not in ("normal", "zoomed"):
            return
        width = max(300, self.winfo_width() if self.winfo_width() > 1 else 330)
        main = (self.parent.winfo_rootx(), self.parent.winfo_rooty(),
                self.parent.winfo_rootx() + self.parent.winfo_width(),
                self.parent.winfo_rooty() + self.parent.winfo_height())
        work = (self.winfo_vrootx(), self.winfo_vrooty(),
                self.winfo_vrootx() + self.winfo_screenwidth(),
                self.winfo_vrooty() + self.winfo_screenheight())
        x, y, width, height = dock_rect(main, width, work)
        self.geometry(f"{width}x{max(320, height)}+{x}+{y}")

    def _parent_moved(self, event):
        if (event.widget is self.parent and self.parent.state() in ("normal", "zoomed")
                and self.winfo_exists() and self.state() != "withdrawn"):
            if self._dock_job is not None:
                self.after_cancel(self._dock_job)
            self._dock_job = self.after(80, self.dock)

    def render(self):
        self._cancel_card_drag()
        self._rendering = True
        for widget in self.scroller.content.winfo_children():
            widget.destroy()
        self.cards.clear()
        if not self.records:
            ttk.Label(self.scroller.content, text="点击 ＋ 创建一张文本卡片。", padding=12).pack()
        for record in self.records:
            card = ttk.Frame(self.scroller.content, padding=8, relief="solid", borderwidth=1)
            card.pack(fill="x", pady=(0, 8))
            header = ttk.Frame(card)
            header.pack(fill="x")
            drag_handle = tk.Label(header, text="☰", width=2, cursor="fleur",
                                   bg="#EEF2F5", fg="#607080",
                                   font=("Microsoft YaHei UI", 10))
            drag_handle.pack(side="left", padx=(0, 3))
            drag_handle.bind("<ButtonPress-1>",
                             lambda event, ident=record["id"]: self._drag_start(event, ident))
            drag_handle.bind("<B1-Motion>", self._drag_motion)
            drag_handle.bind("<ButtonRelease-1>", self._drag_end)
            toggle = ttk.Button(header, text="▸" if record["collapsed"] else "▾", width=2,
                                command=lambda ident=record["id"]: self.toggle_card(ident))
            toggle.pack(side="left")
            title = tk.StringVar(value=record["title"])
            title_input = ttk.Entry(header, textvariable=title, width=12,
                                    validate="key", validatecommand=(self.register(lambda value: len(value) <= 80), "%P"))
            title_input.pack(side="left", fill="x", expand=True, padx=5)
            ttk.Button(header, text="复制", width=4,
                       command=lambda ident=record["id"]: self.copy_card(ident)).pack(side="left")
            ttk.Button(header, text="×", width=2,
                       command=lambda ident=record["id"]: self.remove_card(ident)).pack(side="left", padx=(3, 0))
            content = ttk.Frame(card, height=record.get("height", DEFAULT_CARD_HEIGHT))
            content.pack_propagate(False)
            editor = ttk.Frame(content)
            text = tk.Text(editor, wrap="word", undo=True, relief="solid", bd=1,
                           font=("Microsoft YaHei UI", 10), padx=6, pady=5)
            scrollbar = ttk.Scrollbar(editor, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            grip = tk.Frame(content, height=8, bg="#D8DEE5", cursor="sb_v_double_arrow",
                            highlightthickness=0, bd=0)
            grip.pack(fill="x", side="bottom", pady=(3, 0))
            grip.pack_propagate(False)
            line = tk.Frame(grip, height=1, width=34, bg="#8D99A6", cursor="sb_v_double_arrow")
            line.place(relx=0.5, rely=0.5, anchor="center")
            editor.pack(fill="both", expand=True)
            for handle in (grip, line):
                handle.bind("<ButtonPress-1>",
                            lambda event, ident=record["id"]: self._resize_start(event, ident))
                handle.bind("<B1-Motion>", self._resize_motion)
                handle.bind("<ButtonRelease-1>", self._resize_end)
            images = {}
            parts = record.get("content", [{"text": record["text"]}])
            if "content" not in record and record.get("image"):
                parts = [*parts, {"image": record["image"]}]
            for part in parts:
                if "text" in part:
                    text.insert("end", part["text"])
                else:
                    self._insert_image(text, images, part["image"], "end-1c")
            text.bind("<<Paste>>", lambda event, ident=record["id"]: self._paste_event(event, ident))
            text.bind("<Button-3>", lambda event, ident=record["id"]: self._content_menu(event, ident))
            text.edit_modified(False)
            text.bind("<<Modified>>", lambda event: self._text_changed(event.widget))
            title.trace_add("write", lambda *_: self.schedule_save())
            if not record["collapsed"]:
                content.pack(fill="x", pady=(7, 0))
            self.cards[record["id"]] = {"title": title, "text": text, "content": content,
                                         "toggle": toggle, "grip": grip, "grip_line": line,
                                         "frame": card, "drag_handle": drag_handle}
            self.cards[record["id"]]["images"] = images
        self._rendering = False

    def _insert_image(self, text, images, encoded, index="insert"):
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as original:
            thumbnail = original.copy()
        thumbnail.thumbnail((250, 350))
        preview = ImageTk.PhotoImage(thumbnail, master=self)
        name = text.image_create(index, image=preview)
        images[name] = (encoded, preview)
        return name

    def _content_menu(self, event, ident):
        text = self.cards[ident]["text"]
        text.mark_set("insert", f"@{event.x},{event.y}")
        menu = tk.Menu(self, tearoff=False)
        image_name = text.image_cget("insert", "name") if text.dump(
            "insert", "insert+1c", image=True) else None
        if image_name:
            menu.add_command(label="复制图片", command=lambda: self._copy_image(ident, image_name))
        menu.add_command(label="粘贴", command=lambda: text.event_generate("<<Paste>>"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_image(self, ident, name):
        try:
            encoded = self.cards[ident]["images"][name][0]
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as original:
                write_image(self, original)
            self.status_var.set("已复制图片")
        except (EntryError, OSError, ValueError) as exc:
            self.status_var.set(str(exc))

    def _paste_event(self, event, ident):
        try:
            source = ImageGrab.grabclipboard()
            if isinstance(source, list):
                sources = []
                for path in source:
                    if Path(path).suffix.lower() in Image.registered_extensions():
                        with Image.open(path) as opened:
                            sources.append(opened.copy())
            else:
                sources = [source] if isinstance(source, Image.Image) else []
            if not sources:
                return None
            encoded_images = []
            for original in sources:
                stream = io.BytesIO()
                original.save(stream, format="PNG")
                encoded = base64.b64encode(stream.getvalue()).decode("ascii")
                if len(encoded) > 48_000_000:
                    raise ValueError("图片过大，请缩小后再粘贴（最大约 36 MB）。")
                encoded_images.append(encoded)
            card = self.cards[ident]
            text = card["text"]
            if text.tag_ranges("sel"):
                text.delete("sel.first", "sel.last")
            for encoded in encoded_images:
                self._insert_image(text, card["images"], encoded)
                text.insert("insert", "\n")
            text.see("insert")
            self.flush()
            return "break"
        except (OSError, ValueError, tk.TclError) as exc:
            self.status_var.set(f"图片粘贴失败：{exc}")
            return "break"

    def _drag_start(self, event, ident):
        self.collect()
        self._cancel_card_drag()
        self._drag_state = {
            "id": ident,
            "start_y": event.y_root,
            "active": False,
            "target": None,
            "order": [item["id"] for item in self.records],
        }

    def _drag_motion(self, event):
        state = self._drag_state
        if state is None:
            return
        if not state["active"]:
            if abs(event.y_root - state["start_y"]) < 5:
                return
            state["active"] = True
            source = self.cards[state["id"]]["frame"]
            source.configure(relief="ridge", borderwidth=2)
            self._drag_indicator = tk.Frame(self.scroller.content, height=3, bg="#397DB5")
            self.status_var.set("拖到目标位置后松开，顺序会自动保存。")

        others = [ident for ident in state["order"] if ident != state["id"]]
        target = len(others)
        for index, ident in enumerate(others):
            frame = self.cards[ident]["frame"]
            if event.y_root < frame.winfo_rooty() + frame.winfo_height() / 2:
                target = index
                break
        state["target"] = target
        self._place_drag_indicator(others, target)

        canvas_y = event.y_root - self.scroller.canvas.winfo_rooty()
        if canvas_y < 28:
            self.scroller.canvas.yview_scroll(-1, "units")
        elif canvas_y > self.scroller.canvas.winfo_height() - 28:
            self.scroller.canvas.yview_scroll(1, "units")

    def _place_drag_indicator(self, others, target):
        if self._drag_indicator is None:
            return
        self._drag_indicator.pack_forget()
        if target < len(others):
            self._drag_indicator.pack(fill="x", pady=(0, 5),
                                      before=self.cards[others[target]]["frame"])
        elif others:
            self._drag_indicator.pack(fill="x", pady=(0, 5),
                                      after=self.cards[others[-1]]["frame"])
        else:
            self._drag_indicator.pack(fill="x", pady=(0, 5))

    def _drag_end(self, _event):
        state = self._drag_state
        if state is None:
            return
        active, target = state["active"], state["target"]
        source_id, original_order = state["id"], state["order"]
        self._cancel_card_drag()
        if not active or target is None:
            return
        others = [ident for ident in original_order if ident != source_id]
        reordered = list(others)
        reordered.insert(target, source_id)
        if reordered == original_order:
            self.status_var.set("卡片顺序未改变。")
            return
        records = {item["id"]: item for item in self.records}
        previous = list(self.records)
        self.records = [records[ident] for ident in reordered]
        if not self.flush():
            self.records = previous
            return
        self.records = copy.deepcopy(self.workspace.notes)
        self.render()
        self.status_var.set("卡片顺序已保存。")

    def _cancel_card_drag(self):
        state, self._drag_state = self._drag_state, None
        if state is not None and state.get("id") in self.cards:
            self.cards[state["id"]]["frame"].configure(relief="solid", borderwidth=1)
        if self._drag_indicator is not None:
            self._drag_indicator.destroy()
            self._drag_indicator = None

    def collect(self):
        for record in self.records:
            widgets = self.cards[record["id"]]
            record["title"] = widgets["title"].get()
            record["text"] = widgets["text"].get("1.0", "end-1c")
            parts = []
            for kind, value, _ in widgets["text"].dump("1.0", "end-1c", text=True, image=True):
                if kind == "text":
                    parts.append({"text": value})
                elif kind == "image":
                    parts.append({"image": widgets["images"][value][0]})
            if any("image" in part for part in parts):
                record["content"] = parts
            else:
                record.pop("content", None)
            record.pop("image", None)
            record["height"] = max(MIN_CARD_HEIGHT, min(MAX_CARD_HEIGHT,
                                                        int(widgets["content"].cget("height"))))
        return self.records

    def _resize_start(self, event, ident):
        content = self.cards[ident]["content"]
        self._resize_state = (ident, event.y_root, content.winfo_height())
        event.widget.grab_set()

    def _resize_motion(self, event):
        if self._resize_state is None:
            return
        ident, start_y, start_height = self._resize_state
        height = max(MIN_CARD_HEIGHT, min(MAX_CARD_HEIGHT, start_height + event.y_root - start_y))
        self.cards[ident]["content"].configure(height=height)
        self.scroller.canvas.configure(scrollregion=self.scroller.canvas.bbox("all"))
        self.schedule_save()

    def _resize_end(self, event):
        if self._resize_state is None:
            return
        self._resize_state = None
        event.widget.grab_release()
        self.flush()

    def _text_changed(self, text):
        if text.edit_modified():
            text.edit_modified(False)
            self.schedule_save()

    def schedule_save(self):
        if self._rendering:
            return
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(450, self.flush)
        self.status_var.set("正在编辑…")

    def flush(self):
        if self._save_job is not None:
            self.after_cancel(self._save_job)
            self._save_job = None
        try:
            self.workspace.save_notes(self.collect())
        except OSError as exc:
            self.status_var.set(f"保存失败，请保留此窗口并重试：{exc}")
            return False
        self.status_var.set("已自动保存")
        return True

    def add_card(self):
        if not self.flush():
            return
        record = {"id": uuid.uuid4().hex, "title": f"剪贴板 {len(self.records) + 1}",
                  "text": "", "collapsed": False, "height": DEFAULT_CARD_HEIGHT}
        try:
            self.workspace.save_notes([*self.records, record])
        except OSError as exc:
            self.status_var.set(f"创建失败：{exc}")
            return
        self.records = copy.deepcopy(self.workspace.notes)
        self.render()
        self.update_idletasks()
        self.scroller.canvas.yview_moveto(1)
        self.cards[record["id"]]["text"].focus_set()

    def toggle_card(self, ident):
        record = next(item for item in self.records if item["id"] == ident)
        record["collapsed"] = not record["collapsed"]
        widgets = self.cards[ident]
        widgets["toggle"].configure(text="▸" if record["collapsed"] else "▾")
        if record["collapsed"]:
            widgets["content"].pack_forget()
        else:
            widgets["content"].pack(fill="x", pady=(7, 0))
        self.flush()

    def copy_card(self, ident):
        text = self.cards[ident]["text"].get("1.0", "end-1c")
        try:
            self.collect()
            record = next(item for item in self.records if item["id"] == ident)
            images = [part["image"] for part in record.get("content", []) if "image" in part]
            if images:
                from .file_clipboard import write_rich_content
                write_rich_content(self, record["content"])
            else:
                write_text(self, text)
        except (EntryError, OSError, ValueError) as exc:
            self.status_var.set(str(exc))
            return
        self.status_var.set("已复制卡片内容")

    def remove_card(self, ident):
        if not messagebox.askyesno("删除卡片", "删除这张卡片及其内容？", parent=self):
            return
        if not self.flush():
            return
        try:
            self.workspace.save_notes([item for item in self.records if item["id"] != ident])
        except OSError as exc:
            self.status_var.set(f"删除失败：{exc}")
            return
        self.records = copy.deepcopy(self.workspace.notes)
        self.render()

    def hide(self):
        self._cancel_card_drag()
        if self.flush():
            self.withdraw()
            if self.on_visibility_change is not None:
                self.on_visibility_change(False)
            return True
        return False

    def _destroyed(self, event):
        if event.widget is self:
            self.parent.unbind("<Configure>", self._parent_binding)
            for job in (self._save_job, self._dock_job):
                if job is not None:
                    self.after_cancel(job)
