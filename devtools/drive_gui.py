#!/usr/bin/env python3
"""Drives the AppWalk window through its controls, against a connected emulator, and reports what happened.
For checking the window end to end on each operating system; not a unit test.

Usage: python3 devtools/drive_gui.py <serial> <out folder> [pictures folder]

It walks the four steps the way a person would, by setting the fields and pressing the window's own
buttons, in five scenarios:
  A  rows from the open list, two rows, with Pause and Resume part way
  B  only the open screen
  C  Stop part way through a run
  D  a lock comes to the front: the waiting dialog, and a code typed for the tool to enter (emulator test PIN)
  E  Check this export, and Check an earlier export
  F  the other theme (light or dark), with what was typed kept
  G  the app in front changed after it was chosen: Start asks which one is meant
The emulator must show the chat app's list of conversations when this starts. Pictures of the window
are taken on macOS only (screencapture, limited to the window's rectangle).
"""
import json
import os
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import appwalk  # noqa: E402
import appwalk_gui  # noqa: E402

serial, out = sys.argv[1], sys.argv[2]
shots = sys.argv[3] if len(sys.argv) > 3 else None
PIN = "739126"   # not a number that appears in any seeded text
os.makedirs(out, exist_ok=True)
if shots:
    os.makedirs(shots, exist_ok=True)
appwalk_gui.SETTINGS = os.path.join(out, "settings.json")   # never the real one
appwalk_gui.classic.SETTINGS = appwalk_gui.SETTINGS   # the plain window reads its own copy of the name
DOC = os.environ.get("APPWALK_DOC_PICTURES")   # pictures for the guide: light theme, and no real folder names in them
if DOC:
    with open(appwalk_gui.SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"theme": "light"}, f)
CTK = getattr(appwalk_gui, "ctk", None) and os.environ.get("APPWALK_GUI") != "classic"
root = appwalk_gui.ctk.CTk() if CTK else tk.Tk()
app = (appwalk_gui.App if CTK else appwalk_gui.classic.App)(root)
print("window:", "CustomTkinter" if CTK else "plain Tk", flush=True)


def state(widget):
    return str(widget.cget("state"))
root.attributes("-topmost", True)
root.geometry("+40+60")   # top left, so a picture of the window is never cut by the bottom of the screen
report, deadline = [], [0]


