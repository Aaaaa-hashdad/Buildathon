"""Simple window for the LinkedIn data bot. Double-click run_app.bat to open it."""

import ctypes
import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import linkedin_bot as bot

HERE = Path(__file__).parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BLUE = "#0a66c2"

# Sharp text on the 150% scaled display instead of a blurry stretched window.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

KINDS = {"People": "people", "Companies": "companies", "Jobs": "jobs"}

# Columns shown in the live table (Excel gets them all).
TABLE_COLS = {
    "people": ["Name", "Headline", "Location", "Connection"],
    "companies": ["Name", "Industry / Location", "Followers"],
    "jobs": ["Title", "Company", "Location", "Posted", "Apply type"],
}

SPEED_INFO = {
    "Low": ("low", "~3s per action, safest", "green"),
    "Medium": ("medium", "~1s per action, careful", "#b26a00"),
    "High": ("high", "quick, higher ban risk", "red"),
}


class ActiveBadge:
    """Small always-on-top badge in the bottom-right corner while the bot runs."""

    def __init__(self, root, px, on_stop):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1d2226")
        self.px = px
        p = lambda v: int(v * px)

        top = tk.Frame(self.win, bg="#1d2226")
        top.pack(fill="x", padx=p(12), pady=(p(10), p(2)))
        self.dot = tk.Label(top, text="\u25CF", fg="#ff4d4d", bg="#1d2226", font=("Segoe UI", 12))
        self.dot.pack(side="left")
        tk.Label(top, text=" BOT ACTIVE", fg="white", bg="#1d2226",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Button(top, text="Stop", command=on_stop, bg="#ff4d4d", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=p(8), cursor="hand2").pack(side="right")

        self.status = tk.Label(self.win, text="Starting...", fg="#cfd8dc", bg="#1d2226",
                               font=("Segoe UI", 9), anchor="w", justify="left", wraplength=p(300))
        self.status.pack(fill="x", padx=p(12))
        self.count = tk.Label(self.win, text="0 saved", fg="#7fc1ff", bg="#1d2226",
                              font=("Segoe UI", 9, "bold"), anchor="w")
        self.count.pack(fill="x", padx=p(12), pady=(0, p(10)))

        # Drag it anywhere you like.
        for w in (self.win, top, self.status, self.count):
            w.bind("<ButtonPress-1>", self._grab)
            w.bind("<B1-Motion>", self._drag)

        self.win.update_idletasks()
        w, h = p(330), self.win.winfo_reqheight()
        x = self.win.winfo_screenwidth() - w - p(16)
        y = self.win.winfo_screenheight() - h - p(64)   # sits above the taskbar
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self._on = True
        self._pulse()

    def _grab(self, e):
        self._dx, self._dy = e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y()

    def _drag(self, e):
        self.win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _pulse(self):
        if not self.win.winfo_exists():
            return
        self._on = not self._on
        self.dot.config(fg="#ff4d4d" if self._on else "#5a2020")
        self.win.after(600, self._pulse)

    def update(self, status=None, saved=None):
        if status:
            self.status.config(text=status)
        if saved is not None:
            self.count.config(text=f"{saved} saved")

    def close(self):
        self.win.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.last_file = None
        self.badge = None
        self.saved = 0

        root.title("LinkedIn Data Bot")
        self.px = root.winfo_fpixels("1i") / 96
        p = lambda v: int(v * self.px)
        root.geometry(f"{p(680)}x{p(760)}")
        root.minsize(p(520), p(560))

        style = ttk.Style()
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=p(6))
        style.configure("Treeview", rowheight=p(22))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        f = ttk.Frame(root, padding=p(14))
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        bold = ("Segoe UI", 10, "bold")

        # Step 1
        ttk.Label(f, text="1.  Open Chrome and log into LinkedIn", font=bold).grid(
            row=0, column=0, columnspan=2, sticky="w")
        row1 = ttk.Frame(f)
        row1.grid(row=1, column=0, columnspan=2, sticky="we", pady=(p(4), p(12)))
        ttk.Button(row1, text="Open Chrome", command=self.open_chrome).pack(side="left")
        self.chrome_status = ttk.Label(row1, text="")
        self.chrome_status.pack(side="left", padx=p(10))

        # Step 2
        ttk.Label(f, text="2.  What do you want to find?", font=bold).grid(
            row=2, column=0, columnspan=2, sticky="w")

        self.kind = tk.StringVar(value="People")
        ttk.Label(f, text="Looking for").grid(row=3, column=0, sticky="w", pady=p(3))
        ttk.Combobox(f, textvariable=self.kind, values=list(KINDS), state="readonly").grid(
            row=3, column=1, sticky="we", pady=p(3))

        self.query = tk.StringVar()
        ttk.Label(f, text="Search for").grid(row=4, column=0, sticky="w", pady=p(3))
        q = ttk.Entry(f, textvariable=self.query)
        q.grid(row=4, column=1, sticky="we", pady=p(3))
        q.focus()

        self.location = tk.StringVar()
        ttk.Label(f, text="Location (optional)").grid(row=5, column=0, sticky="w", padx=(0, p(10)), pady=p(3))
        ttk.Entry(f, textvariable=self.location).grid(row=5, column=1, sticky="we", pady=p(3))

        self.pages = tk.IntVar(value=3)
        ttk.Label(f, text="Pages (1-10)").grid(row=6, column=0, sticky="w", pady=p(3))
        ttk.Spinbox(f, from_=1, to=bot.MAX_PAGES, textvariable=self.pages, width=5).grid(
            row=6, column=1, sticky="w", pady=p(3))

        self.speed = tk.StringVar(value="Medium")
        ttk.Label(f, text="Speed").grid(row=7, column=0, sticky="w", pady=p(3))
        speed_row = ttk.Frame(f)
        speed_row.grid(row=7, column=1, sticky="we", pady=p(3))
        speed_box = ttk.Combobox(speed_row, textvariable=self.speed, values=list(SPEED_INFO),
                                 state="readonly", width=10)
        speed_box.pack(side="left")
        self.speed_info = ttk.Label(speed_row, text="")
        self.speed_info.pack(side="left", padx=p(10))
        speed_box.bind("<<ComboboxSelected>>", lambda e: self.show_speed_info())
        self.show_speed_info()

        self.grab_links = tk.BooleanVar(value=False)
        self.grab_box = ttk.Checkbutton(
            f, variable=self.grab_links,
            text="Jobs: also get hidden company-site apply links (clicks Apply, counts as an apply click)")
        self.grab_box.grid(row=8, column=1, sticky="w", pady=p(3))
        self.kind.trace_add("write", lambda *_: self.grab_box.state(
            ["!disabled"] if self.kind.get() == "Jobs" else ["disabled"]))
        self.grab_box.state(["disabled"])

        # Step 3
        ttk.Label(f, text="3.  Go", font=bold).grid(row=9, column=0, columnspan=2, sticky="w", pady=(p(12), 0))
        btns = ttk.Frame(f)
        btns.grid(row=10, column=0, columnspan=2, sticky="we", pady=p(4))
        self.start_btn = ttk.Button(btns, text="Start", style="Big.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop.set, state="disabled")
        self.stop_btn.pack(side="left", padx=p(8))
        self.open_btn = ttk.Button(btns, text="Open Excel", command=self.open_file, state="disabled")
        self.open_btn.pack(side="right")
        ttk.Button(btns, text="Results folder", command=self.open_folder).pack(side="right", padx=p(8))

        # Live view
        self.now = ttk.Label(f, text="Idle", foreground="gray", font=("Segoe UI", 10, "bold"))
        self.now.grid(row=11, column=0, columnspan=2, sticky="w", pady=(p(10), p(4)))

        table = ttk.Frame(f)
        table.grid(row=12, column=0, columnspan=2, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, show="headings", height=10)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self.open_row)
        self.set_columns("people")
        f.rowconfigure(12, weight=3)

        self.log_box = tk.Text(f, height=5, wrap="word", font=("Consolas", 9), state="disabled")
        self.log_box.grid(row=13, column=0, columnspan=2, sticky="nsew", pady=(p(8), 0))
        f.rowconfigure(13, weight=1)

        root.bind("<Return>", lambda e: self.start())
        self.check_chrome()
        self.pump()

    def show_speed_info(self):
        _, text, color = SPEED_INFO[self.speed.get()]
        self.speed_info.config(text=text, foreground=color)

    def set_columns(self, kind):
        cols = TABLE_COLS[kind]             # link column hidden; double-click opens it
        all_cols = bot.COLUMNS[kind]
        self.col_idx = [all_cols.index(c) for c in cols]
        self.link_idx = all_cols.index(bot.LINK_COLUMN[kind])
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = cols
        for i, c in enumerate(cols):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=int((180 if i < 2 else 110) * self.px), stretch=True)

    def log(self, msg):
        self.events.put(("log", msg))     # safe from the worker thread

    def pump(self):
        # Tk isn't thread-safe, so the worker only queues events and this loop applies them.
        while not self.events.empty():
            kind, val = self.events.get()
            if kind == "log":
                self.log_box.configure(state="normal")
                self.log_box.insert("end", val + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
            elif kind == "status":
                self.now.config(text="\U0001F916  " + val, foreground=BLUE)
                if self.badge:
                    self.badge.update(status=val)
            elif kind == "row":
                self.saved += 1
                iid = self.tree.insert("", "end", values=[val[i] for i in self.col_idx],
                                       tags=(val[self.link_idx],))
                self.tree.see(iid)
                if self.badge:
                    self.badge.update(saved=self.saved)
            elif kind == "done":
                self.finished(val)
        self.root.after(120, self.pump)

    def check_chrome(self):
        if bot.chrome_running():
            self.chrome_status.config(text="Chrome connected", foreground="green")
        else:
            self.chrome_status.config(text="Chrome not open", foreground="red")
        self.root.after(3000, self.check_chrome)

    def open_chrome(self):
        if bot.chrome_running():
            self.log("Chrome is already open. Make sure you're logged into LinkedIn there.")
            return
        subprocess.Popen([CHROME, "--remote-debugging-port=9222",
                          f"--user-data-dir={HERE / 'chrome-profile'}", "https://www.linkedin.com/"])
        self.log("Opened Chrome. Log into LinkedIn there (only needed the first time).")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        query = self.query.get().strip()
        if not query:
            self.log("Type something in 'Search for' first.")
            return
        try:
            pages = max(1, min(int(self.pages.get()), bot.MAX_PAGES))
        except (tk.TclError, ValueError):
            pages = 3
        kind = KINDS[self.kind.get()]
        location = self.location.get().strip()
        speed = SPEED_INFO[self.speed.get()][0]
        grab_links = kind == "jobs" and self.grab_links.get()

        self.stop.clear()
        self.saved = 0
        self.set_columns(kind)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.badge = ActiveBadge(self.root, self.px, self.stop.set)
        self.log(f"--- {self.kind.get()}: '{query}' {location} ({pages} pages, {speed} speed) ---")

        def work():
            try:
                path = bot.run(kind, query, location, pages, log=self.log, stop=self.stop,
                               report=lambda k, v: self.events.put((k, v)), speed=speed,
                               grab_apply_links=grab_links)
            except Exception as e:
                self.log(f"Error: {e}")
                path = None
            self.events.put(("done", path))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def finished(self, path):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        if self.badge:
            self.badge.close()
            self.badge = None
        self.now.config(text=f"Done \u2013 {self.saved} saved" if path else "Idle", foreground="gray")
        if path:
            self.last_file = path
            self.open_btn.config(state="normal")
            self.log("Done! Click 'Open Excel'.")

    def open_row(self, _):
        sel = self.tree.selection()
        if sel:
            os.startfile(self.tree.item(sel[0], "tags")[0])

    def open_file(self):
        if self.last_file:
            os.startfile(self.last_file)

    def open_folder(self):
        bot.OUTPUT_DIR.mkdir(exist_ok=True)
        os.startfile(bot.OUTPUT_DIR)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
