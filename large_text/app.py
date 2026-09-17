"""Windows-friendly Tk UI. Disk work runs on a cancellable worker thread."""
from __future__ import annotations

import math
import os
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
import unicodedata

from . import i18n, theme as themes
from .core import (Cancelled, ENCODINGS, MIB, Snapshot, export_utf8, find_next,
                   mib_bytes, new_output_directory, read_page, read_window, split_file)

# Tk on Windows hands a run of text to the OS about 200 bytes at a time. When a
# piece starts on a Thai vowel or tone mark, that mark is drawn alone over a
# dotted circle and shoves the rest of the line sideways. A tag boundary makes
# Tk draw each side separately, so long lines are tagged into runs that always
# start on a base character. Tagging one huge line grows quadratically, so only
# the first MAX_RUNS_PER_LINE runs of a line are protected.
RUN_BYTES = 180
MAX_RUNS_PER_LINE = 1000
LONG_LINE = re.compile("[^\n]{%d,}" % (RUN_BYTES // 6 + 1))


def human_size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,.2f} {unit}"
        value /= 1024


def displayed(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "␀")


def _joins_previous(char):
    code = ord(char)
    return (unicodedata.category(char) in ("Mn", "Mc", "Me") or code in (0x0E33, 0x200D)
            or 0x1F3FB <= code <= 0x1F3FF)


def run_spans(text):
    """Text-widget index pairs for every other draw run, ready for tag_add."""
    spans, line, last = [], 1, 0
    for match in LONG_LINE.finditer(text):
        line += text.count("\n", last, match.start())
        last = match.start()
        piece = match.group()
        # Tcl 8.6 stores a supplementary character as a 6-byte surrogate pair,
        # and "line.column" indices count it as two columns.
        units = len(piece.encode("utf-16-le")) // 2
        if len(piece.encode("utf-8")) + 2 * (units - len(piece)) <= RUN_BYTES:
            continue
        bounds, used, column = [0], 0, 0
        for char in piece:
            code = ord(char)
            size = 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 6
            if used + size > RUN_BYTES and not _joins_previous(char):
                bounds.append(column)
                if len(bounds) > MAX_RUNS_PER_LINE:
                    break
                used = 0
            used += size
            column += 2 if code > 0xFFFF else 1
        bounds.append(units)
        for start, end in zip(bounds[1::2], bounds[2::2]):
            spans += [f"{line}.{start}", f"{line}.{end}"]
    return spans


class BigTextApp:
    def __init__(self, root, initial_file=None, language=None, theme=None):
        self.root = root
        self.language = language if language in i18n.CODES else i18n.load_language()
        self.theme = theme if theme in themes.THEMES else i18n.load_setting("theme", themes.THEMES, themes.DEFAULT)
        width = min(1160, root.winfo_screenwidth() - 60)
        height = min(820, root.winfo_screenheight() - 100)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(900, 600)
        self.snapshot = None
        self.view = None
        self.page_index = 0
        self.page_bytes = MIB
        self.last_match = None
        self.last_query = None
        self.split_output = None
        self.busy = False
        self.closing = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.controls = []
        self.translated = []
        self.switches = []
        self.comboboxes = []
        self.last_status = ("ready", {})
        self.encoding = tk.StringVar(value="auto")
        self.page_mib = tk.StringVar(value="1")
        self.page_number = tk.StringVar(value="1")
        self.percent = tk.StringVar(value="0")
        self.query = tk.StringVar()
        self.part_mib = tk.StringVar(value="1")
        self.output_parent = tk.StringVar(value=str(Path(__file__).resolve().parents[1] / "output"))
        self.prefer_lines = tk.BooleanVar(value=True)
        self.wrap = tk.BooleanVar(value=False)
        self.language_choice = tk.StringVar(value=self.language)
        self.theme_choice = tk.StringVar(value=self.theme)
        self.status = tk.StringVar()
        self.file_label = tk.StringVar()
        self.info = tk.StringVar()
        self.page_label = tk.StringVar()
        self.part_estimate = tk.StringVar()
        self._build()
        self._apply_theme()
        self._apply_language()
        self.part_mib.trace_add("write", lambda *_: self._estimate())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-o>", lambda _: self.open_file() if not self.busy else None)
        self.root.bind("<Control-f>", lambda _: self.search_entry.focus_set())
        self.root.bind("<Control-Right>", lambda _: self.navigate(1) if not self.busy else None)
        self.root.bind("<Control-Left>", lambda _: self.navigate(-1) if not self.busy else None)
        self.root.after(60, self._poll)
        if initial_file:
            self.root.after(100, lambda: self.open_file(initial_file))

    def t(self, key, **values):
        values = {name: i18n.message(self.language, value) if isinstance(value, (BaseException, i18n.Message)) else value
                  for name, value in values.items()}
        return i18n.text(self.language, key, **values)

    def say(self, key, **values):
        self.last_status = (key, values)
        self.status.set(self.t(key, **values))

    def _tr(self, widget, key):
        self.translated.append((widget, key))
        return widget

    def _control(self, widget, normal="normal"):
        self.controls.append((widget, normal))
        return widget

    def _button(self, parent, key, command, **kwargs):
        return self._tr(self._control(ttk.Button(parent, command=command, **kwargs)), key)

    def _label(self, parent, key, **kwargs):
        return self._tr(ttk.Label(parent, **kwargs), key)

    def _build(self):
        self.ui_font = tkfont.Font(self.root, family=i18n.CONTENT_FAMILY, size=11)
        self.title_font = tkfont.Font(self.root, family=i18n.CONTENT_FAMILY, size=18, weight="bold")
        self.content_font = tkfont.Font(self.root, family=i18n.CONTENT_FAMILY, size=11)
        self.content_bold = tkfont.Font(self.root, family=i18n.CONTENT_FAMILY, size=11, weight="bold")
        # Entry and Combobox use TkTextFont, not the "." style; they can hold
        # Thai, so they keep the content font in every language.
        tkfont.nametofont("TkTextFont", root=self.root).configure(family=i18n.CONTENT_FAMILY, size=11)
        self.root.option_add("*TCombobox*Listbox.font", self.content_font)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=self.ui_font)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # Colours for these plain tk widgets and the ttk styles come from _apply_theme.
        header = tk.Frame(self.root, padx=22, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        self.title_label = tk.Label(header, text="BigText", font=("Segoe UI", 23, "bold"))
        self.title_label.pack(side="left")
        languages = tk.Frame(header)
        languages.pack(side="right")
        for code, name in i18n.LANGUAGES:
            self._switch(languages, name, code, self.language_choice,
                         lambda: self.set_language(self.language_choice.get()))
        looks = tk.Frame(header)
        looks.pack(side="right", padx=(0, 18))
        for name in themes.THEMES:
            self._tr(self._switch(looks, "", name, self.theme_choice,
                                  lambda: self.set_theme(self.theme_choice.get())), f"theme_{name}")
        self.header_frames = (header, languages, looks)
        # anchor="w" so a narrow window cuts the end of the tagline, not both sides.
        self.tagline = self._tr(tk.Label(header, font=self.ui_font, anchor="w"), "tagline")
        self.tagline.pack(side="left", padx=24)

        filebar = ttk.Frame(self.root, padding=(16, 10))
        filebar.grid(row=1, column=0, sticky="ew")
        filebar.columnconfigure(1, weight=1)
        self._button(filebar, "open", self.open_file, style="Accent.TButton").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ttk.Label(filebar, textvariable=self.file_label, font=self.content_bold,
                  anchor="w").grid(row=0, column=1, sticky="ew")
        ttk.Label(filebar, textvariable=self.info, font=self.content_font).grid(row=1, column=1, sticky="ew", pady=(3, 0))
        self._label(filebar, "encoding").grid(row=0, column=2, padx=8)
        enc = self._control(ttk.Combobox(filebar, textvariable=self.encoding, values=ENCODINGS,
                                       state="readonly", width=12), "readonly")
        self.comboboxes.append(enc)
        enc.grid(row=1, column=2, padx=8)
        enc.bind("<<ComboboxSelected>>", lambda _: self.reload())
        self._button(filebar, "reload", self.reload).grid(row=0, column=3, rowspan=2)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=16)
        reader = ttk.Frame(self.tabs, padding=10)
        splitter = ttk.Frame(self.tabs, padding=22)
        self.tabs.add(reader)
        self.tabs.add(splitter)
        reader.columnconfigure(0, weight=1)
        reader.rowconfigure(2, weight=1)

        nav = ttk.Frame(reader)
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for key, command in (("first", lambda: self.load_page(0)), ("prev", lambda: self.navigate(-1)),
                             ("next", lambda: self.navigate(1)), ("last", self.last_page)):
            self._button(nav, key, command).pack(side="left", padx=(0, 4))
        ttk.Label(nav, textvariable=self.page_label).pack(side="left", padx=10)
        page = self._control(ttk.Entry(nav, width=9, textvariable=self.page_number))
        page.pack(side="left")
        page.bind("<Return>", lambda _: self.jump_page())
        self._button(nav, "go_page", self.jump_page).pack(side="left", padx=4)
        self._label(nav, "mib_per_page").pack(side="left", padx=(10, 4))
        sizes = self._control(ttk.Combobox(nav, width=5, state="readonly", textvariable=self.page_mib,
                                         values=("0.0625", "0.25", "1", "4")), "readonly")
        self.comboboxes.append(sizes)
        sizes.pack(side="left")
        sizes.bind("<<ComboboxSelected>>", lambda _: self.change_page_size())

        searchbar = ttk.Frame(reader)
        searchbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._label(searchbar, "search_label").pack(side="left", padx=(0, 8))
        self.search_entry = self._control(ttk.Entry(searchbar, textvariable=self.query))
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda _: self.search())
        self._button(searchbar, "find_next", self.search).pack(side="left", padx=5)
        percent = self._control(ttk.Entry(searchbar, textvariable=self.percent, width=6))
        percent.pack(side="left", padx=(12, 3))
        percent.bind("<Return>", lambda _: self.jump_percent())
        self._button(searchbar, "go_percent", self.jump_percent).pack(side="left")

        editor = ttk.Frame(reader)
        editor.grid(row=2, column=0, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        self.text = tk.Text(editor, wrap="none", state="disabled", font=(i18n.CONTENT_FAMILY, 12),
                            relief="flat", borderwidth=0, highlightthickness=0, padx=12, pady=10, undo=False)
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(editor, command=self.text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(editor, orient="horizontal", command=self.text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.text.tag_configure("found")
        self.text.bind("<Control-a>", self.select_all)
        tools = ttk.Frame(reader)
        tools.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._tr(self._control(ttk.Checkbutton(tools, variable=self.wrap,
                                              command=lambda: self.text.configure(wrap="word" if self.wrap.get() else "none"))),
                 "wrap").pack(side="left")
        self._button(tools, "export", self.export).pack(side="right")

        splitter.columnconfigure(1, weight=1)
        self._label(splitter, "split_title", font=self.title_font).grid(row=0, column=0, columnspan=3, sticky="w")
        self._label(splitter, "split_intro", wraplength=820).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 24))
        self._label(splitter, "part_size").grid(row=2, column=0, sticky="w", padx=(0, 16))
        self._control(ttk.Entry(splitter, width=12, textvariable=self.part_mib)).grid(row=2, column=1, sticky="w")
        self._label(splitter, "mib_note").grid(row=3, column=1, sticky="w", pady=(5, 18))
        self._label(splitter, "output_folder").grid(row=4, column=0, sticky="w")
        self._control(ttk.Entry(splitter, textvariable=self.output_parent)).grid(row=4, column=1, sticky="ew")
        self._button(splitter, "choose", self.choose_output).grid(row=4, column=2, padx=(8, 0))
        self._tr(self._control(ttk.Checkbutton(splitter, variable=self.prefer_lines)),
                 "prefer_lines").grid(row=5, column=0, columnspan=3, sticky="w", pady=(18, 6))
        self._label(splitter, "long_lines_note", wraplength=820).grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Label(splitter, textvariable=self.part_estimate, font=self.content_bold,
                  wraplength=820).grid(row=7, column=0, columnspan=3, sticky="w", pady=20)
        actions = ttk.Frame(splitter)
        actions.grid(row=8, column=0, columnspan=3, sticky="w")
        self._button(actions, "start_split", self.split, style="Accent.TButton").pack(side="left", padx=(0, 10))
        self._button(actions, "open_output", self.open_output).pack(side="left")
        self._label(splitter, "split_footer", wraplength=820).grid(row=9, column=0, columnspan=3, sticky="w", pady=20)

        footer = ttk.Frame(self.root, padding=(16, 8, 16, 10))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, font=self.content_font, wraplength=940).grid(row=0, column=0, sticky="w")
        # _run swaps in a fresh Event per job, so look it up when the button is pressed.
        self.stop = self._tr(ttk.Button(footer, command=lambda: self.cancel.set(), state="disabled"), "stop")
        self.stop.grid(row=0, column=1, padx=(10, 0))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _switch(self, parent, text, value, variable, command):
        button = tk.Radiobutton(parent, text=text, value=value, variable=variable, command=command,
                                indicatoron=False, font=(i18n.CONTENT_FAMILY, 11, "bold"), width=8, pady=5,
                                relief="flat", borderwidth=0, cursor="hand2")
        button.pack(side="left", padx=(6, 0))
        self.switches.append(button)
        return button

    def set_theme(self, name, remember=True):
        if name not in themes.THEMES:
            return
        self.theme = name
        self.theme_choice.set(name)
        if remember:
            i18n.save_setting("theme", name)
        self._apply_theme()

    def _apply_theme(self):
        p = themes.PALETTES[self.theme]
        themes.configure_ttk(ttk.Style(self.root), p)
        self.root.configure(background=p["window"])
        for frame in self.header_frames:
            frame.configure(background=p["header"])
        self.title_label.configure(foreground=p["header_text"], background=p["header"])
        self.tagline.configure(foreground=p["tagline"], background=p["header"])
        for button in self.switches:
            button.configure(foreground=p["header_text"], background=p["switch"], activeforeground=p["header_text"],
                             activebackground=p["switch_active"], selectcolor=p["switch_on"])
        self.text.configure(background=p["page"], foreground=p["page_text"], insertbackground=p["cursor"],
                            selectbackground=p["select"], selectforeground=p["select_text"],
                            inactiveselectbackground=p["select"])
        self.text.tag_configure("found", background=p["found"], foreground=p["found_text"])
        for combobox in self.comboboxes:
            # The drop-down list is a plain Tk listbox that ttk styles do not reach.
            popdown = combobox.tk.call("ttk::combobox::PopdownWindow", combobox)
            combobox.tk.call(f"{popdown}.f.l", "configure", "-background", p["field"], "-foreground", p["text"],
                             "-selectbackground", p["select"], "-selectforeground", p["select_text"])
        themes.title_bar(self.root, self.theme == "dark")

    def set_language(self, code, remember=True):
        if code not in i18n.CODES:
            return
        self.language = code
        self.language_choice.set(code)
        if remember:
            i18n.save_language(code)
        self._apply_language()

    def _apply_language(self):
        family = i18n.ui_family(self.language, tkfont.families(self.root))
        self.ui_font.configure(family=family)
        self.title_font.configure(family=family)
        self.root.title(self.t("title"))
        for widget, key in self.translated:
            widget.configure(text=self.t(key))
        self.tabs.tab(0, text=f"  {self.t('tab_read')}  ")
        self.tabs.tab(1, text=f"  {self.t('tab_split')}  ")
        self._labels()
        key, values = self.last_status
        self.status.set(self.t(key, **values))

    def _labels(self):
        if self.snapshot:
            self.file_label.set(self.snapshot.path.name)
            self.info.set(self.t("file_info", size=human_size(self.snapshot.size),
                                 bytes=f"{self.snapshot.size:,}", encoding=self.snapshot.encoding))
            count = self.snapshot.page_count(self.page_bytes)
            self.page_label.set(self.t("page_of", page=f"{self.page_index + 1:,}", count=f"{count:,}"))
        else:
            self.file_label.set(self.t("no_file"))
            self.info.set(self.t("info_idle"))
            self.page_label.set(self.t("page_none"))
        self._estimate()

    def _set_busy(self, value):
        self.busy = value
        for widget, normal in self.controls:
            widget.configure(state="disabled" if value else normal)
        self.stop.configure(state="normal" if value else "disabled")

    def _run(self, tag, function):
        if self.busy:
            return
        self.cancel = threading.Event()
        self.progress["value"] = 0
        self._set_busy(True)
        self.say("working")

        def work():
            try:
                result = function()
                self.events.put(("result", tag, result))
            except Cancelled as error:
                self.events.put(("cancelled", tag, error))
            except Exception as error:
                self.events.put(("error", tag, error))
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, done, total, detail):
        self.events.put(("progress", "", (done, total, detail)))

    def _poll(self):
        try:
            while True:
                kind, tag, value = self.events.get_nowait()
                if kind == "progress":
                    done, total, detail = value
                    self.progress["value"] = 100 * done / total if total else 100
                    self.say("progress", detail=i18n.Message(detail), done=human_size(done), total=human_size(total))
                    continue
                self._set_busy(False)
                if self.closing:
                    self.root.destroy()
                    return
                if kind == "error":
                    self.say("error_status", error=value)
                    messagebox.showerror("BigText", i18n.message(self.language, value), parent=self.root)
                elif kind == "cancelled":
                    self.say("cancelled")
                else:
                    self.progress["value"] = 100
                    self._result(tag, value)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _result(self, tag, value):
        if tag == "open":
            self.snapshot, self.view = value
            self.page_index = 0
            self.last_match = self.last_query = None
            self._show()
        elif tag == "page":
            self.page_index, self.view = value
            self.last_match = self.last_query = None
            self._show()
        elif tag == "search":
            found, view, query = value
            if found is None:
                self.say("not_found")
                return
            self.last_match, self.last_query, self.view = found, query, view
            self.page_index = found // self.page_bytes
            self._show()
            payload = view.raw[:found - view.start]
            if view.start == 0 and self.snapshot.bom:
                payload = payload[len(self.snapshot.bom):]
            prefix = displayed(payload.decode(self.snapshot.encoding, errors="replace"))
            # Text's +Nc counts characters, while Tcl string length counts a
            # supplementary character twice on Tk 8.6 (for example an emoji).
            begin = f"1.0+{len(prefix)}c"
            length = len(displayed(query))
            self.text.tag_add("found", begin, f"{begin}+{length}c")
            self.text.see(begin)
            self.say("found_at", offset=f"{found:,}")
        elif tag == "split":
            self.say("split_done_status", parts=f"{value['parts']:,}", folder=self.split_output)
            messagebox.showinfo(self.t("split_done_title"),
                                self.t("split_done_body", parts=f"{value['parts']:,}", folder=self.split_output,
                                       hard=f"{value['hard_splits']:,}"), parent=self.root)
        elif tag == "export":
            self.say("exported", path=value)

    def _show(self):
        shown = displayed(self.view.text)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", shown)
        spans = run_spans(shown)
        if spans:
            self.text.tag_add("run", *spans)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        self.text.xview_moveto(0)
        self._labels()
        self.page_number.set(str(self.page_index + 1))
        self.percent.set(f"{100 * self.view.start / self.snapshot.size:.2f}" if self.snapshot.size else "0")
        self.say("page_status", start=f"{self.view.start:,}", end=f"{self.view.end:,}",
                 read=human_size(len(self.view.raw)), note=self.t("decode_note") if self.view.decode_warning else "")

    def _error(self, error):
        messagebox.showerror("BigText", i18n.message(self.language, error), parent=self.root)

    def open_file(self, path=None):
        if self.busy:
            return
        path = path or filedialog.askopenfilename(parent=self.root, filetypes=[
            (self.t("filetype_logs"), "*.txt *.log *.csv *.json *.jsonl"), (self.t("filetype_all"), "*.*")])
        if not path:
            return
        encoding, page_bytes = self.encoding.get(), self.page_bytes
        def work():
            snapshot = Snapshot.inspect(path, encoding)
            return snapshot, read_page(snapshot, 0, page_bytes)
        self._run("open", work)

    def reload(self):
        if self.snapshot:
            self.open_file(str(self.snapshot.path))

    def load_page(self, number):
        if not self.snapshot or self.busy:
            return
        number = max(0, min(number, self.snapshot.page_count(self.page_bytes) - 1))
        self._run("page", lambda: (number, read_page(self.snapshot, number, self.page_bytes)))

    def navigate(self, step):
        self.load_page(self.page_index + step)

    def last_page(self):
        if self.snapshot:
            self.load_page(self.snapshot.page_count(self.page_bytes) - 1)

    def jump_page(self):
        try:
            number = int(self.page_number.get().replace(",", "")) - 1
            if self.snapshot and not 0 <= number < self.snapshot.page_count(self.page_bytes):
                raise ValueError(self.t("page_out_of_range"))
            self.load_page(number)
        except ValueError as error:
            self._error(error)

    def jump_percent(self):
        try:
            percent = float(self.percent.get())
            if not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError(self.t("percent_range"))
            if self.snapshot:
                self.load_page(int(max(0, self.snapshot.size - 1) * percent / 100) // self.page_bytes)
        except ValueError as error:
            self._error(error)

    def change_page_size(self):
        offset = self.view.start if self.view else 0
        self.page_bytes = mib_bytes(self.page_mib.get())
        self.load_page(offset // self.page_bytes)

    def search(self):
        if not self.snapshot or self.busy:
            return
        query = self.query.get()
        try:
            if not query or len(query.encode(self.snapshot.encoding)) > 4096:
                raise ValueError(self.t("search_limit"))
        except UnicodeEncodeError:
            self._error(self.t("query_encoding"))
            return
        except ValueError as error:
            self._error(error)
            return
        start = self.last_match + self.snapshot.unit if self.last_query == query and self.last_match is not None else self.view.start
        def work():
            found = find_next(self.snapshot, query, start, cancel=self.cancel, progress=self._progress)
            view = read_window(self.snapshot, max(0, found - 128), self.page_bytes) if found is not None else None
            return found, view, query
        self._run("search", work)

    def export(self):
        if not self.view or self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".txt",
                                           initialfile=f"page_{self.page_index + 1:08d}.txt",
                                           filetypes=[(self.t("filetype_text"), "*.txt")])
        if path:
            self._run("export", lambda: export_utf8(self.view, path, self.snapshot.path))

    def choose_output(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.output_parent.set(path)

    def _estimate(self):
        if not self.snapshot:
            self.part_estimate.set(self.t("open_first"))
            return
        try:
            size = mib_bytes(self.part_mib.get())
            count = (self.snapshot.size + size - 1) // size
            self.part_estimate.set(self.t("estimate", count=f"{count:,}", size=human_size(self.snapshot.size)))
        except ValueError:
            self.part_estimate.set(self.t("bad_size"))

    def split(self):
        if not self.snapshot or self.busy:
            return
        try:
            size = mib_bytes(self.part_mib.get())
            parent = self.output_parent.get().strip()
            if not parent:
                raise ValueError(self.t("choose_output_first"))
            destination = new_output_directory(parent, self.snapshot.path.stem)
        except ValueError as error:
            self._error(error)
            return
        self.split_output = destination
        prefer_lines = self.prefer_lines.get()
        self._run("split", lambda: split_file(self.snapshot, destination, size, prefer_lines=prefer_lines,
                                              cancel=self.cancel, progress=self._progress))

    def open_output(self):
        if self.split_output and self.split_output.is_dir():
            os.startfile(self.split_output)

    def select_all(self, _event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel.set()
            self.say("closing")
        else:
            self.root.destroy()


def launch(initial_file=None):
    root = tk.Tk()
    root.withdraw()
    app = BigTextApp(root, initial_file)
    # Windows creates the frame on this idle pass; colouring the title bar before
    # the window first appears is the only way it shows dark without a repaint.
    root.update_idletasks()
    themes.title_bar(root, app.theme == "dark")
    root.deiconify()
    root.mainloop()
