#!/usr/bin/env python3
"""Drives the AppWalk window the way a person would, against a connected phone or emulator, and saves
pictures of the window. For checking the window end to end; not a unit test.

Usage: python3.12 devtools/drive_gui.py <out folder> <shots folder> [rows to pick, default 2]

It fills the form through the window's own controls (Refresh, Use the app in front, the mode, Start),
picks the first rows in the rows dialog, and waits for the run to end. Pictures are taken with
macOS screencapture, limited to the window's own rectangle.
"""
import os
import subprocess
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import appwalk_gui  # noqa: E402

out, shots = sys.argv[1], sys.argv[2]
n_rows = int(sys.argv[3]) if len(sys.argv) > 3 else 2
os.makedirs(shots, exist_ok=True)
root = tk.Tk()
app = appwalk_gui.App(root)
root.attributes("-topmost", True)
state = {"picked": False, "ticks": 0, "result": "timeout"}


def picture(name, widget=None):
    w = widget or root
    w.update_idletasks()
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty() - 28, w.winfo_width(), w.winfo_height() + 28
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{wd},{ht}", os.path.join(shots, name + ".png")],
                   stdin=subprocess.DEVNULL, capture_output=True)


def begin():
    app.refresh_devices()
    app.use_front_app()
    app.mode.set("list")
    app.out.set(out)
    app.scenario.set("gui_check")
    app.label.set("emuA15")
    picture("1_form")
    app.start_btn.invoke()
    root.after(500, watch)


def watch():
    state["ticks"] += 1
    lb = getattr(app, "rows_list", None)
    if lb is not None and not state["picked"] and lb.winfo_exists():
        lb.selection_set(0, n_rows - 1)
        picture("2_rows", lb.winfo_toplevel())
        state["picked"] = True
        app.rows_ok.invoke()
    if state["picked"] and state["ticks"] % 40 == 0:
        picture("3_running")
    if str(app.start_btn["state"]) == "normal" and state["ticks"] > 4:
        picture("4_done")
        state["result"] = app.status.get()
        root.after(300, root.destroy)
        return
    if state["ticks"] > 1800:   # 15 minutes
        root.destroy()
        return
    root.after(500, watch)


root.after(800, begin)
root.mainloop()
print("status:", state["result"])
print("export:", app.export)
