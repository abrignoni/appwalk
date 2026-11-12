#!/usr/bin/env python3
"""The Teach dialog: the examiner clicks on a screenshot of an export, step by step, and AppWalk writes
the app's profile (see teach.py), then makes the transcript and the report again with it.

Plain Tk, so it opens from either window."""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teach  # noqa: E402
import transcript as T  # noqa: E402

COLOURS = {
    "light": dict(bg="#FFFFFF", soft="#EEF1F5", ink="#1B1F24", muted="#5F6B7A", accent="#2457D6", warn="#A85B00", ok="#17803D"),
    "dark": dict(bg="#1D2128", soft="#2A303A", ink="#E8EBF0", muted="#9AA5B4", accent="#5B8CFF", warn="#F5B041", ok="#4ADE80"),
}


class TeachDialog:
    def __init__(self, root, export, theme="light", on_saved=None):
        self.export, self.on_saved = export, on_saved
        self.app = teach.package_of(export)
        self.package = self.app["package"]
        self.shots = teach.screenshots(export)
        self.answers, self.step, self.shot = {}, 0, 0
        self.profile = self.warnings = None
        c = self.c = COLOURS.get(theme, COLOURS["light"])
        win = self.win = tk.Toplevel(root, bg=c["bg"])
        win.title("Teach AppWalk this app")
        if root.winfo_viewable():   # a dialog tied to a hidden window can stay hidden on macOS
            win.transient(root)
        self.max_h = max(420, min(760, win.winfo_screenheight() - 220))
        left = tk.Frame(win, bg=c["bg"])
        left.pack(side="left", padx=16, pady=16)
        self.canvas = tk.Canvas(left, width=10, height=self.max_h, bg=c["soft"], highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_click)
        nav = tk.Frame(left, bg=c["bg"])
        nav.pack(fill="x", pady=(8, 0))
        self.prev_btn = tk.Button(nav, text="< Earlier screenshot", command=lambda: self.show_shot(self.shot - 1))
        self.prev_btn.pack(side="left")
        self.shot_label = tk.Label(nav, text="", bg=c["bg"], fg=c["muted"])
        self.shot_label.pack(side="left", expand=True)
        self.next_shot_btn = tk.Button(nav, text="Later screenshot >", command=lambda: self.show_shot(self.shot + 1))
        self.next_shot_btn.pack(side="right")

        right = tk.Frame(win, bg=c["bg"], width=380)
        right.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)
        tk.Label(right, text=f"Teach AppWalk {self.package}", font=("TkDefaultFont", 15, "bold"), bg=c["bg"], fg=c["ink"],
                 anchor="w").pack(fill="x")
        tk.Label(right, text="Click on the screenshot where each thing below is. Use the arrows to find a screenshot "
                             "that shows it. Only the views you click are used; nothing is read from what they say.",
                 wraplength=360, justify="left", bg=c["bg"], fg=c["muted"], anchor="w").pack(fill="x", pady=(4, 10))
        self.step_labels = []
        for name, what, _ in teach.STEPS:
            lab = tk.Label(right, text="", wraplength=360, justify="left", bg=c["bg"], fg=c["muted"], anchor="w")
            lab.pack(fill="x", pady=2)
            self.step_labels.append(lab)
        self.found = tk.Label(right, text="", wraplength=360, justify="left", bg=c["bg"], fg=c["warn"], anchor="w")
        self.found.pack(fill="x", pady=(10, 4))
        self.summary = tk.Label(right, text="", wraplength=360, justify="left", bg=c["bg"], fg=c["ink"], anchor="w")
        self.summary.pack(fill="x")
        buttons = tk.Frame(right, bg=c["bg"])
        buttons.pack(side="bottom", fill="x")
        self.back_btn = tk.Button(buttons, text="Back", command=self.back)
        self.back_btn.pack(side="left")
        self.skip_btn = tk.Button(buttons, text="Skip this one", command=self.skip)
        self.skip_btn.pack(side="left", padx=6)
        self.save_btn = tk.Button(buttons, text="Save and make the report again", command=self.save, state="disabled")
        self.save_btn.pack(side="right")
        tk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        self.show_shot(0)
        self.refresh()
        win.lift()
        win.after(50, lambda: win.winfo_exists() and win.focus_force())

    # ---- screenshot ----

    def show_shot(self, i):
        if not self.shots:
            self.shot_label.configure(text="This export has no screenshots.")
            return
        self.shot = max(0, min(i, len(self.shots) - 1))
        step, png, tree = self.shots[self.shot]
        try:
            from PIL import Image, ImageTk
            im = Image.open(png)
            self.scale = self.max_h / im.height
            self.img = ImageTk.PhotoImage(im.resize((max(1, int(im.width * self.scale)), self.max_h)))
            w = int(im.width * self.scale)
        except ImportError:
            img = tk.PhotoImage(file=png)
            factor = max(1, -(-img.height() // self.max_h))
            self.img = img.subsample(factor)
            self.scale, w = 1 / factor, self.img.width()
        self.canvas.configure(width=w, height=int(self.img.height()) if hasattr(self.img, "height") else self.max_h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.img)
        self.shot_label.configure(text=f"S{step}  ·  {self.shot + 1} of {len(self.shots)}")
        self.prev_btn.configure(state="normal" if self.shot > 0 else "disabled")
        self.next_shot_btn.configure(state="normal" if self.shot < len(self.shots) - 1 else "disabled")
        for name, v in self.answers.items():
            if v and v.get("_shot") == self.shot:
                self.mark(v, name)

    def mark(self, view, name):
        b = view["bounds"]
        s = self.scale
        self.canvas.create_rectangle(b[0] * s, b[1] * s, b[2] * s, b[3] * s, outline=self.c["accent"], width=3)
        self.canvas.create_text(b[0] * s + 4, b[1] * s - 2, text=name, anchor="sw", fill=self.c["accent"],
                                font=("TkDefaultFont", 11, "bold"))

    # ---- steps ----

    def refresh(self):
        for i, (name, what, needed) in enumerate(teach.STEPS):
            v = self.answers.get(name, "unset")
            if i == self.step:
                text, colour = f"▶  Click {what}.", self.c["accent"]
            elif v == "unset":
                text, colour = f"   {what[0].upper() + what[1:]}", self.c["muted"]
            elif v is None:
                text, colour = f"–  {name}: skipped", self.c["muted"]
            else:
                where = T.side_of(v["bounds"], v["list_bounds"])
                text, colour = f"✓  {name}: {v['rid'] or 'a view with no name'}, {where}", self.c["ok"]
            self.step_labels[i].configure(text=text, fg=colour)
        done = self.step >= len(teach.STEPS)
        self.skip_btn.configure(state="disabled" if done else "normal")
        self.back_btn.configure(state="normal" if self.step > 0 else "disabled")
        if done:
            self.profile, self.warnings = teach.make_profile(self.export, self.answers)
            lines = teach.describe(self.profile) + [""] + self.warnings
            self.summary.configure(text="This profile will be saved:\n" + "\n".join(lines))
            self.save_btn.configure(state="normal" if self.profile.get("message_ids") or self.profile.get("message_parent_ids") else "disabled")
        else:
            self.summary.configure(text="")
            self.save_btn.configure(state="disabled")

    def on_click(self, event):
        if self.step >= len(teach.STEPS) or not self.shots:
            return
        self.pick(int(event.x / self.scale), int(event.y / self.scale))

    def pick(self, x, y):
        """A click at (x, y) in screen pixels of the shown screenshot."""
        view = teach.view_at(self.shots[self.shot][2], self.package, x, y)
        if view is None:
            self.found.configure(text="Nothing the app labelled is there. Click on the text or the picture itself.")
            return None
        view["_shot"] = self.shot
        name = teach.STEPS[self.step][0]
        self.answers[name] = view
        self.found.configure(text="")
        self.step += 1
        self.show_shot(self.shot)
        self.refresh()
        return view

    def skip(self):
        if self.step < len(teach.STEPS):
            self.answers[teach.STEPS[self.step][0]] = None
            self.step += 1
            self.found.configure(text="")
            self.refresh()

    def back(self):
        if self.step > 0:
            self.step -= 1
            self.answers.pop(teach.STEPS[self.step][0], None)
            self.show_shot(self.shot)
            self.refresh()

    def save(self):
        path = teach.save(self.profile)
        notes = []
        try:
            _, notes = T.write(self.export)
        except Exception as e:  # noqa: BLE001 - say what failed; the profile is saved either way
            notes = [f"transcript not written: {type(e).__name__}: {e}"]
        report = "report.pdf made again"
        try:
            import report_pdf
            report_pdf.build(self.export)
        except Exception as e:  # noqa: BLE001
            report = f"report.pdf not made again: {type(e).__name__}: {e}"
        rows, _, _ = T.build(self.export)
        msgs = [r for r in rows if r["role"] == "message"]
        counts = {d: sum(1 for r in msgs if r["direction"] == d) for d in ("sent", "received")}
        self.result = (f"Profile saved in {path}. Transcript and {report}: {len(msgs)} messages, "
                       f"{counts['sent']} sent, {counts['received']} received.")
        self.win.destroy()
        if self.on_saved:
            self.on_saved(self.result)
        return self.result
