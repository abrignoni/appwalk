#!/usr/bin/env python3
"""AppWalk window: the same runs as appwalk.py, in four steps and with no terminal.

1. Phone: is a phone connected and ready, what state is it in, who is doing the work.
2. What to document: the app in front, the open screen or rows from the open list.
3. Run: start, watch each screenshot arrive, pause or stop, answer when the tool has to wait.
4. Result: what was captured, warnings in plain words, the report, the transcript, the row hash.

Nothing is unlocked or bypassed. When a lock or another app is in front the tool waits; the examiner
deals with it on the phone, or types the code here for the tool to enter. The code is not recorded.

This window is drawn with CustomTkinter (MIT licence). Without it, the plain Tk window in
appwalk_gui_classic.py opens instead; both run the same code underneath.

Run with a Python whose Tk is 8.6. On macOS the python.org 3.14 build ships Tk 9, which has crashed
this kind of window; use python3.12 there.
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appwalk  # noqa: E402,F401
import appwalk_gui_classic as classic  # noqa: E402
from appwalk_gui_classic import plain_block, plain_summary, problems, system_theme  # noqa: E402,F401

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

SETTINGS = classic.SETTINGS

# Each colour is (light, dark); CustomTkinter picks by the appearance mode.
BG, CARD, SOFT = ("#F3F4F6", "#111318"), ("#FFFFFF", "#1A1D23"), ("#EEF0F3", "#22262E")
INK, MUTED, LINE = ("#111827", "#E6E8EC"), ("#6B7280", "#9AA3AF"), ("#E3E6EA", "#2C313A")
ACCENT, ACCENT_HOVER = ("#2563EB", "#3B82F6"), ("#1D4ED8", "#2563EB")
OK, WARN, BAD = ("#15803D", "#4ADE80"), ("#B45309", "#F5B041"), ("#B91C1C", "#FF7B72")
GHOST = ("#FFFFFF", "#1A1D23")


def load_settings():
    classic.SETTINGS = sys.modules[__name__].SETTINGS
    return classic.load_settings()


def save_settings(values):
    classic.SETTINGS = sys.modules[__name__].SETTINGS
    classic.save_settings(values)


def plain_image(label, image):
    """Puts a CTkImage, or a plain Tk PhotoImage when PIL.ImageTk is missing, on a CTkLabel. CustomTkinter
    accepts the PhotoImage but warns that it will not scale; that is known and expected here."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        label.configure(image=image)


