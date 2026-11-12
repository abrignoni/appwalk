#!/usr/bin/env python3
"""AppWalk window: the same runs as appwalk.py, in four steps and with no terminal.

1. Phone: is a phone connected and ready, what state is it in, who is doing the work.
2. What to document: the app in front, the open screen or rows from the open list.
3. Run: start, watch each screenshot arrive, pause or stop, answer when the tool has to wait.
4. Result: what was captured, any warnings in plain words, the transcript, the row hash.

Nothing is unlocked or bypassed. When a lock or another app is in front the tool waits; the examiner
deals with it on the phone, or types the code here for the tool to enter. The code is not recorded.

Run with a Python whose Tk is 8.6. On macOS the python.org 3.14 build ships Tk 9, which has crashed
this kind of window; use python3.12 there.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appwalk  # noqa: E402

PALETTES = {
    "light": dict(BG="#F4F5F7", CARD="#FFFFFF", INK="#1B1F24", MUTED="#5F6B7A", LINE="#D9DEE5", FIELD="#FFFFFF", SOFT="#EEF1F5",
                  EDGE="#B8C0CC", ACCENT="#2457D6", ACCENT_DOWN="#1B45AD", ACCENT_OFF="#AFC0EC", INK_OFF="#9AA4B1",
                  OK="#17803D", WARN="#A85B00", BAD="#B42318"),
    "dark": dict(BG="#14171C", CARD="#1D2128", INK="#E8EBF0", MUTED="#9AA5B4", LINE="#323843", FIELD="#262B34", SOFT="#2A303A",
                 EDGE="#4A5362", ACCENT="#5B8CFF", ACCENT_DOWN="#4474E6", ACCENT_OFF="#33415F", INK_OFF="#6B7585",
                 OK="#4ADE80", WARN="#F5B041", BAD="#FF7B72"),
}
globals().update(PALETTES["light"])


def system_theme():
    """dark when the operating system is set to dark, where that can be read; otherwise light."""
    try:
        if sys.platform == "darwin":
            p = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
            return "dark" if b"Dark" in p.stdout else "light"
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except (OSError, subprocess.SubprocessError):
        pass
    return "light"
STEPS = ["Phone", "What to document", "Run", "Result"]
SETTINGS = os.path.join(os.path.expanduser("~"), ".appwalk", "settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(values):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=1)
    except OSError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("AppWalk")
        root.minsize(900, 680)
        root.configure(bg=BG)
        self.events = queue.Queue()
        self.stop_flag, self.paused = threading.Event(), threading.Event()
        self.thumb, self.export, self.result, self.facts = None, None, None, None
        self.started_at, self.running = None, False
        remembered = load_settings()
        # ---- variables ----
        self.device = tk.StringVar()
        self.examiner = tk.StringVar(value=remembered.get("examiner", ""))
        self.reference = tk.StringVar()
        self.label = tk.StringVar(value=remembered.get("device_label", ""))
        self.notes = tk.StringVar()
        self.package = tk.StringVar()
        self.mode = tk.StringVar(value="list")
        self.out = tk.StringVar(value=remembered.get("out") or os.path.join(os.path.expanduser("~"), "Downloads"))
        self.keep_list = tk.BooleanVar(value=True)
        self.wait = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="")
        self.counter = tk.StringVar(value="")

        self.theme = remembered.get("theme") if remembered.get("theme") in PALETTES else system_theme()
        self.page = 0
        self.build()
        self.refresh_devices()
        self.poller = root.after(100, self.poll)
        self.ticker = root.after(1000, self.tick)

    def build(self):
        """Draws the whole window in the current theme. Called at start and when the theme is switched;
        what was typed is kept, because the fields' values live outside the widgets."""
        root = self.root
        globals().update(PALETTES[self.theme])
        for w in root.winfo_children():
            w.destroy()
        root.configure(bg=BG)
        self.style()

        # ---- header: the four steps ----
        head = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        head.pack(fill="x")
        tk.Label(head, text="AppWalk", font=self.f_title, bg=CARD, fg=INK).pack(side="left", padx=20, pady=14)
        self.step_labels = []
        for i, name in enumerate(STEPS):
            lab = tk.Label(head, text=f"{i + 1}  {name}", font=self.f_body, bg=CARD, fg=MUTED, padx=14, pady=6)
            lab.pack(side="left")
            self.step_labels.append(lab)
        self.theme_btn = ttk.Button(head, text="Light" if self.theme == "dark" else "Dark", command=self.switch_theme)
        self.theme_btn.pack(side="right", padx=16)

        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=22, pady=16)
        self.foot = tk.Frame(root, bg=BG)
        self.foot.pack(fill="x", padx=22, pady=(0, 16))
        self.back_btn = ttk.Button(self.foot, text="Back", command=lambda: self.show(self.page - 1))
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(self.foot, text="Next", style="Accent.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.hint = tk.Label(self.foot, text="", font=self.f_body, bg=BG, fg=WARN, wraplength=560, justify="right")
        self.hint.pack(side="right", padx=14)

        self.pages = [self.page_phone(), self.page_choose(), self.page_run(), self.page_result()]
        self.show(self.page)
        if self.serial():
            self.read_facts()
        if self.result:
            self.render_result()

    def switch_theme(self):
        if self.running:
            return
        self.theme = "light" if self.theme == "dark" else "dark"
        save_settings(dict(load_settings(), theme=self.theme))
        self.build()

    # ---- look ----

    def style(self):
        base = tkfont.nametofont("TkDefaultFont")
        size = max(base.cget("size"), 12)
        fam = base.cget("family")
        self.f_body = tkfont.Font(family=fam, size=size)
        self.f_small = tkfont.Font(family=fam, size=size - 1)
        self.f_bold = tkfont.Font(family=fam, size=size, weight="bold")
        self.f_title = tkfont.Font(family=fam, size=size + 6, weight="bold")
        self.f_big = tkfont.Font(family=fam, size=size + 3, weight="bold")
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", font=self.f_body, background=BG, foreground=INK)
        s.configure("TButton", padding=(14, 8), background=CARD, bordercolor=EDGE, lightcolor=CARD, darkcolor=CARD,
                    relief="solid", borderwidth=1)
        s.map("TButton", background=[("active", SOFT), ("disabled", BG)], foreground=[("disabled", INK_OFF)])
        s.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, relief="flat")
        s.map("Accent.TButton", background=[("active", ACCENT_DOWN), ("disabled", ACCENT_OFF)], foreground=[("disabled", "#FFFFFF")])
        s.configure("Card.TFrame", background=CARD)
        s.configure("Card.TLabel", background=CARD)
        s.configure("Muted.Card.TLabel", background=CARD, foreground=MUTED, font=self.f_small)
        s.configure("Card.TRadiobutton", background=CARD)
        s.configure("Card.TCheckbutton", background=CARD)
        s.map("Card.TRadiobutton", background=[("active", CARD)])
        s.map("Card.TCheckbutton", background=[("active", CARD)])
        s.configure("Card.TRadiobutton", foreground=INK, indicatorcolor=FIELD)
        s.configure("Card.TCheckbutton", foreground=INK, indicatorcolor=FIELD)
        s.map("Card.TRadiobutton", indicatorcolor=[("selected", ACCENT)], foreground=[("active", INK)])
        s.map("Card.TCheckbutton", indicatorcolor=[("selected", ACCENT)], foreground=[("active", INK)])
        s.configure("TEntry", fieldbackground=FIELD, foreground=INK, insertcolor=INK, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=6)
        s.map("TEntry", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", INK)])
        s.configure("TCombobox", fieldbackground=FIELD, foreground=INK, background=CARD, arrowcolor=INK, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", INK)], selectbackground=[("readonly", FIELD)],
              selectforeground=[("readonly", INK)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", INK)
        s.configure("Vertical.TScrollbar", background=SOFT, troughcolor=CARD, bordercolor=CARD, arrowcolor=MUTED, lightcolor=SOFT,
                    darkcolor=SOFT, gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", EDGE), ("disabled", CARD)])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=SOFT, bordercolor=SOFT, lightcolor=ACCENT, darkcolor=ACCENT)

    def card(self, parent, title, sub=None):
        outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="x", pady=(0, 14))
        tk.Label(outer, text=title, font=self.f_big, bg=CARD, fg=INK, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        if sub:
            tk.Label(outer, text=sub, font=self.f_small, bg=CARD, fg=MUTED, anchor="w", justify="left", wraplength=800).pack(fill="x", padx=18)
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="x", padx=18, pady=14)
        return inner

    def field(self, parent, row, label, var, width=34, col=0):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=4, padx=(0, 10))
        e = ttk.Entry(parent, textvariable=var, width=width)
        e.grid(row=row, column=col + 1, sticky="w", pady=4, padx=(0, 24))
        return e

    # ---- pages ----

    def page_phone(self):
        page = tk.Frame(self.body, bg=BG)
        c = self.card(page, "Phone", "Connect the phone by USB with USB debugging on, and accept the prompt on its screen.")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        self.device_box = ttk.Combobox(row, textvariable=self.device, state="readonly", width=44)
        self.device_box.pack(side="left")
        self.device_box.bind("<<ComboboxSelected>>", lambda e: self.read_facts())
        ttk.Button(row, text="Look again", command=self.refresh_devices).pack(side="left", padx=10)
        self.phone_state = tk.Label(c, text="", font=self.f_bold, bg=CARD, fg=MUTED, anchor="w", justify="left")
        self.phone_state.pack(fill="x", pady=(12, 2))
        self.phone_facts = tk.Label(c, text="", font=self.f_body, bg=CARD, fg=INK, anchor="w", justify="left")
        self.phone_facts.pack(fill="x")
        self.phone_warn = tk.Label(c, text="", font=self.f_body, bg=CARD, fg=WARN, anchor="w", justify="left", wraplength=800)
        self.phone_warn.pack(fill="x")

        c = self.card(page, "This examination", "Written into the log of every run. Leave blank what does not apply.")
        self.field(c, 0, "Examiner", self.examiner)
        self.field(c, 0, "Case or reference", self.reference, col=2)
        self.field(c, 1, "Label for this phone", self.label)
        self.field(c, 1, "Note", self.notes, col=2)

        c = self.card(page, "Earlier work")
        ttk.Button(c, text="Check an earlier export", command=self.check_earlier).pack(side="left")
        self.earlier = tk.Label(c, text="Picks a folder AppWalk wrote and checks its hash chain and every file.", font=self.f_small, bg=CARD, fg=MUTED,
                                anchor="w", justify="left", wraplength=560)
        self.earlier.pack(side="left", padx=12)
        return page

    def page_choose(self):
        page = tk.Frame(self.body, bg=BG)
        c = self.card(page, "App", "On the phone, open the app and the screen you want documented. Then check here.")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.package, width=46).pack(side="left")
        ttk.Button(row, text="Use the app in front", command=self.use_front_app).pack(side="left", padx=10)

        c = self.card(page, "What to document")
        ttk.Radiobutton(c, text="Rows I choose from the list that is open", variable=self.mode, value="list", style="Card.TRadiobutton").pack(anchor="w")
        ttk.Label(c, text="For a list of conversations. AppWalk reads the list, you pick rows, it opens and documents each.",
                  style="Muted.Card.TLabel").pack(anchor="w", padx=26, pady=(0, 8))
        ttk.Radiobutton(c, text="Only the screen that is open", variable=self.mode, value="screen", style="Card.TRadiobutton").pack(anchor="w")
        ttk.Label(c, text="For one thread or one list. AppWalk scrolls it from end to end.", style="Muted.Card.TLabel").pack(anchor="w", padx=26)

        c = self.card(page, "Options")
        ttk.Checkbutton(c, text="Keep pictures of the list itself", variable=self.keep_list, style="Card.TCheckbutton").pack(anchor="w")
        ttk.Label(c, text="They record which rows exist and which were unread, and they show one line of every row, chosen or not.",
                  style="Muted.Card.TLabel").pack(anchor="w", padx=26, pady=(0, 8))
        ttk.Checkbutton(c, text="If a lock or another app comes to the front, wait for me", variable=self.wait, style="Card.TCheckbutton").pack(anchor="w")
        ttk.Label(c, text="Otherwise the run ends there. AppWalk never gets past a lock by itself.", style="Muted.Card.TLabel").pack(anchor="w", padx=26)

        c = self.card(page, "Save in")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.out, width=62).pack(side="left")
        ttk.Button(row, text="Choose", command=self.choose_folder).pack(side="left", padx=10)
        return page

    def page_run(self):
        page = tk.Frame(self.body, bg=BG)
        outer = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="both", expand=True)
        left = tk.Frame(outer, bg=CARD)
        left.pack(side="left", fill="both", expand=True, padx=18, pady=16)
        tk.Label(left, textvariable=self.status, font=self.f_big, bg=CARD, fg=INK, anchor="w", justify="left", wraplength=520).pack(fill="x")
        tk.Label(left, textvariable=self.counter, font=self.f_body, bg=CARD, fg=MUTED, anchor="w").pack(fill="x", pady=(2, 10))
        self.bar = ttk.Progressbar(left, mode="determinate", maximum=100)
        self.bar.pack(fill="x")
        buttons = tk.Frame(left, bg=CARD)
        buttons.pack(fill="x", pady=12)
        self.start_btn = ttk.Button(buttons, text="Start", style="Accent.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(buttons, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(buttons, text="Stop after this step", command=self.stop_flag.set, state="disabled")
        self.stop_btn.pack(side="left")
        tk.Label(left, text="What AppWalk is doing", font=self.f_small, bg=CARD, fg=MUTED, anchor="w").pack(fill="x")
        self.log = tk.Text(left, height=12, wrap="word", state="disabled", relief="flat", bg=FIELD, fg=INK, insertbackground=INK, font=self.f_small,
                           highlightthickness=1, highlightbackground=LINE)
        self.log.pack(fill="both", expand=True)
        right = tk.Frame(outer, bg=CARD)
        right.pack(side="right", fill="y", padx=(0, 18), pady=16)
        tk.Label(right, text="Latest screenshot", font=self.f_small, bg=CARD, fg=MUTED).pack()
        self.picture = tk.Label(right, text="", bg=SOFT, width=30, height=24)
        self.picture.pack()
        return page

    def page_result(self):
        page = tk.Frame(self.body, bg=BG)
        outer = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="both", expand=True)
        self.verdict = tk.Label(outer, text="", font=self.f_title, bg=CARD, fg=OK, anchor="w", justify="left")
        self.verdict.pack(fill="x", padx=18, pady=(16, 4))
        self.result_text = tk.Text(outer, height=13, wrap="word", state="disabled", relief="flat", bg=CARD, fg=INK, font=self.f_body, highlightthickness=0)
        self.result_text.pack(fill="both", expand=True, padx=18)
        self.result_text.tag_configure("warn", foreground=WARN)
        hash_row = tk.Frame(outer, bg=CARD)
        hash_row.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(hash_row, text="Latest row hash. Write it somewhere else: it is what shows later that nothing was changed.",
                 font=self.f_small, bg=CARD, fg=MUTED, anchor="w").pack(fill="x")
        self.hash_value = tk.StringVar()
        ttk.Entry(hash_row, textvariable=self.hash_value, state="readonly", width=70).pack(side="left", pady=4)
        ttk.Button(hash_row, text="Copy", command=self.copy_hash).pack(side="left", padx=8)
        buttons = tk.Frame(outer, bg=CARD)
        buttons.pack(fill="x", padx=18, pady=14)
        self.open_btn = ttk.Button(buttons, text="Open transcript", style="Accent.TButton", command=self.open_transcript)
        self.open_btn.pack(side="left")
        ttk.Button(buttons, text="Open folder", command=self.open_folder).pack(side="left", padx=8)
        self.verify_btn = ttk.Button(buttons, text="Check this export", command=self.verify_now)
        self.verify_btn.pack(side="left")
        ttk.Button(buttons, text="Document something else", command=lambda: self.show(1)).pack(side="right")
        return page

    def show(self, n):
        if self.running and n != 2:
            return
        self.page = max(0, min(n, 3))
        for p in self.pages:
            p.pack_forget()
        self.pages[self.page].pack(fill="both", expand=True)
        for i, lab in enumerate(self.step_labels):
            lab.configure(fg=ACCENT if i == self.page else MUTED, font=self.f_bold if i == self.page else self.f_body)
        self.hint.configure(text="")
        self.back_btn.configure(state="normal" if self.page in (1, 2) and not self.running else "disabled")
        self.next_btn.configure(state="normal" if self.page in (0, 1) else "disabled")
        if self.page == 1 and not self.package.get() and self.serial():
            self.use_front_app()
        if self.page == 2 and not self.running:
            self.status.set("Ready. Leave the phone alone once you press Start.")
            self.counter.set(f"{self.package.get()}  on  {self.device.get()}")

    def next(self):
        if self.page == 0:
            if not self.serial():
                return self.hint.configure(text="No phone is ready yet.")
            if not self.label.get().strip():
                return self.hint.configure(text="Give this phone a label, for example phone A.")
        if self.page == 1:
            if not self.package.get().strip():
                return self.hint.configure(text="Open the app on the phone and press Use the app in front.")
            if not os.path.isdir(self.out.get()):
                return self.hint.configure(text="The folder to save in does not exist.")
        self.show(self.page + 1)

    # ---- phone ----

    def serial(self):
        return self.device.get().split()[0] if self.device.get() else ""

    def refresh_devices(self):
        try:
            found = appwalk.list_devices()
        except (OSError, subprocess.SubprocessError) as e:
            self.phone_state.configure(text="adb could not be run on this computer.", fg=BAD)
            self.phone_facts.configure(text=str(e))
            return
        ready = [f"{s}   {m}".rstrip() for s, state, m in found if state == "device"]
        waiting = [f"{s} ({state})" for s, state, m in found if state != "device"]
        self.device_box["values"] = ready
        if ready and self.device.get() not in ready:
            self.device.set(ready[0])
        if not ready:
            self.device.set("")
            self.facts = None
            self.phone_facts.configure(text="")
            self.phone_warn.configure(text="")
            self.phone_state.configure(fg=WARN, text=("Seen but not ready: " + ", ".join(waiting) + ". Accept the USB debugging prompt on the phone."
                                                      if waiting else "No phone found."))
            return
        self.read_facts()

    def read_facts(self):
        try:
            f = self.facts = appwalk.device_facts(self.serial())
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            self.phone_state.configure(text="The phone did not answer.", fg=BAD)
            self.phone_facts.configure(text=str(e))
            return
        self.phone_state.configure(text="Ready", fg=OK)
        on = lambda v: "not reported" if v is None else ("on" if v else "off")
        drift = f["phone_clock_minus_computer_s"]
        self.phone_facts.configure(text=(
            f"{f['manufacturer'] or ''} {f['model'] or ''}  ·  Android {f['android'] or '?'}  ·  battery {f['battery_percent'] if f['battery_percent'] is not None else '?'}%\n"
            f"Airplane mode {on(f['airplane_mode_on'])}  ·  Wi-Fi {on(f['wifi_on'])}  ·  "
            f"phone clock {'not read' if drift is None else f'{drift:+.1f} s from this computer'}"))
        warns = []
        if f["airplane_mode_on"] == 0:
            warns.append("The phone is online. Opening threads can send read receipts and download content. Turn airplane mode on unless that is intended.")
        if f["battery_percent"] is not None and f["battery_percent"] < 20:
            warns.append("Battery is under 20%.")
        if f["screen_off_timeout_ms"] is not None and f["screen_off_timeout_ms"] < 120000:
            warns.append("The screen turns off after less than 2 minutes. If it locks during a run, AppWalk will stop or wait.")
        if drift is not None and abs(drift) > 60:
            warns.append("The phone's clock is more than a minute from this computer's. Both are recorded in the log.")
        self.phone_warn.configure(text="\n".join(warns))

    def use_front_app(self):
        if not self.serial():
            return self.hint.configure(text="Pick a phone first.")
        try:
            pkg = appwalk.app_in_front(self.serial())
        except (OSError, subprocess.SubprocessError) as e:
            return self.hint.configure(text=f"Could not ask the phone: {e}")
        if pkg:
            self.package.set(pkg)
            self.hint.configure(text="")
        else:
            self.hint.configure(text="Could not tell which app is in front. Type its package name.")

    def choose_folder(self):
        d = filedialog.askdirectory(initialdir=self.out.get() or os.path.expanduser("~"))
        if d:
            self.out.set(d)

    def check_earlier(self):
        d = filedialog.askdirectory(initialdir=self.out.get() or os.path.expanduser("~"))
        if d:
            self.earlier.configure(text=self.verify_text(d)[0])

    def verify_text(self, folder):
        try:
            res = appwalk.verify_export(folder)
        except (OSError, ValueError, KeyError) as e:
            return f"Not an AppWalk export, or unreadable: {type(e).__name__}", False
        ok = all(good for _, _, good in res)
        return ("Checks out: " if ok else "PROBLEM: ") + "; ".join(f"{n}: {r}" for n, r, good in res if not good or n == "hash chain" or "match" in r), ok

    # ---- run ----

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        """Before anything runs: if the app in front is no longer the one named, ask which one is meant."""
        try:
            front = appwalk.app_in_front(self.serial())
        except (OSError, subprocess.SubprocessError):
            front = None
        named = self.package.get().strip()
        # A lock screen in front is not another app to document: the run itself waits for that.
        if not front or front == named or front == "com.android.systemui":
            return self.begin()
        win = tk.Toplevel(self.root, bg=CARD)
        win.title("A different app is in front")
        win.transient(self.root)
        tk.Label(win, text="A different app is in front", font=self.f_big, bg=CARD, fg=WARN, anchor="w").pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(win, wraplength=460, justify="left", font=self.f_body, bg=CARD, fg=INK,
                 text=f"You chose  {named}\nThe phone now shows  {front}\n\nDocument {front} instead?").pack(padx=16, pady=10, anchor="w")

        def close(action):
            win.destroy()
            if action == "switch":
                self.package.set(front)
                self.counter.set(f"{front}  on  {self.device.get()}")
            if action in ("switch", "keep"):
                self.begin()
        buttons = tk.Frame(win, bg=CARD)
        buttons.pack(pady=12, padx=16)
        self.front_yes = ttk.Button(buttons, text="Yes, document that app", style="Accent.TButton", command=lambda: close("switch"))
        self.front_yes.pack(side="left", padx=4)
        self.front_no = ttk.Button(buttons, text="No, keep my choice", command=lambda: close("keep"))
        self.front_no.pack(side="left", padx=4)
        self.front_cancel = ttk.Button(buttons, text="Cancel", command=lambda: close("cancel"))
        self.front_cancel.pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close("cancel"))
        self.front_win = win

    def begin(self):
        save_settings(dict(load_settings(), examiner=self.examiner.get(), device_label=self.label.get(), out=self.out.get()))
        self.stop_flag.clear()
        self.paused.clear()
        self.export = self.result = None
        self.running, self.started_at = True, time.time()
        self.shots, self.items_total, self.items_done = 0, 0, 0
        self.bar.configure(mode="indeterminate")
        self.bar.start(60)
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.back_btn.configure(state="disabled")
        self.theme_btn.configure(state="disabled")
        self.status.set("Working. Do not touch the phone unless asked.")
        scenario = self.reference.get().strip() or "appwalk"
        job = dict(serial=self.serial(), package=self.package.get().strip(), out=self.out.get().strip(), scenario=scenario,
                   device_label=self.label.get().strip(), mode=self.mode.get(), capture_list=self.keep_list.get(),
                   choose=self.ask_rows, ask=self.ask_blocked if self.wait.get() else None,
                   say=lambda *a: self.events.put(("say", " ".join(str(x) for x in a))),
                   on_screenshot=lambda path, step, item: self.events.put(("shot", path, step, item)),
                   should_stop=self.stop_flag.is_set, pause_gate=self.pause_gate,
                   case={"examiner": self.examiner.get().strip(), "reference": self.reference.get().strip(), "note": self.notes.get().strip()})
        threading.Thread(target=self.work, args=(job,), daemon=True).start()

    def work(self, job):
        try:
            self.events.put(("done", appwalk.run_job(**job)))
        except Exception as e:  # shown to the examiner, never swallowed
            self.events.put(("failed", f"{type(e).__name__}: {e}"))

    def pause_gate(self):
        """Called by the walk between steps, on its own thread. Holds it while Pause is on."""
        if not self.paused.is_set():
            return 0
        began = time.time()
        while self.paused.is_set() and not self.stop_flag.is_set():
            time.sleep(0.2)
        return time.time() - began

    def toggle_pause(self):
        if self.paused.is_set():
            self.paused.clear()
            self.pause_btn.configure(text="Pause")
            self.status.set("Working. Do not touch the phone unless asked.")
        else:
            self.paused.set()
            self.pause_btn.configure(text="Resume")
            self.status.set("Pausing after this step. Leave the phone on the same screen.")

    def from_worker(self, kind, *payload):
        box = {"event": threading.Event(), "answer": None}
        self.events.put((kind, box) + payload)
        box["event"].wait()
        return box["answer"]

    def ask_rows(self, titles):
        picked = self.from_worker("rows", titles)
        return picked, "examiner, in the window"

    def ask_blocked(self, why):
        return self.from_worker("blocked", why)

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                getattr(self, "on_" + ev[0])(*ev[1:])
        except queue.Empty:
            pass
        self.poller = self.root.after(100, self.poll)

    def tick(self):
        if self.running and self.started_at:
            el = int(time.time() - self.started_at)
            rows = f"  ·  row {min(self.items_done + 1, self.items_total)} of {self.items_total}" if self.items_total else ""
            self.counter.set(f"{self.shots} screenshots{rows}  ·  {el // 60}:{el % 60:02d}")
        self.ticker = self.root.after(1000, self.tick)

    def on_say(self, text):
        self.say(text)

    def on_shot(self, path, step, item):
        self.shots = step
        if item and item != getattr(self, "current_item", None):
            if getattr(self, "current_item", None):
                self.items_done += 1
            self.current_item = item
            if self.items_total:
                self.bar.configure(value=100 * self.items_done / self.items_total)
        try:
            img = tk.PhotoImage(file=path)
            self.thumb = img.subsample(max(1, img.height() // 400))
            self.picture.configure(image=self.thumb, width=self.thumb.width(), height=self.thumb.height())
        except tk.TclError:
            pass
        if not self.paused.is_set():
            self.status.set("Documenting " + item if item else "Reading the screen that is open")

    def on_done(self, result):
        self.result, self.export = result, result["export"]
        self.end_run()
        self.render_result()
        self.show(3)

    def render_result(self):
        result = self.result
        ok = result["ok"]
        self.verdict.configure(text="Done. Everything was captured and confirmed." if ok else "Finished, with things to look at.", fg=OK if ok else WARN)
        self.hash_value.set(result["manifest"]["hash_chain"]["last_row_hash"])
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        for line in plain_summary(result):
            self.result_text.insert("end", line[1] + "\n", ("warn",) if line[0] else ())
        self.result_text.insert("end", "\nSaved in " + self.export + "\n")
        self.result_text.configure(state="disabled")
        self.open_btn.configure(state="normal" if os.path.exists(os.path.join(self.export, "transcript.html")) else "disabled")

    def on_failed(self, message):
        self.end_run()
        self.status.set("The run failed.")
        self.say("FAILED: " + message)
        self.hint.configure(text="The run failed before it could finish. The lines above say why.")

    def end_run(self):
        self.running = False
        self.current_item = None
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100)
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause")
        self.stop_btn.configure(state="disabled")
        self.back_btn.configure(state="normal")
        self.theme_btn.configure(state="normal")

    def on_rows(self, box, titles):
        win = tk.Toplevel(self.root, bg=CARD)
        win.title("Rows to document")
        win.transient(self.root)
        tk.Label(win, text=f"{len(titles)} rows found", font=self.f_big, bg=CARD, fg=INK, anchor="w").pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(win, text="Tick the rows to open and document. Type to narrow the list.", font=self.f_small, bg=CARD, fg=MUTED, anchor="w").pack(fill="x", padx=16)
        find = tk.StringVar()
        ttk.Entry(win, textvariable=find, width=44).pack(fill="x", padx=16, pady=8)
        holder = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        holder.pack(fill="both", expand=True, padx=16)
        canvas = tk.Canvas(holder, bg=CARD, highlightthickness=0, width=460, height=min(420, 34 * max(4, len(titles))))
        bar = ttk.Scrollbar(holder, command=canvas.yview)
        inner = tk.Frame(canvas, bg=CARD)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.row_vars = {t: tk.BooleanVar(value=False) for t in titles}
        boxes = {}
        for t in titles:
            boxes[t] = ttk.Checkbutton(inner, text=t, variable=self.row_vars[t], style="Card.TCheckbutton", command=lambda: count())
        chosen = tk.Label(win, text="", font=self.f_small, bg=CARD, fg=MUTED)

        def count():
            n = sum(v.get() for v in self.row_vars.values())
            chosen.configure(text=f"{n} chosen")
            self.rows_ok.configure(state="normal" if n else "disabled")

        def relist(*_):
            for b in boxes.values():
                b.pack_forget()
            for t in titles:
                if find.get().lower() in t.lower():
                    boxes[t].pack(anchor="w", padx=8, pady=2)
            inner.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        find.trace_add("write", relist)

        def set_shown(value):
            for t in titles:
                if find.get().lower() in t.lower():
                    self.row_vars[t].set(value)
            count()

        def close(picked):
            self.items_total = len(picked)
            if picked:
                self.bar.stop()
                self.bar.configure(mode="determinate", value=0)
            box["answer"] = picked
            win.destroy()
            box["event"].set()
        buttons = tk.Frame(win, bg=CARD)
        buttons.pack(fill="x", padx=16, pady=12)
        ttk.Button(buttons, text="Tick all shown", command=lambda: set_shown(True)).pack(side="left")
        ttk.Button(buttons, text="Clear", command=lambda: set_shown(False)).pack(side="left", padx=6)
        chosen.pack(in_=buttons, side="left", padx=8)
        self.rows_ok = ttk.Button(buttons, text="Document these", style="Accent.TButton", state="disabled",
                                  command=lambda: close([t for t in titles if self.row_vars[t].get()]))
        self.rows_ok.pack(side="right")
        ttk.Button(buttons, text="None", command=lambda: close([])).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: close([]))
        self.rows_win = win
        relist()
        count()

    def on_blocked(self, box, why):
        win = tk.Toplevel(self.root, bg=CARD)
        win.title("AppWalk is waiting")
        win.transient(self.root)
        tk.Label(win, text="AppWalk is waiting", font=self.f_big, bg=CARD, fg=WARN, anchor="w").pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(win, wraplength=440, justify="left", font=self.f_body, bg=CARD, fg=INK,
                 text=f"{plain_block(why)}\n\nDeal with it on the phone and bring the screen to document back to the front, "
                      "then press Done on the phone.\n\nOr type the code here and AppWalk types it on the phone. The code is not recorded.").pack(padx=16, pady=8)
        code = tk.StringVar()
        ttk.Entry(win, textvariable=code, show="•", width=28).pack(pady=4)

        def close(answer):
            box["answer"] = answer
            win.destroy()
            box["event"].set()
        buttons = tk.Frame(win, bg=CARD)
        buttons.pack(pady=12, padx=16)
        self.blocked_done = ttk.Button(buttons, text="Done on the phone", style="Accent.TButton", command=lambda: close(""))
        self.blocked_done.pack(side="left", padx=4)
        self.blocked_code, self.blocked_type = code, ttk.Button(buttons, text="Type this code on the phone",
                                                                command=lambda: close(code.get()) if code.get() else None)
        self.blocked_type.pack(side="left", padx=4)
        ttk.Button(buttons, text="End the run", command=lambda: close(None)).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close(None))
        self.blocked_win = win

    # ---- result ----

    def open_path(self, path):
        if sys.platform == "darwin":
            subprocess.Popen(["open", path], stdin=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path], stdin=subprocess.DEVNULL)

    def open_transcript(self):
        if self.export:
            self.open_path(os.path.join(self.export, "transcript.html"))

    def open_folder(self):
        if self.export:
            self.open_path(self.export)

    def copy_hash(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.hash_value.get())

    def verify_now(self):
        if not self.export:
            return
        text, ok = self.verify_text(self.export)
        self.result_text.configure(state="normal")
        self.result_text.insert("end", "\n" + text + "\n", () if ok else ("warn",))
        self.result_text.see("end")
        self.result_text.configure(state="disabled")

    def close(self):
        for t in (self.poller, self.ticker):
            try:
                self.root.after_cancel(t)
            except tk.TclError:
                pass
        self.root.destroy()


