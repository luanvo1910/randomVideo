"""App ghép ngẫu nhiên nhiều video thành các kết quả khác nhau."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from engine import (
    BatchResult,
    find_tools,
    fmt_count,
    list_videos,
    plan_outputs,
    render_job,
)
from updater import APP_VERSION, download_release, fetch_latest, is_newer, launch_replacer, running_as_exe

def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SETTINGS_PATH = _app_dir() / "settings.json"
ERROR_LOG = _app_dir() / "error.log"

BG = "#171a21"
CARD = "#212633"
BORDER = "#31384a"
TEXT = "#e7ebf5"
MUTED = "#9aa3b8"
ACCENT = "#6c9eff"
ACCENT_HOVER = "#8bb2ff"
ACCENT_TEXT = "#10141d"
ENTRY = "#141821"
WARNING = "#e0af68"
DANGER = "#f07178"
OK = "#8bd5a4"
SECONDARY = "#2b3142"
SECONDARY_HOVER = "#394156"


def enable_dpi() -> None:
    from engine import silence_windows_crash_dialogs

    silence_windows_crash_dialogs()
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def install_excepthook(root: tk.Tk) -> None:
    def hook(exc_type, value, tb) -> None:
        detail = "".join(traceback.format_exception(exc_type, value, tb))
        try:
            ERROR_LOG.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        try:
            messagebox.showerror(
                "Lỗi",
                f"{value}\n\nChi tiết đã ghi vào error.log",
                parent=root,
            )
        except tk.TclError:
            pass

    sys.excepthook = hook


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"Ghép video ngẫu nhiên {APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.minsize(760, 600)
        self._center(1040, 900)
        install_excepthook(self.root)

        self.events: queue.Queue = queue.Queue()
        self.videos: list[Path] = []
        self.scan_id = 0
        self.scan_job: str | None = None
        self.scanning = False
        self.running = False
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.job_notice: str | None = None
        self.pending_update = None
        self.updating = False
        self.update_cancel = threading.Event()
        self.tools = None
        self.tools_error: str | None = None
        self._lockable: list[tk.Widget] = []
        self._wrapping: list[tuple[tk.Label, float]] = []
        self._wrap_width = 0

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.k_var = tk.StringVar(value="3")
        self.count_var = tk.StringVar(value="5")
        self.orientation = tk.StringVar(value="landscape")
        self.quality = tk.StringVar(value="1080")
        self.recursive = tk.BooleanVar(value=False)
        self.found_var = tk.StringVar(value="Chưa chọn thư mục nguồn.")
        self.plan_var = tk.StringVar(value="Hãy chọn thư mục chứa ít nhất 2 video.")
        self.size_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self.ffmpeg_var = tk.StringVar(value="Đang kiểm tra FFmpeg...")

        self._style_progress()
        self._build()
        self._bind_updates()
        self.root.bind("<Configure>", self._on_resize)
        self._load_settings()
        self._refresh_size()
        self._refresh_plan()
        self.root.after(50, self._detect_ffmpeg)
        self.root.after(100, self._poll)
        self.root.after(600, self._check_for_update)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    def _center(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(720, min(width, screen_w - 32))
        height = max(560, min(height, screen_h - 72))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _style_progress(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Work.Horizontal.TProgressbar",
            troughcolor=ENTRY,
            background=ACCENT,
            bordercolor=ENTRY,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=10,
        )

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=2)
        self.root.rowconfigure(6, weight=1, minsize=150)

        tk.Frame(self.root, bg=ACCENT, height=4).grid(row=0, column=0, sticky="ew")

        header = tk.Frame(self.root, bg=BG)
        header.grid(row=1, column=0, sticky="ew", padx=22, pady=(16, 8))
        tk.Label(
            header,
            text="Ghép video ngẫu nhiên",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 22),
            anchor="w",
        ).pack(fill="x")
        subtitle = tk.Label(
            header,
            text="Trộn ngẫu nhiên video trong thư mục rồi ghép thành các kết quả. Mỗi video nguồn chỉ dùng đúng một lần, không lặp lại.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 11),
            anchor="w",
            justify="left",
            wraplength=860,
        )
        subtitle.pack(fill="x", pady=(4, 0))
        self._wrapping.append((subtitle, 1.0))

        split = tk.Frame(self.root, bg=BG)
        split.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 8))
        split.columnconfigure(0, weight=5)
        split.columnconfigure(1, weight=6)
        split.rowconfigure(0, weight=1)
        source = self._card(split, row=0, column=0, sticky="nsew", padx=(0, 8))
        self._section(source, "Thư mục video nguồn")
        self.input_entry = self._path_row(source, self.input_var, self._choose_input)
        self.recursive_check = tk.Checkbutton(
            source,
            text="Đọc cả thư mục con",
            variable=self.recursive,
            command=self._scan,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=ENTRY,
            font=("Segoe UI", 10),
            highlightthickness=0,
        )
        self.recursive_check.pack(anchor="w", padx=14, pady=(8, 0))
        self._lockable.append(self.recursive_check)
        tk.Label(
            source,
            textvariable=self.found_var,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(6, 4))

        list_wrap = tk.Frame(source, bg=CARD)
        list_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        list_wrap.rowconfigure(0, weight=1)
        list_wrap.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(
            list_wrap,
            bg=ENTRY,
            fg=TEXT,
            selectbackground=ACCENT,
            selectforeground=ACCENT_TEXT,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 10),
            height=5,
        )
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.bind("<MouseWheel>", self._wheel_list)

        options = self._card(split, row=0, column=1, sticky="nsew")
        self._section(options, "Cách ghép và thư mục xuất")
        self.output_entry = self._path_row(options, self.output_var, self._choose_output)

        form = tk.Frame(options, bg=CARD)
        form.pack(fill="x", padx=14, pady=(10, 0))
        self.k_spin = self._number_field(form, "Số video ghép trong mỗi kết quả", self.k_var, 2, 500)
        self.count_spin = self._number_field(form, "Số kết quả muốn xuất", self.count_var, 1, 99999)

        choice = tk.Frame(options, bg=CARD)
        choice.pack(fill="x", padx=14, pady=(4, 0))
        self.orientation_buttons = self._radio_group(
            choice,
            "Khổ video xuất",
            self.orientation,
            (("Ngang 16:9", "landscape"), ("Dọc 9:16", "portrait")),
        )
        self.quality_buttons = self._radio_group(
            choice,
            "Độ phân giải",
            self.quality,
            (("Full HD", "1080"), ("HD", "720")),
        )

        tk.Label(
            options,
            textvariable=self.size_var,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 0))
        self.plan_label = tk.Label(
            options,
            textvariable=self.plan_var,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.plan_label.pack(fill="x", padx=14, pady=(4, 0))
        hint = tk.Label(
            options,
            text="Mỗi kết quả ghép ít nhất 2 video khác nhau, chọn và xếp ngẫu nhiên, không theo tên file. Mỗi video nguồn chỉ dùng đúng 1 lần trong toàn bộ kết quả. Video đã đúng khổ dọc hoặc ngang được giữ nguyên.",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
            wraplength=420,
        )
        hint.pack(fill="x", padx=14, pady=(4, 14))
        self.hint_label = hint
        options.bind("<Configure>", self._fit_options_text, add="+")

        actions = tk.Frame(self.root, bg=BG)
        actions.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 8))
        self.start_button = self._button(actions, "Bắt đầu ghép", self._start, primary=True)
        self.start_button.pack(side="left")
        self.stop_button = self._button(actions, "Dừng", self._stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_button = self._button(actions, "Mở thư mục xuất", self._open_output)
        self.open_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")

        progress_row = tk.Frame(self.root, bg=BG)
        progress_row.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 8))
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            progress_row,
            style="Work.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )
        self.progress.grid(row=0, column=0, sticky="ew")
        tk.Label(
            progress_row,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.log_grip = tk.Frame(self.root, bg=BG, height=14, cursor="sb_v_double_arrow")
        self.log_grip.grid(row=5, column=0, sticky="ew", padx=22, pady=(0, 2))
        self.log_grip.grid_propagate(False)
        grip_bar = tk.Frame(self.log_grip, bg=BORDER, height=4, cursor="sb_v_double_arrow")
        grip_bar.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.18, height=4)
        for widget in (self.log_grip, grip_bar):
            widget.bind("<ButtonPress-1>", self._start_log_drag)
            widget.bind("<B1-Motion>", self._drag_log)
            widget.bind("<ButtonRelease-1>", self._end_log_drag)

        log_card = self._card(self.root, row=6, column=0, sticky="nsew", padx=22, pady=(0, 8))
        self.log_card = log_card
        log_header = tk.Frame(log_card, bg=CARD)
        log_header.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(
            log_header,
            text="Nhật ký",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
            anchor="w",
        ).pack(side="left")
        self.log_toggle = self._button(log_header, "Mở rộng", self._toggle_log)
        self.log_toggle.pack(side="right")
        self.log_expanded = False
        log_wrap = tk.Frame(log_card, bg=CARD)
        log_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        log_wrap.rowconfigure(0, weight=1)
        log_wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(
            log_wrap,
            bg="#141821",
            fg=TEXT,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 10),
            height=5,
            wrap="word",
            padx=10,
            pady=8,
            state="disabled",
        )
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.bind("<MouseWheel>", self._wheel_log)

        footer = tk.Frame(self.root, bg=BG)
        footer.grid(row=7, column=0, sticky="ew", padx=22, pady=(0, 12))
        tk.Label(
            footer,
            textvariable=self.ffmpeg_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x")

    def _card(self, parent: tk.Widget, **grid) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        frame.grid(**grid)
        return frame

    def _section(self, parent: tk.Frame, title: str) -> None:
        tk.Label(
            parent,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI Semibold", 12),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 8))

    def _path_row(self, parent: tk.Frame, variable: tk.StringVar, command) -> tk.Entry:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=14)
        entry = tk.Entry(
            row,
            textvariable=variable,
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        button = self._button(row, "Chọn thư mục", command)
        button.pack(side="left")
        self._lockable.extend((entry, button))
        return entry

    def _number_field(self, parent: tk.Frame, label: str, variable: tk.StringVar, low: int, high: int) -> tk.Spinbox:
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill="x", pady=(0, 8))
        tk.Label(box, text=label, bg=CARD, fg=MUTED, font=("Segoe UI", 10), anchor="w").pack(fill="x")
        spin = tk.Spinbox(
            box,
            from_=low,
            to=high,
            textvariable=variable,
            bg=ENTRY,
            fg=TEXT,
            buttonbackground=SECONDARY,
            relief="flat",
            justify="center",
            font=("Segoe UI", 13),
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            insertbackground=TEXT,
            width=12,
        )
        spin.pack(anchor="w", pady=(4, 0), ipady=4)
        self._lockable.append(spin)
        return spin

    def _radio_group(self, parent: tk.Frame, label: str, variable: tk.StringVar, options: tuple[tuple[str, str], ...]) -> list[tk.Radiobutton]:
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill="x", pady=(0, 8))
        tk.Label(box, text=label, bg=CARD, fg=MUTED, font=("Segoe UI", 10), anchor="w").pack(fill="x")
        buttons: list[tk.Radiobutton] = []
        row = tk.Frame(box, bg=CARD)
        row.pack(anchor="w", pady=(4, 0))
        for text, value in options:
            button = tk.Radiobutton(
                row,
                text=text,
                variable=variable,
                value=value,
                command=self._on_option_change,
                bg=CARD,
                fg=TEXT,
                activebackground=CARD,
                activeforeground=TEXT,
                selectcolor=ENTRY,
                font=("Segoe UI", 11),
                highlightthickness=0,
            )
            button.pack(side="left", padx=(0, 16))
            buttons.append(button)
            self._lockable.append(button)
        return buttons

    def _button(self, parent: tk.Widget, text: str, command, primary: bool = False) -> tk.Button:
        normal = ACCENT if primary else SECONDARY
        hover = ACCENT_HOVER if primary else SECONDARY_HOVER
        foreground = ACCENT_TEXT if primary else TEXT
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=normal,
            fg=foreground,
            activebackground=hover,
            activeforeground=foreground,
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=("Segoe UI Semibold", 11),
            cursor="hand2",
            disabledforeground="#6d7690",
        )

        def on_enter(_event, widget=button, color=hover) -> None:
            if str(widget.cget("state")) == "normal":
                widget.configure(bg=color)

        def on_leave(_event, widget=button, color=normal) -> None:
            if str(widget.cget("state")) == "normal":
                widget.configure(bg=color)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)
        return button

    def _bind_updates(self) -> None:
        self.k_var.trace_add("write", lambda *_: self._refresh_plan())
        self.count_var.trace_add("write", lambda *_: self._refresh_plan())
        self.input_var.trace_add("write", self._schedule_scan)
        self.output_var.trace_add("write", lambda *_: self._refresh_buttons())

    def _on_option_change(self) -> None:
        self._refresh_size()
        self._refresh_plan()

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        width = event.width
        if abs(width - self._wrap_width) < 24:
            return
        self._wrap_width = width
        for label, ratio in self._wrapping:
            label.configure(wraplength=max(220, int((width - 80) * ratio)))

    def _set_log_height(self, pixels: int) -> None:
        screen_h = self.root.winfo_screenheight()
        window_h = max(self.root.winfo_height(), 640)
        limit = max(220, min(screen_h - 160, window_h - 240))
        pixels = max(120, min(pixels, limit))
        needed = min(screen_h - 48, pixels + 300)
        if self.root.winfo_height() < needed:
            x = self.root.winfo_x()
            y = max(0, min(self.root.winfo_y(), screen_h - needed - 8))
            self.root.geometry(f"{max(self.root.winfo_width(), 760)}x{needed}+{x}+{y}")
        self.root.rowconfigure(6, minsize=pixels)
        self._log_minsize = pixels

    def _toggle_log(self) -> None:
        self.log_expanded = not self.log_expanded
        if self.log_expanded:
            self._set_log_height(max(360, int(self.root.winfo_height() * 0.58)))
            self.log_toggle.configure(text="Thu gọn")
        else:
            self._set_log_height(150)
            self.log_toggle.configure(text="Mở rộng")
        self.log.see("end")

    def _start_log_drag(self, event) -> None:
        self._drag_y = event.y_root
        self._drag_start = self.log_card.winfo_height()

    def _drag_log(self, event) -> None:
        self._set_log_height(self._drag_start + (self._drag_y - event.y_root))
        expanded = self._log_minsize > 240
        if expanded != self.log_expanded:
            self.log_expanded = expanded
            self.log_toggle.configure(text="Thu gọn" if expanded else "Mở rộng")

    def _end_log_drag(self, _event) -> None:
        self.log.see("end")

    def _fit_options_text(self, event) -> None:
        width = max(180, event.width - 36)
        self.plan_label.configure(wraplength=width)
        self.hint_label.configure(wraplength=width)

    def _schedule_scan(self, *_args) -> None:
        if self.scan_job is not None:
            try:
                self.root.after_cancel(self.scan_job)
            except tk.TclError:
                pass
        self.scan_job = self.root.after(300, self._scan)

    def _is_drive_root(self, folder: Path) -> bool:
        try:
            resolved = folder.resolve()
        except OSError:
            return False
        return resolved.parent == resolved

    def _existing_dir(self, raw: str) -> str | None:
        if not raw:
            return None
        path = Path(raw)
        if path.is_dir():
            return str(path)
        if path.parent.is_dir():
            return str(path.parent)
        return None

    def _wheel_list(self, event) -> None:
        self.listbox.yview_scroll(int(-event.delta / 120), "units")

    def _wheel_log(self, event) -> None:
        self.log.yview_scroll(int(-event.delta / 120), "units")

    def _ask_directory(self, title: str, current: str) -> str:
        options = {"title": title}
        initial = self._existing_dir(current)
        if initial:
            options["initialdir"] = initial
        selected = filedialog.askdirectory(**options)
        return selected or ""

    def _choose_input(self) -> None:
        selected = self._ask_directory("Chọn thư mục video nguồn", self.input_var.get().strip())
        if selected:
            self.input_var.set(selected)

    def _choose_output(self) -> None:
        selected = self._ask_directory("Chọn thư mục xuất video", self.output_var.get().strip())
        if selected:
            self.output_var.set(selected)

    def _open_output(self) -> None:
        folder = self.output_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showinfo("Thư mục xuất", "Hãy chọn thư mục xuất trước.", parent=self.root)
            return
        os.startfile(folder)

    def _scan(self) -> None:
        self.scan_job = None
        self.scan_id += 1
        scan_id = self.scan_id
        raw = self.input_var.get().strip()
        folder = Path(raw) if raw else None
        recursive = bool(self.recursive.get())
        if folder is not None and folder.is_dir() and recursive and self._is_drive_root(folder):
            self.scanning = False
            self.videos = []
            self._fill_list([])
            self.found_var.set("Thư mục này là cả ổ đĩa. Hãy chọn đúng thư mục video, hoặc tắt Đọc cả thư mục con.")
            self._refresh_plan()
            return
        if folder is None or not folder.is_dir():
            self.scanning = False
            self.videos = []
            self._fill_list([])
            self.found_var.set("Chưa chọn thư mục nguồn." if not raw else "Thư mục nguồn không hợp lệ.")
            self._refresh_plan()
            return
        self.scanning = True
        self.videos = []
        self._fill_list([])
        self.found_var.set("Đang đọc thư mục...")
        self.plan_var.set("Đang đọc thư mục...")
        self.plan_label.configure(fg=MUTED)
        self._refresh_buttons()

        def work() -> None:
            found = list_videos(folder, recursive)
            self.events.put(("scan", scan_id, found))

        threading.Thread(target=work, daemon=True).start()

    def _fill_list(self, videos: list[Path]) -> None:
        self.listbox.delete(0, "end")
        root = Path(self.input_var.get().strip()) if self.input_var.get().strip() else None
        for path in videos:
            if root and root.is_dir():
                try:
                    label = str(path.resolve().relative_to(root.resolve()))
                except (OSError, ValueError):
                    label = path.name
            else:
                label = path.name
            self.listbox.insert("end", label)

    def _integers(self) -> tuple[int | None, int | None]:
        return self._parse_int(self.k_var.get()), self._parse_int(self.count_var.get())

    def _parse_int(self, raw: str) -> int | None:
        text = raw.strip()
        if not text.isdigit():
            return None
        return int(text)

    def _output_size(self) -> tuple[int, int, str]:
        portrait = self.orientation.get() == "portrait"
        hd = self.quality.get() == "720"
        if portrait and hd:
            return 720, 1280, "Dọc HD 720×1280"
        if portrait:
            return 1080, 1920, "Dọc Full HD 1080×1920"
        if hd:
            return 1280, 720, "Ngang HD 1280×720"
        return 1920, 1080, "Ngang Full HD 1920×1080"

    def _refresh_size(self) -> None:
        _width, _height, label = self._output_size()
        self.size_var.set(f"Kích thước xuất: {label} · 30 khung hình/giây")

    def _refresh_plan(self) -> None:
        if self.scanning:
            self.plan_var.set("Đang đọc thư mục...")
            self.plan_label.configure(fg=MUTED)
            self._refresh_buttons()
            return
        clips, count = self._integers()
        if not self.input_var.get().strip():
            self.plan_var.set("Hãy chọn thư mục chứa ít nhất 2 video.")
            self.plan_label.configure(fg=MUTED)
        elif clips is None or count is None:
            self.plan_var.set("Hãy nhập số nguyên cho số video mỗi kết quả và số kết quả.")
            self.plan_label.configure(fg=WARNING)
        else:
            plan = plan_outputs(len(self.videos), clips, count)
            if plan.error:
                self.plan_var.set(plan.error)
                self.plan_label.configure(fg=DANGER)
            elif plan.notice:
                self.plan_var.set(
                    f"Chỉ tạo được tối đa {fmt_count(plan.maximum)} kết quả. "
                    f"App sẽ tạo {fmt_count(plan.actual)} video thay vì {fmt_count(plan.requested)}."
                )
                self.plan_label.configure(fg=WARNING)
            else:
                self.plan_var.set(
                    f"Có thể tạo tối đa {fmt_count(plan.maximum)} kết quả. "
                    f"Mỗi video nguồn chỉ dùng đúng 1 lần, cách ghép là ngẫu nhiên. "
                    f"Lần này sẽ tạo {fmt_count(plan.actual)} video."
                )
                self.plan_label.configure(fg=OK)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        if self.running or self.updating:
            return
        can_open = bool(self.output_var.get().strip())
        self.open_button.configure(state="normal" if can_open else "disabled")
        ready = not self.scanning and self.tools is not None
        self.start_button.configure(state="normal" if ready else "disabled")

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for widget in self._lockable:
            widget.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self._refresh_buttons()

    def _detect_ffmpeg(self) -> None:
        tools, error = find_tools()
        self.tools = tools
        self.tools_error = error
        if tools:
            self.ffmpeg_var.set(f"FFmpeg: {tools.ffmpeg}")
        else:
            self.ffmpeg_var.set(error or "Không tìm thấy FFmpeg.")
        self._refresh_buttons()

    def _start(self) -> None:
        if self.running or self.updating:
            return
        if self.tools is None:
            self._detect_ffmpeg()
        if self.tools is None:
            messagebox.showerror("Thiếu FFmpeg", self.tools_error or "Không tìm thấy FFmpeg.", parent=self.root)
            return
        if self.scanning:
            messagebox.showinfo("Đang đọc thư mục", "Hãy đợi app đọc xong danh sách video.", parent=self.root)
            return

        source = Path(self.input_var.get().strip())
        output = Path(self.output_var.get().strip())
        clips, count = self._integers()
        if not source.is_dir():
            messagebox.showerror("Thư mục nguồn", "Hãy chọn thư mục chứa video.", parent=self.root)
            return
        if not self.output_var.get().strip():
            messagebox.showerror("Thư mục xuất", "Hãy chọn thư mục để lưu video kết quả.", parent=self.root)
            return
        if clips is None or count is None:
            messagebox.showerror("Số lượng", "Hãy nhập số nguyên hợp lệ.", parent=self.root)
            return
        try:
            same_folder = source.resolve() == output.resolve()
            output_inside_source = output.resolve().is_relative_to(source.resolve())
        except OSError:
            messagebox.showerror("Thư mục", "Không đọc được đường dẫn thư mục vừa chọn.", parent=self.root)
            return
        if same_folder:
            messagebox.showerror(
                "Thư mục xuất",
                "Thư mục xuất phải khác thư mục nguồn để video gốc không bị lẫn với kết quả.",
                parent=self.root,
            )
            return
        if self.recursive.get() and output_inside_source:
            messagebox.showerror(
                "Thư mục xuất",
                "Thư mục xuất đang nằm trong thư mục nguồn. Hãy chọn thư mục khác, hoặc tắt đọc thư mục con.",
                parent=self.root,
            )
            return
        if not output.exists():
            create = messagebox.askyesno(
                "Tạo thư mục",
                f"Thư mục này chưa có:\n{output}\n\nTạo mới và xuất video vào đó?",
                parent=self.root,
            )
            if not create:
                return
            try:
                output.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Thư mục xuất", f"Không tạo được thư mục.\n{exc}", parent=self.root)
                return
        if not output.is_dir():
            messagebox.showerror("Thư mục xuất", "Đường dẫn xuất không phải là thư mục.", parent=self.root)
            return
        if self.recursive.get() and self._is_drive_root(source):
            messagebox.showerror(
                "Thư mục nguồn",
                "Không đọc cả ổ đĩa. Hãy chọn đúng thư mục chứa video, hoặc tắt Đọc cả thư mục con.",
                parent=self.root,
            )
            return

        if self.scan_job is not None:
            try:
                self.root.after_cancel(self.scan_job)
            except tk.TclError:
                pass
            self.scan_job = None
        self.scan_id += 1
        self.scanning = False
        self.status_var.set("Đang đọc thư mục...")
        self.root.update_idletasks()
        self.videos = list_videos(source, bool(self.recursive.get()))
        self._fill_list(self.videos)
        found = len(self.videos)
        if found == 0:
            self.found_var.set(
                "Không thấy video trong thư mục này. Nếu video nằm trong thư mục con, hãy bật Đọc cả thư mục con."
            )
        else:
            self.found_var.set(f"Đã tìm thấy {fmt_count(found)} video.")
        self._refresh_plan()

        plan = plan_outputs(len(self.videos), clips, count)
        if plan.error:
            messagebox.showerror("Không ghép được", plan.error, parent=self.root)
            return
        if plan.notice:
            proceed = messagebox.askokcancel("Số kết quả vượt quá số cách ghép", plan.notice + "\n\nTiếp tục?", parent=self.root)
            if not proceed:
                return
        elif plan.actual > 100:
            proceed = messagebox.askokcancel(
                "Số lượng lớn",
                f"Bạn sắp tạo {fmt_count(plan.actual)} video. Việc này có thể mất nhiều thời gian và dung lượng ổ đĩa.\n\nTiếp tục?",
                parent=self.root,
            )
            if not proceed:
                return

        width, height, size_label = self._output_size()
        self.job_notice = plan.notice
        self._save_settings()
        self._clear_log()
        self._log(f"Nguồn: {source}")
        self._log(f"Xuất: {output}")
        self._log(f"Khổ: {size_label}")
        self._log(f"Mỗi kết quả: {plan.clips_per_output} video · Số kết quả: {plan.actual}")
        if plan.notice:
            self._log(plan.notice)
        self.progress.configure(value=0, maximum=100)
        self.status_var.set("Đang chuẩn bị...")
        self.cancel.clear()
        self._set_running(True)

        videos = list(self.videos)
        tools = self.tools

        def work() -> None:
            try:
                result = render_job(
                    videos=videos,
                    source_root=source,
                    clips_per_output=plan.clips_per_output,
                    count=plan.actual,
                    output_dir=output,
                    width=width,
                    height=height,
                    size_label=size_label,
                    tools=tools,
                    cancel=self.cancel,
                    on_log=lambda message: self.events.put(("log", message)),
                    on_progress=lambda done, total, label: self.events.put(("progress", done, total, label)),
                    notice=plan.notice,
                )
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.running:
            self.cancel.set()
            self.status_var.set("Đang dừng...")
            self._log("Đang dừng sau tác vụ hiện tại...")

    def _poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, event) -> None:
        kind = event[0]
        if kind == "scan":
            _kind, scan_id, videos = event
            if scan_id != self.scan_id:
                return
            self.scanning = False
            self.videos = videos
            self._fill_list(videos)
            count = len(videos)
            if count == 0:
                extra = " Nếu video nằm trong thư mục con, hãy bật Đọc cả thư mục con."
                self.found_var.set("Không thấy video trong thư mục này." + extra)
            else:
                noun = "video" if count > 1 else "video"
                self.found_var.set(f"Đã tìm thấy {fmt_count(count)} {noun}.")
            self._refresh_plan()
        elif kind == "log":
            self._log(event[1])
        elif kind == "progress":
            _kind, done, total, label = event
            if total <= 0:
                self.progress.configure(value=0, maximum=1)
            else:
                self.progress.configure(maximum=total, value=done)
            percent = 0 if total <= 0 else int(done * 100 / total)
            self.status_var.set(f"{label} · {percent}%")
        elif kind == "done":
            self._finish(event[1])
        elif kind == "error":
            self._set_running(False)
            self.status_var.set("Có lỗi")
            self._log(event[1])
            messagebox.showerror("Không ghép được", event[1], parent=self.root)
        elif kind == "update-available":
            self.pending_update = event[1]
            if not self.running and not self.updating:
                self._begin_update()
        elif kind == "update-progress":
            received, total = event[1], event[2]
            if total > 0:
                percent = int(received * 100 / total)
                self.status_var.set(f"Đang tải bản mới · {percent}%")
            else:
                self.status_var.set("Đang tải bản mới...")
        elif kind == "update-failed":
            self.updating = False
            self.status_var.set("Không cập nhật được")
            self._log(event[1])
        elif kind == "update-ready":
            self._apply_update(Path(event[1]))

    def _finish(self, result: BatchResult) -> None:
        self._set_running(False)
        created = len(result.created)
        if result.cancelled:
            self.status_var.set(f"Đã dừng · tạo được {created} video")
        elif result.failures and created:
            self.status_var.set(f"Xong một phần · {created} video")
        elif result.failures:
            self.status_var.set("Không tạo được video")
        else:
            self.status_var.set(f"Đã tạo {created} video")

        lines: list[str] = []
        if result.cancelled:
            lines.append(f"Đã dừng. Tạo được {created} video.")
        elif created:
            lines.append(f"Đã tạo {created} video.")
        else:
            lines.append("Chưa tạo được video nào.")
        if self.job_notice:
            lines.extend(["", self.job_notice])
        if result.failures:
            lines.extend(["", f"Có {len(result.failures)} lỗi:"])
            lines.extend(f"• {item}" for item in result.failures[:6])
            if len(result.failures) > 6:
                lines.append("• Các lỗi còn lại nằm trong danh_sach_ghep.txt")
        if result.manifest:
            lines.extend(["", "Chi tiết từng video nằm trong danh_sach_ghep.txt"])
        if created:
            lines.extend(["", f"Thư mục xuất:\n{self.output_var.get().strip()}"])
        text = "\n".join(lines)

        if created and not result.failures:
            messagebox.showinfo("Đã ghép xong", text, parent=self.root)
        elif created:
            messagebox.showwarning("Ghép xong một phần", text, parent=self.root)
        elif result.cancelled:
            messagebox.showinfo("Đã dừng", text, parent=self.root)
        else:
            messagebox.showerror("Không ghép được", text, parent=self.root)

        if created and not result.cancelled:
            try:
                os.startfile(self.output_var.get().strip())
            except OSError:
                pass
        if self.pending_update and not self.updating:
            self.root.after(300, self._begin_update)

    def _check_for_update(self) -> None:
        def work() -> None:
            try:
                release = fetch_latest()
            except Exception:
                return
            if release and is_newer(release.version, APP_VERSION):
                self.events.put(("update-available", release))

        threading.Thread(target=work, daemon=True).start()

    def _begin_update(self) -> None:
        release = self.pending_update
        if release is None or self.updating or self.running:
            return
        self.pending_update = None
        if not running_as_exe():
            self._log(f"Có bản {release.version} trên GitHub. Bản này đang chạy từ mã nguồn nên không tự thay file.")
            return
        self.updating = True
        self.update_cancel.clear()
        self.start_button.configure(state="disabled")
        destination = _app_dir() / "GhepVideo.new"
        self.status_var.set(f"Đang tải bản {release.version}...")
        self._log(f"Có bản {release.version}. Đang tải và sẽ khởi động lại.")

        def work() -> None:
            try:
                download_release(
                    release.download_url,
                    destination,
                    lambda received, total: self.events.put(("update-progress", received, total)),
                    self.update_cancel,
                )
            except Exception as exc:
                self.events.put(("update-failed", f"Không tải được bản mới. {exc}"))
                return
            if self.update_cancel.is_set():
                return
            self.events.put(("update-ready", str(destination)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_update(self, new_file: Path) -> None:
        self.status_var.set("Đang khởi động lại bản mới...")
        try:
            launch_replacer(new_file, Path(sys.executable).resolve())
        except Exception as exc:
            self.updating = False
            self.status_var.set("Không cập nhật được")
            self._log(f"Không thay được file đang chạy. {exc}")
            return
        self._save_settings()
        self.root.destroy()

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.k_var.set(str(data.get("k", "3")))
        self.count_var.set(str(data.get("count", "5")))
        orientation = data.get("orientation", "landscape")
        quality = str(data.get("quality", "1080"))
        if orientation in {"landscape", "portrait"}:
            self.orientation.set(orientation)
        if quality in {"1080", "720"}:
            self.quality.set(quality)
        self.recursive.set(bool(data.get("recursive", False)))
        self.input_var.set(str(data.get("input_dir", "")))
        self.output_var.set(str(data.get("output_dir", "")))

    def _save_settings(self) -> None:
        data = {
            "input_dir": self.input_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "k": self.k_var.get().strip(),
            "count": self.count_var.get().strip(),
            "orientation": self.orientation.get(),
            "quality": self.quality.get(),
            "recursive": bool(self.recursive.get()),
        }
        try:
            SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self) -> None:
        self.update_cancel.set()
        if self.running:
            stop = messagebox.askokcancel(
                "Đang ghép",
                "Đang ghép video. Dừng và thoát?",
                parent=self.root,
            )
            if not stop:
                return
            self.cancel.set()
            if self.worker is not None:
                self.worker.join(timeout=8)
        self._save_settings()
        self.root.destroy()


def main() -> None:
    enable_dpi()
    App().run()


if __name__ == "__main__":
    main()