if ctk is not None:
    class Label(ctk.CTkLabel):
        """A label that also takes the plain Tk names the shared code uses (fg for text colour)."""

        def configure(self, require_redraw=False, **kw):
            if "fg" in kw:
                kw["text_color"] = kw.pop("fg")
            kw.pop("bg", None)
            return super().configure(require_redraw=require_redraw, **kw)

        config = configure

    class Combo(ctk.CTkComboBox):
        """A combo box that answers combo["values"], as the shared code and the tests use it."""

        def __getitem__(self, key):
            return tuple(self.cget(key))

        def __setitem__(self, key, value):
            self.configure(**{key: list(value)})

    class Primary(ctk.CTkButton):
        """The blue button. Greyed when it is off, so a pressed Start does not look ready to press again."""

        def configure(self, require_redraw=False, **kw):
            if "state" in kw:
                off = kw["state"] == "disabled"
                kw["fg_color"] = SOFT if off else ACCENT
            return super().configure(require_redraw=require_redraw, **kw)

    class Progress(ctk.CTkProgressBar):
        """A progress bar that takes the plain Tk calls: configure(mode=, value=0..100) and start(ms)."""

        def configure(self, require_redraw=False, **kw):
            value = kw.pop("value", None)
            if "maximum" in kw:
                kw.pop("maximum")
            out = super().configure(require_redraw=require_redraw, **kw) if kw else None
            if value is not None:
                self.set(max(0.0, min(1.0, value / 100)))
            return out

        def start(self, interval=None):
            return super().start()

    class App(classic.App):
        """The four-step window, drawn with CustomTkinter. The behaviour comes from the plain Tk window."""

        def __init__(self, root):
            classic.SETTINGS = sys.modules[__name__].SETTINGS
            remembered = load_settings()
            theme = remembered.get("theme") if remembered.get("theme") in ("light", "dark") else system_theme()
            ctk.set_appearance_mode(theme)
            ctk.set_default_color_theme("blue")
            # The shared code colours its text with the plain window's names; here each is a (light, dark) pair.
            classic.__dict__.update(INK=INK, MUTED=MUTED, ACCENT=ACCENT, OK=OK, WARN=WARN, BAD=BAD)
            super().__init__(root)

        # ---- look ----

        def build(self):
            root = self.root
            for w in root.winfo_children():
                w.destroy()
            ctk.set_appearance_mode(self.theme)
            root.configure(fg_color=BG)
            self.style()
            head = ctk.CTkFrame(root, fg_color=CARD, corner_radius=0, border_width=0)
            head.pack(fill="x")
            try:
                from PIL import Image, ImageTk  # noqa: F401 - CTkImage needs ImageTk, which Ubuntu packs apart
                im = Image.open(os.path.join(classic.ASSETS, "appwalk_256.png"))
                self.logo = ctk.CTkImage(light_image=im, dark_image=im, size=(36, 36))
            except ImportError:
                try:
                    self.logo = tk.PhotoImage(master=root, file=os.path.join(classic.ASSETS, "appwalk_32.png"))
                except tk.TclError:
                    self.logo = None
            except OSError:
                self.logo = None
            if self.logo is not None:
                logo_label = Label(head, text="")
                plain_image(logo_label, self.logo)
                logo_label.pack(side="left", padx=(22, 0), pady=12)
            Label(head, text="AppWalk", font=self.f_title, text_color=INK).pack(side="left", padx=(10 if self.logo else 24, 18), pady=16)
            self.step_labels = []
            for i, name in enumerate(classic.STEPS):
                lab = Label(head, text=f"{i + 1}  {name}", font=self.f_body, text_color=MUTED)
                lab.pack(side="left", padx=12)
                self.step_labels.append(lab)
            self.theme_btn = ctk.CTkSegmentedButton(head, values=["Light", "Dark"], command=self.pick_theme,
                                                    font=self.f_small, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
            self.theme_btn.set("Dark" if self.theme == "dark" else "Light")
            self.theme_btn.invoke = self.switch_theme
            self.theme_btn.pack(side="right", padx=20)
            ctk.CTkFrame(root, height=1, fg_color=LINE, corner_radius=0).pack(fill="x")

            self.body = ctk.CTkFrame(root, fg_color="transparent")
            self.body.pack(fill="both", expand=True, padx=24, pady=18)
            self.foot = ctk.CTkFrame(root, fg_color="transparent")
            self.foot.pack(fill="x", padx=24, pady=(0, 18))
            self.back_btn = self.ghost(self.foot, "Back", lambda: self.show(self.page - 1))
            self.back_btn.pack(side="left")
            self.next_btn = self.primary(self.foot, "Next", self.next)
            self.next_btn.pack(side="right")
            self.hint = Label(self.foot, text="", font=self.f_body, text_color=WARN, wraplength=560, justify="right")
            self.hint.pack(side="right", padx=14)
            self.pages = [self.page_phone(), self.page_choose(), self.page_run(), self.page_result()]
            self.show(self.page)
            if self.serial():
                self.read_facts()
            if self.result:
                self.render_result()

        def pick_theme(self, value):
            wanted = "dark" if value == "Dark" else "light"
            if wanted != self.theme:
                self.switch_theme()

        def switch_theme(self):
            if self.running:
                self.theme_btn.set("Dark" if self.theme == "dark" else "Light")
                return
            self.theme = "light" if self.theme == "dark" else "dark"
            save_settings(dict(load_settings(), theme=self.theme))
            ctk.set_appearance_mode(self.theme)   # every colour here is a (light, dark) pair: no rebuild needed
            self.theme_btn.set("Dark" if self.theme == "dark" else "Light")
            self.result_text.tag_config("warn", foreground=self.warn_ink())

        def warn_ink(self):
            return WARN[1] if self.theme == "dark" else WARN[0]

        def style(self):
            self.f_body = ctk.CTkFont(size=14)
            self.f_small = ctk.CTkFont(size=12)
            self.f_bold = ctk.CTkFont(size=14, weight="bold")
            self.f_title = ctk.CTkFont(size=20, weight="bold")
            self.f_big = ctk.CTkFont(size=17, weight="bold")

        def primary(self, parent, text, command):
            return Primary(parent, text=text, command=command, font=self.f_bold, height=38, corner_radius=10,
                           fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", text_color_disabled=MUTED)

        def ghost(self, parent, text, command):
            return ctk.CTkButton(parent, text=text, command=command, font=self.f_body, height=38, corner_radius=10,
                                 fg_color=GHOST, hover_color=SOFT, text_color=INK, border_width=1, border_color=LINE,
                                 text_color_disabled=MUTED)

        def card(self, parent, title, sub=None):
            outer = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
            outer.pack(fill="x", pady=(0, 14))
            Label(outer, text=title, font=self.f_big, text_color=INK, anchor="w").pack(fill="x", padx=20, pady=(16, 0))
            if sub:
                Label(outer, text=sub, font=self.f_small, text_color=MUTED, anchor="w", justify="left",
                      wraplength=820).pack(fill="x", padx=20)
            inner = ctk.CTkFrame(outer, fg_color="transparent")
            inner.pack(fill="x", padx=20, pady=(10, 16))
            return inner

        def entry(self, parent, var, width=300, **kw):
            return ctk.CTkEntry(parent, textvariable=var, width=width, height=36, corner_radius=8, font=self.f_body,
                                border_color=LINE, fg_color=SOFT, text_color=INK, **kw)

        def field(self, parent, row, label, var, width=300, col=0):
            Label(parent, text=label, font=self.f_body, text_color=INK).grid(row=row, column=col, sticky="w", pady=5, padx=(0, 12))
            e = self.entry(parent, var, width=width)
            e.grid(row=row, column=col + 1, sticky="w", pady=5, padx=(0, 28))
            return e

        def muted(self, parent, text, **pack):
            lab = Label(parent, text=text, font=self.f_small, text_color=MUTED, anchor="w", justify="left", wraplength=780)
            lab.pack(fill="x", **pack)
            return lab

        # ---- pages ----

        def page_phone(self):
            page = ctk.CTkFrame(self.body, fg_color="transparent")
            c = self.card(page, "Phone", "Connect the phone by USB with USB debugging on, and accept the prompt on its screen.")
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x")
            self.device_box = Combo(row, variable=self.device, values=[], width=420, height=36, corner_radius=8, font=self.f_body,
                                    state="readonly", command=lambda v: self.read_facts(), border_color=LINE, button_color=LINE,
                                    fg_color=SOFT, text_color=INK)
            self.device_box.pack(side="left")
            self.ghost(row, "Look again", self.refresh_devices).pack(side="left", padx=10)
            self.phone_state = Label(c, text="", font=self.f_bold, text_color=MUTED, anchor="w", justify="left")
            self.phone_state.pack(fill="x", pady=(12, 2))
            self.phone_facts = Label(c, text="", font=self.f_body, text_color=INK, anchor="w", justify="left")
            self.phone_facts.pack(fill="x")
            self.phone_warn = Label(c, text="", font=self.f_body, text_color=WARN, anchor="w", justify="left", wraplength=800)
            self.phone_warn.pack(fill="x")

            c = self.card(page, "This examination", "Written into the log of every run. Leave blank what does not apply.")
            self.field(c, 0, "Examiner", self.examiner)
            self.field(c, 0, "Case or reference", self.reference, col=2)
            self.field(c, 1, "Label for this phone", self.label)
            self.field(c, 1, "Note", self.notes, col=2)

            c = self.card(page, "Earlier work")
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x")
            self.ghost(row, "Check an earlier export", self.check_earlier).pack(side="left")
            self.earlier = Label(row, text="Picks a folder AppWalk wrote and checks its hash chain and every file.", font=self.f_small,
                                 text_color=MUTED, anchor="w", justify="left", wraplength=540)
            self.earlier.pack(side="left", padx=12)
            return page

        def page_choose(self):
            page = ctk.CTkFrame(self.body, fg_color="transparent")
            c = self.card(page, "App", "On the phone, open the app and the screen you want documented.")
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x")
            self.entry(row, self.package, width=420).pack(side="left")
            self.ghost(row, "Use the app in front", self.use_front_app).pack(side="left", padx=10)

            c = self.card(page, "What to document")
            for value, text, sub in (("list", "Rows I choose from the list that is open",
                                      "For a list of conversations. AppWalk reads the list, you pick rows, it opens and documents each."),
                                     ("screen", "Only the screen that is open", "For one conversation or one list. AppWalk scrolls it from end to end.")):
                ctk.CTkRadioButton(c, text=text, variable=self.mode, value=value, font=self.f_body, text_color=INK,
                                   fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED).pack(anchor="w", pady=(4, 0))
                self.muted(c, sub, padx=30, pady=(0, 6))

            c = self.card(page, "Options")
            ctk.CTkCheckBox(c, text="Keep pictures of the list itself", variable=self.keep_list, font=self.f_body, text_color=INK,
                            fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED, corner_radius=6).pack(anchor="w")
            self.muted(c, "They record which rows exist and which were unread, and they show one line of every row, chosen or not.",
                       padx=30, pady=(0, 8))
            ctk.CTkCheckBox(c, text="If a lock or another app comes to the front, wait for me", variable=self.wait, font=self.f_body,
                            text_color=INK, fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED, corner_radius=6).pack(anchor="w")
            self.muted(c, "Otherwise the run ends there. AppWalk never gets past a lock by itself.", padx=30)

            c = self.card(page, "Save in")
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x")
            self.entry(row, self.out, width=560).pack(side="left")
            self.ghost(row, "Choose", self.choose_folder).pack(side="left", padx=10)
            return page

        def page_run(self):
            page = ctk.CTkFrame(self.body, fg_color="transparent")
            page.columnconfigure(0, weight=1, uniform="half")
            page.columnconfigure(1, weight=1, uniform="half")
            page.rowconfigure(0, weight=1)
            left = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
            Label(left, textvariable=self.status, font=self.f_big, text_color=INK, anchor="w", justify="left",
                  wraplength=380).pack(fill="x", padx=20, pady=(18, 0))
            Label(left, textvariable=self.counter, font=self.f_body, text_color=MUTED, anchor="w", justify="left",
                  wraplength=380).pack(fill="x", padx=20, pady=(2, 10))
            self.bar = Progress(left, height=8, corner_radius=4, progress_color=ACCENT, fg_color=SOFT)
            self.bar.set(0)
            self.bar.pack(fill="x", padx=20)
            buttons = ctk.CTkFrame(left, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=14)
            self.start_btn = self.primary(buttons, "Start", self.start)
            self.start_btn.pack(side="left")
            self.pause_btn = self.ghost(buttons, "Pause", self.toggle_pause)
            self.pause_btn.configure(state="disabled", width=90)
            self.pause_btn.pack(side="left", padx=8)
            self.stop_btn = self.ghost(buttons, "Stop after this step", self.stop_flag.set)
            self.stop_btn.configure(state="disabled")
            self.stop_btn.pack(side="left")
            Label(left, text="What AppWalk is doing", font=self.f_small, text_color=MUTED, anchor="w").pack(fill="x", padx=20)
            self.log = ctk.CTkTextbox(left, font=self.f_small, fg_color=SOFT, text_color=INK, corner_radius=10, wrap="word",
                                      border_width=0)
            self.log.configure(state="disabled")
            self.log.pack(fill="both", expand=True, padx=20, pady=(4, 18))
            right = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
            right.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
            Label(right, text="Latest screenshot", font=self.f_small, text_color=MUTED).pack(pady=(14, 6))
            self.picture = ctk.CTkLabel(right, text="", fg_color=SOFT, corner_radius=10)
            self.picture.pack(fill="both", expand=True, padx=20, pady=(0, 18))
            self.picture.bind("<Configure>", lambda e: self.fit_picture())
            self.last_shot = None
            return page

        def fit_picture(self):
            path = getattr(self, "last_shot", None)
            if not path or not self.picture.winfo_exists():
                return
            w, h = max(40, self.picture.winfo_width() - 12), max(40, self.picture.winfo_height() - 12)
            try:
                from PIL import Image, ImageTk  # noqa: F401 - CTkImage needs ImageTk, which Ubuntu packs apart
                im = Image.open(path)
                scale = min(w / im.width, h / im.height)
                self.thumb = ctk.CTkImage(light_image=im, dark_image=im, size=(max(1, int(im.width * scale)), max(1, int(im.height * scale))))
            except ImportError:
                # Tk reads PNG itself; shrink by a whole factor to fit.
                try:
                    img = tk.PhotoImage(master=self.root, file=path)
                except tk.TclError:
                    return
                factor = max(1, -(-img.width() // w), -(-img.height() // h))
                self.thumb = img.subsample(factor)
            except OSError:
                return
            plain_image(self.picture, self.thumb)

        def page_result(self):
            page = ctk.CTkFrame(self.body, fg_color="transparent")
            outer = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
            outer.pack(fill="both", expand=True)
            self.verdict = Label(outer, text="", font=self.f_title, text_color=OK, anchor="w", justify="left")
            self.verdict.pack(fill="x", padx=20, pady=(18, 6))
            self.result_text = ctk.CTkTextbox(outer, font=self.f_body, fg_color="transparent", text_color=INK, wrap="word",
                                              border_width=0, height=220)
            self.result_text.pack(fill="both", expand=True, padx=14)
            self.result_text.tag_config("warn", foreground=self.warn_ink())
            self.result_text.configure(state="disabled")
            hash_card = ctk.CTkFrame(outer, fg_color=SOFT, corner_radius=10)
            hash_card.pack(fill="x", padx=20, pady=(6, 0))
            Label(hash_card, text="Latest row hash. Write it somewhere else. Checked later, it shows whether the log was changed or cut.",
                  font=self.f_small, text_color=MUTED, anchor="w").pack(fill="x", padx=14, pady=(10, 2))
            self.hash_value = tk.StringVar()
            row = ctk.CTkFrame(hash_card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 12))
            ctk.CTkEntry(row, textvariable=self.hash_value, state="readonly", height=34, font=ctk.CTkFont(family="Menlo", size=12),
                         fg_color=CARD, border_color=LINE, text_color=INK).pack(side="left", fill="x", expand=True)
            self.ghost(row, "Copy", self.copy_hash).pack(side="left", padx=(8, 0))
            buttons = ctk.CTkFrame(outer, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=16)
            self.report_btn = self.primary(buttons, "Open report", self.open_report)
            self.report_btn.pack(side="left")
            self.open_btn = self.ghost(buttons, "Open transcript", self.open_transcript)
            self.open_btn.pack(side="left", padx=8)
            self.ghost(buttons, "Open folder", self.open_folder).pack(side="left")
            self.verify_btn = self.ghost(buttons, "Check this export", self.verify_now)
            self.verify_btn.pack(side="left", padx=8)
            self.teach_btn = self.ghost(buttons, "Teach this app", self.teach)
            self.teach_btn.pack(side="left")
            self.ghost(buttons, "Document something else", lambda: self.show(1)).pack(side="right")
            return page

        # ---- dialogs ----

        def dialog(self, title, heading, color=INK):
            win = ctk.CTkToplevel(self.root, fg_color=CARD)
            classic.windows_icon(win)
            win.title(title)
            win.transient(self.root)
            self.raise_dialog(win)
            Label(win, text=heading, font=self.f_big, text_color=color, anchor="w").pack(fill="x", padx=20, pady=(18, 4))
            return win

        def raise_dialog(self, win):
            win.lift()
            win.attributes("-topmost", True)
            win.after(400, lambda: win.winfo_exists() and win.attributes("-topmost", False))
            win.after(50, lambda: win.winfo_exists() and win.focus_force())
            win.after(20, lambda: self.place_dialog(win))

        def start(self):
            try:
                front = appwalk.app_in_front(self.serial())
            except Exception:
                front = None
            named = self.package.get().strip()
            if not front or front == named or front == "com.android.systemui":
                return self.begin()
            win = self.dialog("A different app is in front", "A different app is in front", WARN)
            Label(win, wraplength=460, justify="left", font=self.f_body, text_color=INK, anchor="w",
                  text=f"You chose  {named}\nThe phone now shows  {front}\n\nDocument {front} instead?").pack(fill="x", padx=20, pady=8)

            def close(action):
                win.destroy()
                if action == "switch":
                    self.package.set(front)
                    self.counter.set(f"{front}  on  {self.device.get()}")
                if action in ("switch", "keep"):
                    self.begin()
            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=(4, 18))
            self.front_yes = self.primary(buttons, "Yes, document that app", lambda: close("switch"))
            self.front_yes.pack(side="left")
            self.front_no = self.ghost(buttons, "No, keep my choice", lambda: close("keep"))
            self.front_no.pack(side="left", padx=8)
            self.front_cancel = self.ghost(buttons, "Cancel", lambda: close("cancel"))
            self.front_cancel.pack(side="left")
            win.protocol("WM_DELETE_WINDOW", lambda: close("cancel"))
            self.front_win = win

        def on_rows(self, box, titles):
            win = self.dialog("Rows to document", f"{len(titles)} rows found")
            self.muted(win, "Tick the rows to open and document. Type to narrow the list.", padx=20)
            find = tk.StringVar()
            self.entry(win, find, width=460).pack(fill="x", padx=20, pady=10)
            lst = ctk.CTkScrollableFrame(win, fg_color=SOFT, corner_radius=10, height=min(380, 34 * max(4, len(titles))), width=460)
            lst.pack(fill="both", expand=True, padx=20)
            self.row_vars = {t: tk.BooleanVar(value=False) for t in titles}
            boxes = {t: ctk.CTkCheckBox(lst, text=t, variable=self.row_vars[t], font=self.f_body, text_color=INK, fg_color=ACCENT,
                                        hover_color=ACCENT_HOVER, border_color=MUTED, corner_radius=6) for t in titles}
            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=16)
            chosen = Label(buttons, text="", font=self.f_small, text_color=MUTED)

            def count():
                n = sum(v.get() for v in self.row_vars.values())
                chosen.configure(text=f"{n} chosen")
                self.rows_ok.configure(state="normal" if n else "disabled")

            def relist(*_):
                for b in boxes.values():
                    b.pack_forget()
                for t in titles:
                    if find.get().lower() in t.lower():
                        boxes[t].pack(anchor="w", padx=8, pady=3)
            find.trace_add("write", relist)

            def set_shown(value):
                for t in titles:
                    if find.get().lower() in t.lower():
                        self.row_vars[t].set(value)

            def close(picked):
                self.items_total = len(picked)
                if picked:
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=0)
                box["answer"] = picked
                win.destroy()
                box["event"].set()
            self.ghost(buttons, "Tick all shown", lambda: set_shown(True)).pack(side="left")
            self.ghost(buttons, "Clear", lambda: set_shown(False)).pack(side="left", padx=6)
            chosen.pack(side="left", padx=8)
            self.rows_ok = self.primary(buttons, "Document these", lambda: close([t for t in titles if self.row_vars[t].get()]))
            self.rows_ok.pack(side="right")
            self.rows_none = self.ghost(buttons, "None", lambda: close([]))
            self.rows_none.pack(side="right", padx=6)
            for v in self.row_vars.values():
                v.trace_add("write", lambda *_: count())
            win.protocol("WM_DELETE_WINDOW", lambda: close([]))
            self.rows_win = win
            relist()
            count()

        def on_blocked(self, box, why):
            win = self.dialog("AppWalk is waiting", "AppWalk is waiting", WARN)
            Label(win, wraplength=460, justify="left", font=self.f_body, text_color=INK, anchor="w",
                  text=f"{plain_block(why)}\n\nDeal with it on the phone and bring the screen to document back to the front, "
                       "then press Done on the phone.\n\nOr type the code here and AppWalk types it on the phone. The code is not recorded."
                  ).pack(fill="x", padx=20, pady=8)
            code = tk.StringVar()
            self.entry(win, code, width=260, show="•").pack(padx=20, pady=4, anchor="w")

            def close(answer):
                box["answer"] = answer
                win.destroy()
                box["event"].set()
            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=(8, 18))
            self.blocked_done = self.primary(buttons, "Done on the phone", lambda: close(""))
            self.blocked_done.pack(side="left")
            self.blocked_code = code
            self.blocked_type = self.ghost(buttons, "Type this code on the phone", lambda: close(code.get()) if code.get() else None)
            self.blocked_type.pack(side="left", padx=8)
            self.ghost(buttons, "End the run", lambda: close(None)).pack(side="left")
            win.protocol("WM_DELETE_WINDOW", lambda: close(None))
            self.blocked_win = win

        def check_earlier(self):
            d = filedialog.askdirectory(initialdir=self.out.get() or os.path.expanduser("~"))
            if d:
                self.earlier.configure(text=self.verify_text(d)[0])

        def say(self, text):
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")


else:
    App = classic.App


def main():
    if sys.platform == "darwin" and tk.TkVersion >= 9 and not os.environ.get("APPWALK_ALLOW_TK9"):
        sys.exit("This Python has Tk %s. On macOS, windows like this one have crashed with Tk 9.\n"
                 "Run it with a Python that has Tk 8.6, for example: python3.12 appwalk_gui.py\n"
                 "(set APPWALK_ALLOW_TK9=1 to try anyway)" % tk.TkVersion)
    if ctk is None:
        print("CustomTkinter is not installed; opening the plain window. pip install customtkinter for the newer one.")
        return classic.main()
    root = ctk.CTk()
    root.title("AppWalk")
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    classic.smoke_check(root, app, "CustomTkinter")
    root.mainloop()


if __name__ == "__main__":
    main()