def plain_summary(result):
    """The result in sentences an examiner can read without knowing the tool: (is_warning, text)."""
    s, out = result["summary"], []
    shots = result["manifest"]["screenshots"]
    blank = sum(1 for x in shots if x["status"] == "blank")
    if s.get("mode") == "list":
        out.append((False, f"{s['items_found']} rows were in the list. {len(s['items_selected'])} were chosen and {s['items_walked']} were opened and documented."))
        for w in s.get("walks", []):
            good = w["end_confirmed"] and w["rewind_end_confirmed"] and not w["gaps"] and not w["unsettled"]
            out.append((not good, f"   {w['item']}: {w['screens']} screenshots" + ("" if good else ". " + problems(w))))
        for t in s.get("items_not_opened", []):
            out.append((True, f"   {t}: could not be opened"))
        if s.get("duplicate_titles"):
            out.append((True, "Rows sharing a title, of which only the first was opened: " + ", ".join(s["duplicate_titles"])))
        if s.get("stop_reason"):
            out.append((True, "The run ended early: " + s["stop_reason"]))
    else:
        good = s["end_confirmed"] and s["rewind_end_confirmed"] is not False and not s["gaps"] and not s["unsettled"]
        out.append((not good, f"{s['screens']} screenshots of the open screen" + ("." if good else ". " + problems(s))))
    out.append((False, f"{len(shots)} screenshots in all, each with the text the app reported for it."))
    if blank:
        out.append((True, f"{blank} screenshots are black: the app blocks screenshots there. The text for those screens is in the transcript only."))
    for n in result["manifest"].get("transcript_notes", []):
        if "shared no line" in n or "not written" in n:
            out.append((True, "Transcript: " + n))
    return out