def adb(*args):
    return subprocess.run(["adb", "-s", serial, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=60).stdout.decode("utf-8", "replace")


def run_loop(seconds):
    """macOS draws a new window only while the event loop runs; a sleep is not enough."""
    until = time.time() + seconds
    while time.time() < until:
        root.update()
        time.sleep(0.02)


def lift_for_picture(win):
    """The driver keeps the main window on top of everything, so a dialog is lifted over it for its picture.
    Wait first: a dialog drops its own brief on-top flag 400 ms after it opens."""
    run_loop(0.8)
    win.attributes("-topmost", True)
    win.lift()
    run_loop(0.3)


def picture(name, widget=None):
    if not shots or sys.platform != "darwin":
        return
    w = widget or root
    undo = []
    if DOC:
        # Only what the window shows is changed, for the moment of the picture; nothing that is saved.
        home, shown = os.path.expanduser("~"), "/Users/examiner"
        real = app.out.get()
        app.out.set("/Users/examiner/Cases/2026-001")
        undo.append(lambda: app.out.set(real))
        box = app.result_text
        text = box.get("1.0", "end-1c")
        if out in text or home in text:
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("end", text.replace(out, "/Users/examiner/Cases/2026-001").replace(home, shown))
            box.configure(state="disabled")

            def put_back():
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("end", text)
                box.configure(state="disabled")
            undo.append(put_back)
    if widget is not None and widget is not root:
        lift_for_picture(widget)
        undo.append(lambda: widget.winfo_exists() and widget.attributes("-topmost", False))
    root.update()   # so the picture shows what was just set
    x, y, wd, ht = w.winfo_rootx(), w.winfo_rooty() - 28, w.winfo_width(), w.winfo_height() + 28
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{wd},{ht}", os.path.join(shots, name + ".png")], stdin=subprocess.DEVNULL, capture_output=True)
    for u in undo:
        u()
    root.update()


def note(name, ok, detail=""):
    report.append((name, bool(ok), detail))
    print(("ok   " if ok else "FAIL ") + name + (": " + detail if detail else ""), flush=True)


def rows_dialog_up():
    return getattr(app, "rows_win", None) is not None and app.rows_win.winfo_exists()


def blocked_dialog_up():
    return getattr(app, "blocked_win", None) is not None and app.blocked_win.winfo_exists()


def finished():
    return not app.running and (app.result is not None or "failed" in app.status.get().lower())


def to_conversation_list():
    adb("shell", "input", "keyevent", "3")
    time.sleep(1)
    adb("shell", "am", "start", "-n", "com.google.android.apps.messaging/.ui.ConversationListActivity")
    time.sleep(5)


OTHER_APP = "com.android.settings"


def scenario_g():
    # G: the app in front is changed after step 2 read it; Start asks which one is meant
    app.show(1)
    note("G: step 2 reads the app in front again", app.package.get() == "com.google.android.apps.messaging", app.package.get())
    app.mode.set("screen")
    app.next_btn.invoke()
    adb("shell", "am", "start", "-a", "android.settings.SETTINGS")
    time.sleep(6)
    app.result = None
    app.start_btn.invoke()
    up = getattr(app, "front_win", None) is not None and app.front_win.winfo_exists()
    note("G: Start asks when a different app is in front", up and not app.running)
    if not up and app.running:
        app.stop_btn.invoke()
        yield finished
    if up:
        picture("7b_other_app", app.front_win)
        app.front_cancel.invoke()
        note("G: Cancel starts nothing and keeps the choice", not app.running and app.package.get() == "com.google.android.apps.messaging")
        app.start_btn.invoke()
        app.front_yes.invoke()
        note("G: Yes switches to the app in front and starts", app.running and app.package.get() == OTHER_APP)
        yield finished
        m = app.result["manifest"] if app.result else {}
        note("G: the export names the app that was documented", m.get("apps_under_test", [{}])[0].get("package") == OTHER_APP
             and app.result["summary"].get("screens", 0) >= 2)
    to_conversation_list()
    app.package.set("com.google.android.apps.messaging")



TEACH_FROM = os.environ.get("APPWALK_TEACH_EXPORT")   # an export of the emulator's Google Messages (made-up texts)


def scenario_t():
    """T: teach the app from the Result page, by clicks on a copy of an earlier export, into a test folder."""
    if not TEACH_FROM:
        note("T: teach skipped (APPWALK_TEACH_EXPORT not set)", True)
        return
    import shutil
    import teach
    import transcript
    copy = os.path.join(out, "teach_" + os.path.basename(os.path.normpath(TEACH_FROM)))
    if os.path.exists(copy):
        shutil.rmtree(copy)
    shutil.copytree(TEACH_FROM, copy)
    folder = os.path.join(out, "taught_profiles")   # never the examiner's own ~/.appwalk/profiles
    teach.USER_PROFILES = transcript.USER_PROFILES = folder
    app.export = copy
    if DOC and app.theme != "light":
        app.switch_theme()   # the guide's pictures are all in the light theme
    app.show(3)
    app.teach_btn.invoke()
    dlg = app.teach_win
    yield lambda: dlg.win.winfo_exists()
    pkg = teach.package_of(copy)["package"]

    def find(want):
        # Where a person would click: the middle of a sent bubble, then of a received one.
        for i, (step, png, tree) in enumerate(dlg.shots):
            root = transcript.ET.parse(tree).getroot()
            node, cb = transcript.container(root, pkg)
            for n in (node.iter("node") if node is not None else []):
                b, d = transcript.box(n), n.get("content-desc") or ""
                if transcript.short_id(n.get("resource-id") or "") == "message_text" and b and want(d):
                    return i, ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
        return None, None
    i, xy = find(lambda d: d.startswith("You said"))
    dlg.show_shot(i)
    dlg.pick(*xy)
    i, xy = find(lambda d: " said " in d and not d.startswith("You"))
    dlg.show_shot(i)
    note("T: a click names the view under it", dlg.pick(*xy) is not None and dlg.answers["sent"]["rid"] == "message_text")
    note("T: a click outside the list is refused and uses up no step", dlg.pick(2, 2) is None and dlg.step == 2)
    for _ in range(3):
        dlg.skip()
    picture("12_teach", dlg.win)
    prof = dlg.profile
    note("T: the profile from the clicks", prof.get("message_ids") == ["message_text"]
         and {r["value"]: r["direction"] for r in prof.get("direction_rules", [])} == {"right": "sent", "left": "received"},
         "; ".join(teach.describe(prof)))
    dlg.save_btn.invoke()
    yield lambda: not dlg.win.winfo_exists()
    saved = os.path.join(folder, pkg + ".json")
    rows, notes, _ = transcript.build(copy)
    note("T: saved in the test folder and used for the transcript", os.path.exists(saved) and "taught on this computer" in notes[0])
    note("T: the result page says what was made", "Profile saved" in app.result_text.get("1.0", "end"),
         dlg.result.replace(home_dir(), "~"))


def home_dir():
    return os.path.expanduser("~")


def teach_only():
    note("window opened", True)
    yield from scenario_t()


def quick():
    """Only the first two steps and scenario G, for a short rerun."""
    app.refresh_devices()
    match = [n for n in app.device_box["values"] if n.split()[0] == serial]
    note("phone listed", match)
    if not match:
        return
    app.device.set(match[0])
    app.read_facts()
    app.examiner.set("Test Examiner")
    app.reference.set("GUI-CHECK-G")
    app.label.set("emuA15")
    app.next_btn.invoke()
    app.out.set(out)
    app.next_btn.invoke()
    note("run step reached", app.page == 2 and app.package.get() == "com.google.android.apps.messaging", app.package.get())
    yield from scenario_g()


def rows_only():
    """Steps 1 and 2, then the row picker with two rows ticked, for the guide's picture. Nothing is walked."""
    app.refresh_devices()
    app.device.set([n for n in app.device_box["values"] if n.split()[0] == serial][0])
    app.read_facts()
    app.examiner.set("Test Examiner")
    app.reference.set("GUI-CHECK-1")
    app.label.set("emuA15")
    app.next_btn.invoke()
    app.out.set(out)
    app.mode.set("list")
    app.next_btn.invoke()
    app.start_btn.invoke()
    yield rows_dialog_up
    for t in list(app.row_vars)[1:3]:
        app.row_vars[t].set(True)
    note("ticking rows turns Document these on", state(app.rows_ok) == "normal")
    picture("3_rows", app.rows_win)
    for t in list(app.row_vars):
        app.row_vars[t].set(False)
    (app.rows_none if hasattr(app, "rows_none") else app.rows_ok.master.winfo_children()[-1]).invoke()   # "None": nothing is walked
    yield finished


def script():
    # Step 1
    app.refresh_devices()
    names = list(app.device_box["values"])
    match = [n for n in names if n.split()[0] == serial]
    note("phone listed", match, ", ".join(names))
    if not match:
        return
    app.device.set(match[0])
    app.read_facts()
    note("phone state shown", app.phone_state.cget("text") == "Ready", app.phone_facts.cget("text").replace("\n", " | "))
    app.next_btn.invoke()
    note("a phone label is asked for before going on", app.page == 0 and "label" in app.hint.cget("text"))
    app.examiner.set("Test Examiner")
    app.reference.set("GUI-CHECK-1")
    app.label.set("emuA15")
    app.notes.set("driven check")
    picture("1_phone")
    app.next_btn.invoke()
    # Step 2
    note("app in front filled in by itself", app.page == 1 and app.package.get() == "com.google.android.apps.messaging", app.package.get())
    app.out.set(out)
    app.mode.set("list")
    picture("2_choose")
    app.next_btn.invoke()
    note("run step reached", app.page == 2)

    # A: list, two rows, pause and resume
    app.start_btn.invoke()
    yield rows_dialog_up
    titles = list(app.row_vars)
    note("rows dialog lists the conversations", len(titles) >= 2, f"{len(titles)} rows")
    note("Document these is off until a row is ticked", state(app.rows_ok) == "disabled")
    for t in titles[1:3]:           # two short threads
        app.row_vars[t].set(True)
    app.rows_win.update()
    picture("3_rows", app.rows_win)
    app.rows_ok.configure(state="normal")
    app.rows_ok.invoke()
    yield lambda: app.shots >= 1 and getattr(app, "current_item", None)
    picture("4_running")
    app.pause_btn.invoke()
    at = app.shots
    picture("4b_paused")
    yield lambda: wait_s(14)
    note("Pause holds the run", app.shots <= at + 1 and app.running, f"screenshots {at} -> {app.shots}")
    app.pause_btn.invoke()
    yield finished
    picture("5_result")
    a_export = app.export
    note("A: list run finished and reached the result step", app.page == 3 and app.result and app.result["ok"], app.verdict.cget("text"))
    rows = [json.loads(x) for x in open(os.path.join(a_export, "actions.jsonl"), encoding="utf-8")]
    acts = [r["action"] for r in rows]
    case = next((json.loads(r["detail"]) for r in rows if r["action"] == "case_information"), {})
    note("case details are in the log", case.get("examiner") == "Test Examiner" and case.get("reference") == "GUI-CHECK-1")
    note("phone state logged before and after", acts.count("device_state") == 2)
    note("the pause is in the log", "paused_by_examiner" in acts)
    note("transcript written", os.path.exists(os.path.join(a_export, "transcript.html")) and state(app.open_btn) == "normal")
    note("report written and Open report on", os.path.exists(os.path.join(a_export, "report.pdf"))
         and os.path.exists(os.path.join(a_export, "report.pdf.sha256")) and state(app.report_btn) == "normal"
         and app.result["manifest"].get("report_note", "").startswith("report.pdf written"))
    app.verify_btn.invoke()
    note("E: Check this export", "Checks out" in app.result_text.get("1.0", "end"))
    app.copy_hash()
    note("Copy puts the row hash on the clipboard", root.clipboard_get() == app.result["manifest"]["hash_chain"]["last_row_hash"])
    text, ok = app.verify_text(out)
    note("E: a folder that is not an export is refused", not ok, text[:60])

    # B: only the open screen (the conversation list)
    to_conversation_list()
    app.show(1)
    app.mode.set("screen")
    app.next_btn.invoke()
    app.start_btn.invoke()
    yield finished
    note("B: open-screen run finished", app.result and app.result["ok"] and app.result["summary"].get("screens", 0) >= 2,
         "; ".join(t for _, t in appwalk_gui.plain_summary(app.result))[:140] if app.result else app.status.get())

    # C: stop part way
    to_conversation_list()
    app.show(1)
    app.mode.set("list")
    app.next_btn.invoke()
    app.start_btn.invoke()
    yield rows_dialog_up
    for t in list(app.row_vars)[:6]:
        app.row_vars[t].set(True)
    app.rows_ok.configure(state="normal")
    app.rows_ok.invoke()
    yield lambda: getattr(app, "current_item", None)
    app.stop_btn.invoke()
    yield finished
    s = app.result["summary"] if app.result else {}
    note("C: Stop ends the run early and says so", app.result and not app.result["ok"] and s.get("items_walked", 9) < 6
         and "look at" in app.verdict.cget("text"), f"walked {s.get('items_walked')} of 6; {s.get('stop_reason')}")
    note("C: the stopped export still checks out", app.verify_text(app.export)[1])
    picture("6_stopped")

    # D: a lock in front; the code is typed here and entered by the tool
    to_conversation_list()
    adb("shell", "locksettings", "set-pin", PIN)
    adb("shell", "input", "keyevent", "26")
    time.sleep(2)
    adb("shell", "input", "keyevent", "26")
    time.sleep(2)
    adb("shell", "input", "keyevent", "82")
    time.sleep(2)
    app.show(1)
    app.mode.set("screen")
    app.next_btn.invoke()
    app.start_btn.invoke()
    yield blocked_dialog_up
    picture("7_waiting", app.blocked_win)
    note("D: the waiting dialog comes up at the lock", True)
    app.blocked_code.set(PIN)
    app.blocked_type.invoke()
    yield finished
    adb("shell", "locksettings", "clear", "--old", PIN)
    d_export = app.export
    blob = b"".join(open(os.path.join(dp, f), "rb").read() for dp, _, fs in os.walk(d_export) for f in fs) if d_export else b""
    acts = [json.loads(x)["action"] for x in open(os.path.join(d_export, "actions.jsonl"), encoding="utf-8")] if d_export else []
    note("D: the run went on after the code was typed", app.result and app.result["summary"].get("screens", 0) >= 1 and "secret_entered" in acts)
    note("D: the code is in no file of the export", d_export and PIN.encode() not in blob)
    yield from scenario_g()

    # F: the other theme, with everything typed kept
    was = app.theme
    app.theme_btn.invoke()
    note("F: theme switched and the fields kept", app.theme != was and app.examiner.get() == "Test Examiner"
         and app.page == 3 and "screenshots" in app.result_text.get("1.0", "end"), f"{was} -> {app.theme}")
    picture("8_result_" + app.theme)
    for n, name in ((0, "9_phone_"), (1, "10_choose_"), (2, "11_run_")):
        app.show(n)
        picture(name + app.theme)
    note("F: the choice of theme is remembered", json.load(open(appwalk_gui.SETTINGS, encoding="utf-8")).get("theme") == app.theme)
    saved = json.load(open(appwalk_gui.SETTINGS, encoding="utf-8"))
    note("settings remembered, without the case reference", saved.get("examiner") == "Test Examiner" and "reference" not in saved)
    yield from scenario_t()


started = {"t": 0.0}


def wait_s(seconds):
    if not started["t"]:
        started["t"] = time.time()
    if time.time() - started["t"] >= seconds:
        started["t"] = 0.0
        return True
    return False


gen = {"quick": quick, "rows": rows_only, "teach": teach_only}.get(os.environ.get("APPWALK_DRIVE"), script)()
waiting = [None]


def pump():
    try:
        if waiting[0] is None or waiting[0]():
            deadline[0] = time.time() + 900
            waiting[0] = next(gen)
        elif time.time() > deadline[0]:
            note("timed out waiting", False, app.status.get())
            raise StopIteration
    except StopIteration:
        failed = [n for n, ok, _ in report if not ok]
        print(f"\n{len(report) - len(failed)} of {len(report)} checks passed" + (": FAILED " + "; ".join(failed) if failed else ""), flush=True)
        app.close()
        return
    root.after(300, pump)


deadline[0] = time.time() + 900
root.after(800, pump)
root.mainloop()
sys.exit(0 if report and all(ok for _, ok, _ in report) else 1)
