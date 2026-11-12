#!/usr/bin/env python3
"""AppWalk window: the same runs as appwalk.py, without a terminal.

Pick the phone, pick the app, choose what to document, press Start. The walk runs in the background;
the window shows each screenshot as it is taken, asks which rows to open, and asks what to do when a
lock or another app is in front. Nothing is unlocked or bypassed: the examiner deals with it on the
phone, or types the code here for the tool to enter. The code is not recorded.

Run with a Python whose Tk is 8.6. On macOS the python.org 3.14 build ships Tk 9, which has crashed
this kind of window; use python3.12 there.
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appwalk  # noqa: E402


class App:
    def __init__(self, root):
        self.root = root
        root.title("AppWalk")
        root.minsize(760, 560)
        self.events = queue.Queue()     # worker thread -> window
        self.worker = None
        self.stop_flag = threading.Event()
        self.thumb = None
        self.export = None

        pad = dict(padx=8, pady=4)
        form = ttk.Frame(root)
        form.pack(fill="x", **pad)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Phone").grid(row=0, column=0, sticky="w")
        self.device = tk.StringVar()
        self.device_box = ttk.Combobox(form, textvariable=self.device, state="readonly")
        self.device_box.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Refresh", command=self.refresh_devices).grid(row=0, column=2, **pad)

        ttk.Label(form, text="App").grid(row=1, column=0, sticky="w")
        self.package = tk.StringVar()
        ttk.Entry(form, textvariable=self.package).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Use the app in front", command=self.use_front_app).grid(row=1, column=2, **pad)

        ttk.Label(form, text="Document").grid(row=2, column=0, sticky="w")
        self.mode = tk.StringVar(value="screen")
        modes = ttk.Frame(form)
        modes.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(modes, text="The screen that is open (one thread or list)", variable=self.mode, value="screen").pack(anchor="w")
        ttk.Radiobutton(modes, text="Rows I choose from the list that is open (it opens each one)", variable=self.mode, value="list").pack(anchor="w")

        ttk.Label(form, text="Save in").grid(row=3, column=0, sticky="w")
        self.out = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        ttk.Entry(form, textvariable=self.out).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Choose", command=self.choose_folder).grid(row=3, column=2, **pad)

        ttk.Label(form, text="Scenario").grid(row=4, column=0, sticky="w")
        names = ttk.Frame(form)
        names.grid(row=4, column=1, columnspan=2, sticky="ew", **pad)
        self.scenario = tk.StringVar(value="scenario1")
        self.label = tk.StringVar(value="phoneA")
        ttk.Entry(names, textvariable=self.scenario, width=24).pack(side="left")
        ttk.Label(names, text="   Device label").pack(side="left")
        ttk.Entry(names, textvariable=self.label, width=18).pack(side="left", padx=6)

        opts = ttk.Frame(form)
        opts.grid(row=5, column=1, columnspan=2, sticky="w", **pad)
        self.keep_list = tk.BooleanVar(value=True)
        self.wait = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Keep pictures of the list itself (they show a line of every row)", variable=self.keep_list).pack(anchor="w")
        ttk.Checkbutton(opts, text="If a lock or another app is in front, wait for me", variable=self.wait).pack(anchor="w")

        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.start_btn = ttk.Button(bar, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop after this step", command=self.stop_flag.set, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(bar, text="Open transcript", command=self.open_transcript, state="disabled")
        self.open_btn.pack(side="left")
        self.folder_btn = ttk.Button(bar, text="Open folder", command=self.open_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=6)
        self.status = tk.StringVar(value="Open the screen to document on the phone, then press Start.")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=10)

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(body, height=14, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        self.picture = ttk.Label(body, text="The latest screenshot\nshows here", anchor="center", width=30)
        self.picture.pack(side="right", fill="y", padx=(8, 0))

        self.refresh_devices()
        root.after(100, self.poll)

    # ---- small helpers ----

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def serial(self):
        return self.device.get().split()[0] if self.device.get() else ""

    def refresh_devices(self):
        try:
            found = appwalk.list_devices()
        except (OSError, subprocess.SubprocessError) as e:
            self.status.set(f"adb could not be run: {e}")
            return
        ready = [f"{s}   {m}".rstrip() for s, state, m in found if state == "device"]
        other = [f"{s} ({state})" for s, state, m in found if state != "device"]
        self.device_box["values"] = ready
        if ready and self.device.get() not in ready:
            self.device.set(ready[0])
        if not ready:
            self.device.set("")
            self.status.set("No phone ready. " + ("Seen but not ready: " + ", ".join(other) + ". Accept the USB debugging prompt on the phone."
                                                  if other else "Connect it by USB with USB debugging on, then Refresh."))

    def use_front_app(self):
        if not self.serial():
            return self.status.set("Pick a phone first.")
        try:
            pkg = appwalk.app_in_front(self.serial())
        except (OSError, subprocess.SubprocessError) as e:
            return self.status.set(f"Could not ask the phone: {e}")
        if pkg:
            self.package.set(pkg)
            self.status.set(f"The app in front is {pkg}.")
        else:
            self.status.set("Could not tell which app is in front. Type its package name.")

    def choose_folder(self):
        d = filedialog.askdirectory(initialdir=self.out.get() or os.path.expanduser("~"))
        if d:
            self.out.set(d)

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

    # ---- the run ----

    def start(self):
        missing = [n for n, v in (("a phone", self.serial()), ("an app", self.package.get().strip()),
                                  ("a folder", self.out.get().strip()), ("a scenario", self.scenario.get().strip()),
                                  ("a device label", self.label.get().strip())) if not v]
        if missing:
            return self.status.set("Still needed: " + ", ".join(missing) + ".")
        if not os.path.isdir(self.out.get()):
            return self.status.set("The folder to save in does not exist.")
        self.stop_flag.clear()
        self.export = None
        for b in (self.open_btn, self.folder_btn, self.start_btn):
            b.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set("Working. Do not touch the phone unless asked.")
        self.say(f"--- {self.package.get().strip()} on {self.serial()} ---")
        job = dict(serial=self.serial(), package=self.package.get().strip(), out=self.out.get().strip(),
                   scenario=self.scenario.get().strip(), device_label=self.label.get().strip(), mode=self.mode.get(),
                   capture_list=self.keep_list.get(), choose=self.ask_rows, ask=self.ask_blocked if self.wait.get() else None,
                   say=lambda *a: self.events.put(("say", " ".join(str(x) for x in a))),
                   on_screenshot=lambda path, step, item: self.events.put(("shot", path, step, item)),
                   should_stop=self.stop_flag.is_set)
        self.worker = threading.Thread(target=self.work, args=(job,), daemon=True)
        self.worker.start()

    def work(self, job):
        try:
            self.events.put(("done", appwalk.run_job(**job)))
        except Exception as e:  # shown to the examiner, never swallowed
            self.events.put(("failed", f"{type(e).__name__}: {e}"))

    def from_worker(self, kind, *payload):
        """Asks the window something from the worker thread and waits for the answer."""
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
        self.root.after(100, self.poll)

    def on_say(self, text):
        self.say(text)

    def on_shot(self, path, step, item):
        try:
            img = tk.PhotoImage(file=path)
            self.thumb = img.subsample(max(1, img.height() // 420))
            self.picture.configure(image=self.thumb, text="")
        except tk.TclError:
            pass
        self.status.set(f"Screenshot {step}" + (f" · {item}" if item else ""))

    def on_done(self, result):
        self.export = result["export"]
        for line in result["lines"]:
            self.say(line)
        self.say(f"Export: {self.export}")
        self.status.set("Done." if result["ok"] else "Finished with warnings. Read the lines above.")
        self.finish()

    def on_failed(self, message):
        self.say("FAILED: " + message)
        self.status.set("The run failed. Nothing was unlocked or changed by the failure itself.")
        self.finish(False)

    def finish(self, has_export=True):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if has_export and self.export:
            self.folder_btn.configure(state="normal")
            if os.path.exists(os.path.join(self.export, "transcript.html")):
                self.open_btn.configure(state="normal")

    def on_rows(self, box, titles):
        win = tk.Toplevel(self.root)
        win.title("Rows to document")
        win.transient(self.root)
        ttk.Label(win, text=f"{len(titles)} rows found. Pick the ones to open and document.\n"
                                 "Click one, Shift-click for a range, Command- or Ctrl-click to add.").pack(padx=10, pady=(10, 4), anchor="w")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        lb = tk.Listbox(frame, selectmode="extended", height=min(18, max(6, len(titles))), width=50, exportselection=False)
        sb = ttk.Scrollbar(frame, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        for t in titles:
            lb.insert("end", t)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.rows_list = lb   # kept so a test can pick rows the way a person would

        def close(picked):
            box["answer"] = picked
            win.destroy()
            box["event"].set()
        buttons = ttk.Frame(win)
        buttons.pack(pady=10)
        ttk.Button(buttons, text="Select all", command=lambda: lb.selection_set(0, "end")).pack(side="left", padx=4)
        self.rows_ok = ttk.Button(buttons, text="Document these", command=lambda: close([titles[i] for i in lb.curselection()]))
        self.rows_ok.pack(side="left", padx=4)
        ttk.Button(buttons, text="None", command=lambda: close([])).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close([]))

    def on_blocked(self, box, why):
        win = tk.Toplevel(self.root)
        win.title("AppWalk is waiting")
        win.transient(self.root)
        ttk.Label(win, wraplength=420, justify="left",
                  text=f"Stopped: {why}.\n\nDeal with it on the phone and bring the screen to document back to the front. "
                       "Or type the code here and AppWalk types it on the phone. The code is not recorded.").pack(padx=12, pady=(12, 6))
        code = tk.StringVar()
        ttk.Entry(win, textvariable=code, show="•", width=28).pack(pady=4)

        def close(answer):
            box["answer"] = answer
            win.destroy()
            box["event"].set()
        buttons = ttk.Frame(win)
        buttons.pack(pady=10)
        self.blocked_done = ttk.Button(buttons, text="Done on the phone", command=lambda: close(""))
        self.blocked_done.pack(side="left", padx=4)
        ttk.Button(buttons, text="Type this code on the phone", command=lambda: close(code.get()) if code.get() else None).pack(side="left", padx=4)
        ttk.Button(buttons, text="End the run", command=lambda: close(None)).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close(None))


def main():
    if sys.platform == "darwin" and tk.TkVersion >= 9 and not os.environ.get("APPWALK_ALLOW_TK9"):
        sys.exit("This Python has Tk %s. On macOS, windows like this one have crashed with Tk 9.\n"
                 "Run it with a Python that has Tk 8.6, for example: python3.12 appwalk_gui.py\n"
                 "(set APPWALK_ALLOW_TK9=1 to try anyway)" % tk.TkVersion)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