def plain_block(why):
    """Why the walk is waiting, in words for someone who does not know package names."""
    if "com.android.systemui" in why:
        return "The phone's lock screen or a system screen is in front."
    if "password field" in why:
        return "The app is asking for a password or code."
    return "Something other than the chosen app is in front (" + why.split(" is in front")[0] + ")."


def problems(w):
    p = []
    if w.get("stop_reason") and "confirmed: the list moved" not in w["stop_reason"] and "one screen of content" not in w["stop_reason"]:
        p.append(w["stop_reason"])
    if w.get("gaps"):
        p.append(f"{w['gaps']} place(s) where two screenshots did not overlap")
    if w.get("unsettled"):
        p.append(f"{w['unsettled']} screenshot(s) taken while the screen was still changing")
    return "; ".join(p) or "the end was not confirmed"


def main():
    if sys.platform == "darwin" and tk.TkVersion >= 9 and not os.environ.get("APPWALK_ALLOW_TK9"):
        sys.exit("This Python has Tk %s. On macOS, windows like this one have crashed with Tk 9.\n"
                 "Run it with a Python that has Tk 8.6, for example: python3.12 appwalk_gui.py\n"
                 "(set APPWALK_ALLOW_TK9=1 to try anyway)" % tk.TkVersion)
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
