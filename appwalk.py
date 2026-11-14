#!/usr/bin/env python3
"""AppWalk: document an Android app's content with screenshots, over adb.

walk: the examiner opens the screen to document (a message thread, a list). The tool then repeats:
read the view tree, take a screenshot, read the view tree again, scroll. It stops when the screen
no longer changes, at a cap, or when something other than the chosen app is in front.

walk-list: the examiner opens a list and chooses rows. The tool opens each chosen row, walks what it
opens the same way, and presses Back for the next.

Everything it does is written to a hash-chained log in the known-data logger (KDL) export format,
with each screenshot's hash inside the chain.

It uses programs already on the phone (uiautomator, screencap, input) and installs nothing.
It does not bypass any lock: when a lock or password prompt is in front it stops, or waits for the
examiner to deal with it.

Tested on an Android 15 emulator and on a Pixel 7a with Android 17; see the README.
"""
import argparse
import csv
import getpass
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

VERSION = "0.1.3"
# transcript.py sits beside this file. Python started with -I does not look there by itself.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 3  # the KDL export schema this writes

# The row fields KDL hashes, in the form its app and make_documentation.py use (Model.kt, Chain).
CHAIN_FIELDS = [
    "account", "action", "app_package", "app_version", "boot_count", "content", "conversation_id", "counterpart",
    "detail", "device_label", "direction", "edited_utc_ms", "elapsed_ms", "entry_id", "expected_artifacts",
    "gnss_time_ms", "kind", "network_time_ms", "prev_hash", "profile_id", "refers_to", "revision_n", "reviewed",
    "scenario_id", "seq", "source", "time_quality", "tz_id", "tz_offset_min", "utc_ms",
]
LIST_CLASSES = ("RecyclerView", "ListView", "ScrollView", "GridView", "ViewPager")
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def clean(s):
    """Unpaired UTF-16 surrogates become U+FFFD, as KDL stores text, so every verifier reads the same bytes."""
    if not isinstance(s, str):
        return s
    return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def chain_canonical(r):
    d = {k: r.get(k) for k in CHAIN_FIELDS}
    w = r.get("window_s")
    d["window_ms"] = None if w is None else int(math.floor(w * 1000 + 0.5))
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def verify_chain(rows):
    """Returns (ok, message, first_bad_seq)."""
    prev = ""
    for r in sorted(rows, key=lambda x: x["seq"]):
        if r.get("prev_hash") != prev:
            return False, f"BROKEN at seq {r['seq']}: previous-hash link does not match", r["seq"]
        if sha256(chain_canonical(r).encode("ascii")) != r.get("row_hash"):
            return False, f"BROKEN at seq {r['seq']}: row content does not match its hash", r["seq"]
        prev = r["row_hash"]
    return True, f"intact, {len(rows)} rows, last hash {prev}", None


# ---- The phone, through adb ----


def find_adb():
    """The adb to run. A program opened from the Finder or the Start menu does not get the PATH a terminal
    has, so after the PATH (and APPWALK_ADB, if set) the places adb is usually installed are tried:
    Homebrew, the Android SDK of Android Studio, ANDROID_HOME and ANDROID_SDK_ROOT. "adb" when none is
    found, so the error says adb could not be run."""
    import shutil
    if os.environ.get("APPWALK_ADB"):
        return os.environ["APPWALK_ADB"]
    found = shutil.which("adb")
    if found:
        return found
    name = "adb.exe" if os.name == "nt" else "adb"
    home = os.path.expanduser("~")
    places = [os.path.join(os.environ.get(v, ""), "platform-tools", name) for v in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(v)]
    places += ["/opt/homebrew/bin/adb", "/usr/local/bin/adb",
               os.path.join(home, "Library", "Android", "sdk", "platform-tools", name),
               os.path.join(home, "Android", "Sdk", "platform-tools", name),
               os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", name),
               "/usr/bin/adb"]
    for p in places:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "adb"

class AdbDevice:
    def __init__(self, serial, adb=None):
        self.serial, self.adb_path = serial, adb or find_adb()

    def adb(self, *args, timeout=60, stdin=None):
        p = subprocess.run([self.adb_path, "-s", self.serial, *args], capture_output=True, timeout=timeout,
                           **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}))
        if p.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args[:3])} failed ({p.returncode}): {p.stderr.decode('utf-8', 'replace')[:300]}")
        return p.stdout

    def shell(self, cmd, timeout=60):
        return self.adb("shell", cmd, timeout=timeout).decode("utf-8", "replace")

    def stamp(self):
        """The phone's own clock, read in one shell call. elapsed_ms is the first field of /proc/uptime."""
        out = self.shell("date +%s%N; cat /proc/uptime; date +%z; getprop persist.sys.timezone; settings get global boot_count")
        ns, up, off, tz, boot = [x.strip() for x in out.strip().splitlines()[:5]]
        sign = -1 if off.startswith("-") else 1
        return {"utc_ms": int(ns) // 1_000_000, "elapsed_ms": int(float(up.split()[0]) * 1000),
                "tz_id": tz, "tz_offset_min": sign * (int(off[1:3]) * 60 + int(off[3:5])),
                "boot_count": int(boot) if boot.lstrip("-").isdigit() else -1}

    def tree_raw(self):
        """The view tree of the window in front, written to the terminal so no file is left on the phone."""
        # uiautomator reads the tree only once the app is idle. An app animating something, such as the
        # spinner Instagram shows at the top of a conversation while it tries to load older messages (and
        # cannot, offline), is not idle until the animation stops: "ERROR: could not get idle state." A short
        # animation ends, so the read is tried again for 45 s. Instagram's offline spinner on a Pixel 7a
        # (13 November 2026) outlasted two minutes; the walk handles that case by stepping back from it.
        deadline = time.monotonic() + 45
        while True:
            raw = self.adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=90)
            if b"could not get idle state" not in raw[:200] or time.monotonic() > deadline:
                break
            time.sleep(3)
        end = raw.rfind(b"</hierarchy>")
        if end < 0:
            raise RuntimeError("uiautomator returned no view tree: " + raw[:200].decode("utf-8", "replace"))
        return raw[:end + len(b"</hierarchy>")]

    def screenshot(self):
        return self.adb("exec-out", "screencap", "-p", timeout=60)

    def swipe(self, x1, y1, x2, y2, ms):
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")

    def tap(self, x, y):
        self.shell(f"input tap {x} {y}")

    def back(self):
        self.shell("input keyevent 4")

    def type_secret(self, secret):
        """Types what the examiner supplied and presses Enter. The text goes to adb on stdin, not on its
        command line, and is never written to the log."""
        quoted = "'" + secret.replace("'", "'\\''").replace(" ", "%s") + "'"
        self.adb("shell", stdin=f"input text {quoted}\ninput keyevent 66\n".encode("utf-8"))

    def profile(self):
        g = lambda k: self.shell(f"getprop {k}").strip() or None
        return {"manufacturer": g("ro.product.manufacturer"), "model": g("ro.product.model"), "device": g("ro.product.device"),
                "fingerprint": g("ro.build.fingerprint"), "release": g("ro.build.version.release"),
                "incremental": g("ro.build.version.incremental"), "sdk_int": int(g("ro.build.version.sdk") or 0),
                "security_patch": g("ro.build.version.security_patch"), "kernel": self.shell("uname -r").strip() or None,
                "baseband": g("gsm.version.baseband"), "carrier": g("gsm.operator.alpha"), "notes": None}

    def app_version(self, package):
        m = re.search(r"versionName=(\S+)", self.shell(f"dumpsys package {package}"))
        return m.group(1) if m else None


# ---- View tree ----

class Tree:
    """A parsed view tree. Text of password fields is removed before anything is stored or hashed."""

    def __init__(self, raw):
        root = ET.fromstring(raw)
        self.secret_fields = 0
        for n in root.iter("node"):
            if n.get("password") == "true":
                self.secret_fields += 1
                n.set("text", "")
        self.root = root
        self.xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        first = root.find("node")
        self.front_package = first.get("package") if first is not None else None

    def nodes(self, package=None):
        return [n for n in self.root.iter("node") if package is None or n.get("package") == package]

    def fingerprint(self, package):
        """What the chosen app shows, position included. Equal fingerprints mean nothing moved."""
        parts = [(n.get("class"), n.get("resource-id"), n.get("text"), n.get("content-desc"), n.get("bounds"),
                  n.get("checked"), n.get("selected")) for n in self.nodes(package)]
        return sha256(json.dumps(parts, ensure_ascii=True).encode("ascii"))

    def labels(self, package):
        """Items inside the scrolling list that a reader can recognise in two frames: (resource id, text,
        description) with some text. Toolbars and input bars outside the list are left out, because they
        are on every frame and would hide a gap."""
        box = self.scroll_container(package)
        inside = box["node"].iter("node") if box else self.nodes(package)
        return {(n.get("resource-id"), n.get("text"), n.get("content-desc")) for n in inside
                if n.get("package") == package and (n.get("text") or n.get("content-desc"))}

    def scroll_container(self, package):
        """The largest scrolling node of the app; failing that the largest list-like node; failing that the window."""
        def area(n):
            m = BOUNDS.match(n.get("bounds") or "")
            if not m:
                return 0, None
            l, t, r, b = map(int, m.groups())
            return max(0, r - l) * max(0, b - t), (l, t, r, b)
        best, how = None, None
        for rule, test in (("scrollable", lambda n: n.get("scrollable") == "true"),
                           ("list class", lambda n: (n.get("class") or "").endswith(LIST_CLASSES)),
                           ("window", lambda n: True)):
            cands = [(area(n), n) for n in self.nodes(package) if test(n)]
            cands = [(a, n) for a, n in cands if a[0] > 0]
            if cands:
                (_, box), node = max(cands, key=lambda c: c[0][0])
                best, how = (box, node), rule
                break
        if best is None:
            return None
        box, node = best
        return {"bounds": box, "rule": how, "resource_id": node.get("resource-id"), "class": node.get("class"), "node": node}


def tree_rows(tree, package):
    """Rows of the scrolling list: its direct children that carry some text. The title is the first text."""
    box = tree.scroll_container(package)
    if box is None:
        return []
    cl, ct, cr, cb = box["bounds"]
    out = []
    for child in box["node"]:
        if child.get("package") != package:
            continue
        texts = [n for n in child.iter("node") if n.get("text")]
        m = BOUNDS.match(child.get("bounds") or "")
        if not texts or not m:
            continue
        l, t, r, b = map(int, m.groups())
        tm = BOUNDS.match(texts[0].get("bounds") or "")
        tl, tt, tr, tb = map(int, tm.groups()) if tm else (l, t, r, b)
        out.append({"title": texts[0].get("text"), "texts": [n.get("text") for n in texts], "bounds": [l, t, r, b],
                    "title_id": texts[0].get("resource-id") or "",
                    "fully_visible": t >= ct and b <= cb and (b - t) >= 40, "tap": ((tl + tr) // 2, (tt + tb) // 2)})
    # A row cut off by the edge of the list loses its first lines, and its next line (a preview of the
    # last message, say) would be taken for its title. Seen with Fossify Messages. So a row counts only
    # when its first text carries the same view id as most rows' first text.
    ids = [r["title_id"] for r in out if r["title_id"]]
    usual = max(set(ids), key=ids.count) if len(ids) >= 2 else None
    for r in out:
        r["whole"] = usual is None or r["title_id"] == usual
    return out


Tree.rows = tree_rows


def pixels_available():
    try:
        import PIL.Image  # noqa: F401
        return True
    except ImportError:
        return False


def same_area(png_a, png_b, bounds):
    """True when two screenshots are the same inside bounds (the list), or everywhere when bounds is None.
    The status bar is left out on purpose: its clock and icons change while nothing in the app moves."""
    from PIL import Image, ImageChops
    a = Image.open(io.BytesIO(png_a)).convert("RGB")
    b = Image.open(io.BytesIO(png_b)).convert("RGB")
    if a.size != b.size:
        return False
    if bounds:
        a, b = a.crop(tuple(bounds)), b.crop(tuple(bounds))
    # A scrollbar fades out about 2 s after a scroll (measured in Signal on a Pixel 7a) and would make every
    # step look unsettled. The thin strip at the right edge where it sits is left out of the comparison.
    edge = max(4, a.width * 15 // 1000)
    a, b = a.crop((0, 0, a.width - edge, a.height)), b.crop((0, 0, b.width - edge, b.height))
    return ImageChops.difference(a, b).getbbox() is None


def is_blank(png):
    """True when every sampled pixel is near black (a protected window or an empty frame), None when
    Pillow is missing and the frame could not be looked at."""
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    for y in range(0, h, max(1, h // 48)):
        for x in range(0, w, max(1, w // 24)):
            if max(im.getpixel((x, y))) > 8:
                return False
    return True


# ---- The log ----

class Log:
    def __init__(self, out_dir, scenario_id, device_label, device, host_clock=time.time):
        self.out, self.scenario_id, self.device_label, self.device = out_dir, scenario_id, device_label, device
        self.host_clock = host_clock
        self.rows, self.shots, self.trees = [], [], []
        os.makedirs(os.path.join(out_dir, "screens"))
        os.makedirs(os.path.join(out_dir, "trees"))
        self.fh = open(os.path.join(out_dir, "actions.jsonl"), "ab")

    def add(self, kind, action, detail=None, stamp=None, **f):
        """Appends one row, stamped with the phone's clock, linked to the row before it."""
        s = stamp or self.device.stamp()
        d = dict(detail or {})
        d["host_utc_ms"] = int(self.host_clock() * 1000)
        r = {
            "schema": SCHEMA_VERSION, "seq": len(self.rows) + 1, "entry_id": f.get("entry_id") or uuid.uuid4().hex, "revision_n": 0,
            "kind": kind, "scenario_id": self.scenario_id, "device_label": self.device_label, "profile_id": "p1",
            "utc_ms": s["utc_ms"], "tz_id": s["tz_id"], "tz_offset_min": s["tz_offset_min"],
            "elapsed_ms": s["elapsed_ms"], "boot_count": s["boot_count"],
            "boot_wall_ms": None if s["elapsed_ms"] is None else s["utc_ms"] - s["elapsed_ms"],
            "network_time_ms": None, "gnss_time_ms": None, "time_quality": s.get("time_quality", "exact"), "window_s": None,
            "edited_utc_ms": s["utc_ms"], "app_package": f.get("app_package"), "app_version": f.get("app_version"),
            "account": None, "action": action, "content": clean(f.get("content")), "counterpart": None,
            "direction": None, "conversation_id": f.get("conversation_id"), "refers_to": f.get("refers_to"),
            "expected_artifacts": [], "detail": clean(json.dumps(d, sort_keys=True, ensure_ascii=True)),
            "source": "system" if kind == "system_event" else "auto_event", "reviewed": True,
            "prev_hash": self.rows[-1]["row_hash"] if self.rows else "",
        }
        r["row_hash"] = sha256(chain_canonical(r).encode("ascii"))
        r["screenshot"] = f.get("screenshot")
        self.rows.append(r)
        self.fh.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
        self.fh.flush()
        os.fsync(self.fh.fileno())
        return r

    def close(self):
        self.fh.close()


# ---- The walk ----

class Walker:
    def __init__(self, device, log, package, *, reveal="above", fraction=0.70, swipe_ms=1200, max_screens=200,
                 stable_stops=2, settle_s=0.6, on_blocked="stop", ask=None, sleep=time.sleep, say=print,
                 rewind=True, max_rewind_scrolls=400, stop_at=None):
        self.d, self.log, self.package = device, log, package
        # Text that ends the walk once a screenshot shows it, for a long conversation of which only the newest
        # part is wanted (a known-data chat started inside an old thread, for example).
        self.stop_at = stop_at
        self.reveal, self.fraction, self.swipe_ms = reveal, fraction, swipe_ms
        self.max_screens, self.stable_stops, self.settle_s = max_screens, stable_stops, settle_s
        self.on_blocked, self.ask, self.sleep, self.say = on_blocked, ask, sleep, say
        self.rewind, self.max_rewind_scrolls = rewind, max_rewind_scrolls
        self.app_version = device.app_version(package)
        self.should_stop = lambda: False   # the window's Stop button; checked between steps
        self.pause_gate = lambda: 0        # the window's Pause button: blocks while paused, returns seconds waited
        self.step = 0      # screenshots taken in this export, across every walk
        self.item = None   # what is being walked, when the tool opened it from a list
        self.walks = []    # one summary per walk
        self.summary = self.new_summary()

    def new_summary(self):
        return {"item": self.item, "screens": 0, "scrolls": 0, "rewind_scrolls": 0, "rewind_end_confirmed": None,
                "end_confirmed": None, "gaps": 0, "gaps_retried": 0, "unsettled": 0, "blank": 0, "pauses": 0, "stop_reason": None}

    def add(self, kind, action, detail=None, **f):
        """A log row carrying the app and, when the tool opened it from a list, the item being walked."""
        return self.log.add(kind, action, detail, app_package=self.package, app_version=self.app_version,
                            conversation_id=self.item, **f)

    def in_front(self, tree):
        return tree.front_package == self.package and not tree.secret_fields

    def settled_capture(self, capture=True):
        """A screenshot and the view tree of the same moment; up to three tries. With Pillow: screenshot,
        tree, screenshot, and the moment is settled when the list's area is the same in both pictures (one
        tree read instead of two: on a Pixel 7a a tree read took about 2.3 s and a screenshot 0.3 s).
        Without Pillow: tree, screenshot, tree, settled when the two trees agree. With capture off, one tree
        and no screenshot."""
        if capture and pixels_available():
            return self.settled_capture_by_pixels()
        for attempt in range(1, 4):
            before = Tree(self.d.tree_raw())
            if not self.in_front(before):
                return before, None, None, attempt
            if not capture:
                return before, b"", None, attempt
            stamp = self.d.stamp()
            png = self.d.screenshot()
            after = Tree(self.d.tree_raw())
            if not self.in_front(after):
                return after, None, None, attempt
            if before.fingerprint(self.package) == after.fingerprint(self.package):
                return after, png, stamp, attempt
            self.sleep(self.settle_s)
        self.summary["unsettled"] += 1
        return after, png, stamp, -1

    def settled_capture_by_pixels(self):
        after = png = stamp = None
        for attempt in range(1, 4):
            stamp = self.d.stamp()
            png = self.d.screenshot()
            # A list can still be gliding after the swipe. Screenshots are cheap and the tree read is not
            # (on a Pixel 7a 0.3 s against 2.3 s), so wait until two quick screenshots in a row agree with
            # the one before. One agreement was not enough in Signal: its floating date label changed about
            # 0.4 s after the list stopped gliding.
            agreed = 0
            for _ in range(8):
                quick_stamp = self.d.stamp()
                quick = self.d.screenshot()
                if same_area(png, quick, getattr(self, "last_list_bounds", None)):
                    agreed += 1
                    if agreed == 2:
                        break
                else:
                    agreed = 0
                    stamp, png = quick_stamp, quick
            after = Tree(self.d.tree_raw())
            if not self.in_front(after):
                return after, None, None, attempt
            again = self.d.screenshot()
            box = after.scroll_container(self.package)
            self.last_list_bounds = box["bounds"] if box else None
            if same_area(png, again, box["bounds"] if box else None):
                return after, png, stamp, attempt
            self.sleep(self.settle_s)
        self.summary["unsettled"] += 1
        return after, png, stamp, -1

    def blocked(self, tree):
        """Something other than the chosen screen is in front. Never worked around: stop, or wait for the examiner."""
        why = ("a password field is on screen" if tree.secret_fields and tree.front_package == self.package
               else f"{tree.front_package} is in front, not {self.package}")
        self.add("system_event", "walk_blocked", {"reason": why, "front_package": tree.front_package,
                                                  "password_fields": tree.secret_fields})
        if self.on_blocked == "stop" or self.ask is None:
            return False
        self.summary["pauses"] += 1
        answer = self.ask(why)
        if answer is None:
            self.add("system_event", "walk_ended_by_examiner", {})
            return False
        if answer:
            self.d.type_secret(answer)
            self.add("system_event", "secret_entered", {"by": "examiner, typed at the workstation", "value": "not recorded"})
            self.sleep(2.0)
        else:
            self.add("system_event", "walk_resumed", {"by": "examiner, handled on the phone"})
        return True

    def run(self, item=None, reveal=None, capture=True, on_tree=None, rewind=None):
        """Walks the screen in front from one end to the other. `item` names it when the tool opened it from
        a list. With capture off nothing is saved: the trees are only handed to `on_tree` (used to read a
        list's rows without keeping pictures of it)."""
        try:
            return self._run(item, reveal, capture, on_tree, rewind)
        except RuntimeError as e:
            # The phone stopped answering as expected (no view tree, no screenshot). The walk ends here with
            # what it has, and says why, so the export is still written and checkable.
            self.summary["stop_reason"] = f"stopped: the phone did not answer as expected ({e}); the end was not reached"
            self.add("system_event", "walk_error", {"error": str(e)[:300]})
            return self.finish()

    def _run(self, item, reveal, capture, on_tree, rewind):
        self.item = item
        if reveal:
            self.reveal = reveal
        s = self.summary = self.new_summary()
        do_rewind = self.rewind if rewind is None else rewind
        self.add("system_event", "walk_start", {
            "tool": f"AppWalk {VERSION}", "package": self.package, "app_version": self.app_version, "item": item,
            "capture": capture, "reveal": self.reveal, "scroll_fraction": self.fraction, "swipe_ms": self.swipe_ms,
            "max_screens": self.max_screens, "stable_stops": self.stable_stops, "on_blocked": self.on_blocked,
            "stop_at": self.stop_at,
            "rewind": do_rewind})
        if do_rewind and not self.go_to_far_end():
            return self.finish()
        prev_fp, prev_labels, prev_png, unchanged, fraction = None, None, None, 0, self.fraction
        prev_lines = None
        prev_tree = None
        while True:
            waited = self.pause_gate()
            if waited:
                self.add("system_event", "paused_by_examiner", {"seconds": round(waited, 1)})
            if self.should_stop():
                s["stop_reason"] = "stopped by the examiner; the end was not reached"
                break
            busy_end = False
            try:
                tree, png, stamp, attempt = self.settled_capture(capture)
            except RuntimeError as e:
                # An app that keeps animating at this end of the list (Instagram's spinner, trying to load older
                # messages it cannot get offline) never lets uiautomator read the tree. Step back a little, so
                # the spinner is off the screen and still, and take that screen as the last one.
                if "idle state" not in str(e) or prev_tree is None:
                    raise
                self.add("system_event", "app_busy_at_end", {"after_screen": s["screens"], "error": str(e)[:200]})
                # The smallest step that lets the tree be read, so as little as possible of the oldest content
                # goes off the screen: 5% of the list, then 10% more, then 20% more. (Instagram, Pixel 7a: three
                # steps of 5% were not enough; about 500 px, a quarter of the list, was.)
                for n, step in enumerate((0.05, 0.10, 0.20)):
                    self.scroll(prev_tree, step, reverse=True)
                    self.sleep(self.settle_s)
                    try:
                        tree, png, stamp, attempt = self.settled_capture(capture)
                        break
                    except RuntimeError as again:
                        if "idle state" not in str(again) or n == 2:
                            raise
                busy_end = True
            if png is None:
                if self.blocked(tree):
                    continue
                s["stop_reason"] = "blocked: " + ("password field on screen" if tree.secret_fields and tree.front_package == self.package
                                                   else f"{tree.front_package} in front")
                break
            s["screens"] += 1
            fp = tree.fingerprint(self.package)
            labels = tree.labels(self.package)
            same = prev_fp is not None and fp == prev_fp and not self.moved_in_pictures(tree, png, prev_png)
            overlap = None if prev_labels is None else len(labels & prev_labels)
            import transcript
            lines = transcript.frame_lines(tree.root, self.package, None)
            joined = None if prev_labels is None or same else self.joined(tree, lines, prev_lines, png, prev_png)
            if on_tree:
                on_tree(tree)
            if capture:
                self.save(tree, png, stamp, attempt, fp, same, overlap, len(labels), joined)
            prev_tree = tree
            if self.stop_at and any(self.stop_at in (n.get("text") or "") for n in tree.nodes(self.package)):
                s["end_confirmed"] = False
                s["stop_reason"] = (f'stopped where the examiner asked: a screenshot shows "{self.stop_at}"; the list '
                                    "goes on past it and was not documented further")
                break
            if busy_end:
                s["end_confirmed"] = False
                s["stop_reason"] = ("the app kept showing a loading indicator at this end of the list, so its view tree "
                                    "could not be read there; the walk stopped just short of it. The app may hold more "
                                    "it could not load (for example offline); the last screenshot shows that end")
                break

            if same:
                unchanged += 1
                if unchanged >= self.stable_stops:
                    s["end_confirmed"], why = self.confirm_end(tree, reverse=False)
                    s["stop_reason"] = f"no change after {unchanged} scrolls in a row; {why}"
                    break
            else:
                unchanged = 0
                if joined is not None and not joined["joined"]:
                    # Nothing shows that this frame continues the one before: content between them may be missing.
                    retry = fraction > 0.08
                    self.add("system_event", "continuity_gap", {"after_screen": s["screens"] - 1, "before_screen": s["screens"],
                                                                "going_back_to_retry": retry, "checked_by": joined["by"]})
                    if retry:
                        # Go back by the distance just scrolled and come forward again in smaller moves. The
                        # frame before the gap stays the one to compare with, so the retry has to join it; a
                        # retry that still does not join is a gap. (In 0.1.0 the comparison was dropped
                        # here and the gap was counted even when the retry closed it.)
                        s["gaps_retried"] += 1
                        self.scroll(tree, fraction, reverse=True)
                        fraction = max(0.08, fraction / 2)
                        self.sleep(self.settle_s)
                        continue
                    s["gaps"] += 1
            if s["screens"] >= self.max_screens:
                s["stop_reason"] = f"cap of {self.max_screens} screens reached; the end was not confirmed"
                break
            prev_fp, prev_labels, prev_png, prev_lines = fp, labels, png, lines
            if not self.scroll(tree, fraction):
                s["stop_reason"] = "nothing to scroll in this window"
                break
            self.sleep(self.settle_s)
        return self.finish()

    def moved_in_pictures(self, tree, png, prev_png):
        """True when the view tree is unchanged but the screenshots show the list clearly shifted. A list of
        items that all look alike to the tree ("Photo", the same id) gives the same tree after a scroll of
        whole items, and the walk would stop early, calling the end reached. A clear shift is asked for, not
        just any difference, so an animation at the end of a list does not keep the walk going."""
        if not (png and prev_png and pixels_available()):
            return False
        box = tree.scroll_container(self.package)
        bounds = box["bounds"] if box else None
        if same_area(prev_png, png, bounds):
            return False
        import transcript
        return bool(transcript.picture_overlap(prev_png, png, bounds, self.reveal)) if bounds else False

    def joined(self, tree, lines, prev_lines, png, prev_png):
        """Whether this frame continues the one before, and on what evidence.

        Text joins two frames when two lines or more moved together by the same distance and the rest of
        what that distance keeps on the list agrees (transcript.text_shift and text_joins). One shared line
        is not enough: a title that stays put, an author's name in a group or a voice note's "0:07" can be
        on two frames that share nothing (reported by a tester on Samsung and Motorola phones, 14 November 2026). A shared
        description never counted: "Photo" or a delivery tick repeats on many bubbles. Short of that the
        two screenshots are compared (transcript.picture_overlap). Without Pillow, or with no screenshots
        kept, one line that moved the right way is taken, and the record says the pictures were not compared."""
        import transcript
        box = tree.scroll_container(self.package)
        bounds = box["bounds"] if box else None
        ts = transcript.text_shift(prev_lines or [], lines, bounds, self.reveal)
        seen = {"text_shared": ts["agree"], "text_not_found": ts["disagree"], "fixed_lines": ts["fixed"], "shift_px": ts["shift"]}
        if transcript.text_joins(ts):
            return {"joined": True, "by": "text", **seen}
        if png and prev_png and pixels_available():
            px = transcript.picture_overlap(prev_png, png, bounds, self.reveal) if bounds else None
            return {"joined": bool(px), "by": "screenshots", "overlap_px": px, **seen}
        if not (lines and prev_lines):
            return {"joined": True, "by": "nothing to compare", **seen}
        return {"joined": ts["agree"] >= 1, "by": "one line of text or more (no screenshots to compare)", **seen}

    def finish(self):
        s = self.summary
        self.add("system_event", "walk_stop", dict(s))
        self.walks.append(dict(s))
        return s

    def save(self, tree, png, stamp, attempt, fp, same, overlap, n_labels, joined=None):
        """Writes one screenshot and its view tree, and the chained row that holds both hashes."""
        s = self.summary
        self.step += 1
        step = self.step
        blank = is_blank(png)
        status = "blank" if blank else ("ok" if blank is False else "ok_not_inspected")
        s["blank"] += 1 if blank else 0
        entry = uuid.uuid4().hex
        # The row id names the files, so file, row and hash can each be found from the other two.
        png_name, tree_name = f"{step:04d}_{entry}.png", f"{step:04d}_{entry}.xml"
        with open(os.path.join(self.log.out, "screens", png_name), "wb") as f:
            f.write(png)
        with open(os.path.join(self.log.out, "trees", tree_name), "wb") as f:
            f.write(tree.xml)
        detail = {"step": step, "item": self.item, "screen_in_this_walk": s["screens"],
                  "png_file": png_name, "png_sha256": sha256(png), "png_status": status,
                  "tree_file": tree_name, "tree_sha256": sha256(tree.xml), "tree_fingerprint": fp,
                  "tree_same_before_and_after_screenshot": attempt != -1, "capture_attempts": abs(attempt),
                  "same_as_previous_step": same, "labels_shared_with_previous_step": overlap,
                  "continuity_with_previous_step": joined,
                  "password_fields_removed": tree.secret_fields}
        row = self.add("auto_event", "screenshot", detail, stamp=stamp, entry_id=entry,
                       screenshot={"file": png_name, "sha256": detail["png_sha256"], "status": status})
        self.log.shots.append({"entry_id": entry, "file": png_name, "status": status, "item": self.item,
                               "sha256_at_capture": detail["png_sha256"], "utc_ms": row["utc_ms"], "row_seq": row["seq"]})
        self.log.trees.append({"entry_id": entry, "file": tree_name, "item": self.item, "step": step,
                               "sha256_at_capture": detail["tree_sha256"], "row_seq": row["seq"]})
        self.say(f"step {step}" + (f" [{self.item}]" if self.item else "") + f": {status}, {n_labels} labelled items"
                 + (", same as previous" if same else ""))

    def go_to_far_end(self):
        """An app can reopen a list wherever it was left. Before the walk, scroll the other way until the
        screen stops changing, so the walk starts at one end and covers the list whatever its position was.
        No screenshots are taken here; the walk passes over the same screens."""
        s = self.summary
        prev, unchanged, rewind_note = None, 0, None
        while True:
            if self.should_stop():
                s["stop_reason"] = "stopped by the examiner before the walk"
                return False
            tree = Tree(self.d.tree_raw())
            if not self.in_front(tree):
                if self.blocked(tree):
                    continue
                s["stop_reason"] = "blocked before the walk: " + (tree.front_package or "nothing") + " in front"
                return False
            fp = tree.fingerprint(self.package)
            unchanged = unchanged + 1 if fp == prev else 0
            if unchanged >= self.stable_stops:
                s["rewind_end_confirmed"], why = self.confirm_end(tree, reverse=True)
                rewind_note = why
                break
            if s["rewind_scrolls"] >= self.max_rewind_scrolls:
                s["rewind_end_confirmed"] = False
                break
            prev = fp
            if not self.scroll(tree, 0.40, reverse=True, phase="rewind", ms=250):
                break
            s["rewind_scrolls"] += 1
            self.sleep(self.settle_s)
        self.add("system_event", "rewind_done", {"scrolls": s["rewind_scrolls"], "end_confirmed": s["rewind_end_confirmed"],
                                                 "note": rewind_note})
        return True

    def confirm_end(self, tree, reverse):
        """"Nothing moved" has two causes: the list is at its end, or the swipe was not taken. To tell them
        apart, scroll a little the other way. If the screen moves, swipes work here and the list really did
        stop; then scroll back. If it does not move either way, the end is not confirmed. `reverse` is the
        direction of the scrolling that stopped (True for the rewind)."""
        box = tree.scroll_container(self.package)
        before = tree.fingerprint(self.package)
        if not self.scroll(tree, 0.15, reverse=not reverse, phase="end check"):
            return False, "end not confirmed: nothing to scroll"
        self.sleep(self.settle_s)
        moved = Tree(self.d.tree_raw())
        if moved.front_package == self.package and moved.fingerprint(self.package) != before:
            self.scroll(moved, 0.30, reverse=reverse, phase="end check", ms=250)
            self.sleep(self.settle_s)
            back = Tree(self.d.tree_raw()).fingerprint(self.package) == before
            return True, ("end confirmed: the list moved the other way and not this way"
                          + ("" if back else "; the screen did not return to the same frame afterwards"))
        if box and box["rule"] != "scrollable":
            return True, "the list does not report itself as scrolling and did not move either way: one screen of content"
        return False, "END NOT CONFIRMED: the screen did not move in either direction, so the swipes may not be reaching the list"

    def scroll(self, tree, fraction, reverse=False, phase="walk", ms=None):
        box = tree.scroll_container(self.package)
        if box is None:
            return False
        l, t, r, b = box["bounds"]
        h = b - t
        x = (l + r) // 2
        down = (self.reveal == "above") != reverse  # finger moves down to bring earlier content into view
        if fraction <= 0.40:
            # Short moves stay in the middle of the list: one that started near the bottom bar was ignored.
            dist = int(h * fraction)
            y_top = t + int(h * 0.30)
            y1, y2 = (y_top, y_top + dist) if down else (y_top + dist, y_top)
        else:
            # Long moves use most of the list's height, with a margin of 12% at each end so the finger never
            # starts on a toolbar or the bottom bar.
            dist = int(h * min(fraction, 0.76))
            lo, hi = t + int(h * 0.12), b - int(h * 0.12)
            y1, y2 = (lo, lo + dist) if down else (hi, hi - dist)
        ms = ms or self.swipe_ms
        self.d.swipe(x, y1, x, y2, ms)
        if phase == "walk":
            self.summary["scrolls"] += 1
        self.add("auto_event", "scroll", {"from": [x, y1], "to": [x, y2], "duration_ms": ms, "phase": phase,
                                          "container": {k: v for k, v in box.items() if k != "node"},
                                          "reverse": reverse})
        return True


# ---- A list of items, each opened and walked ----

class ListWalk:
    """Reads the rows of the list in front, lets the examiner choose, then opens each chosen row, walks
    what it opens and comes back. A row is a direct child of the scrolling list; its title is its first text."""

    def __init__(self, walker, *, item_reveal="above", capture_list=True, max_search_scrolls=300):
        self.w, self.item_reveal, self.capture_list, self.max_search_scrolls = walker, item_reveal, capture_list, max_search_scrolls
        self.titles, self.signature, self.duplicates = [], None, set()
        self.result = {"mode": "list", "list": None, "items_found": 0, "items_selected": [], "items_walked": 0,
                       "items_not_opened": [], "duplicate_titles": [], "stop_reason": None}

    def collect(self, tree):
        box = tree.scroll_container(self.w.package)
        if box and self.signature is None:
            self.signature = list_signature(box)
        seen_here = set()
        for row in tree.rows(self.w.package):
            if not row["whole"]:
                continue
            if row["title"] in seen_here:
                self.duplicates.add(row["title"])
            seen_here.add(row["title"])
            if row["title"] not in self.titles:
                self.titles.append(row["title"])

    def is_list(self, tree):
        """True when the list is in front again: same kind of scrolling list, showing rows already known."""
        box = tree.scroll_container(self.w.package)
        if not self.w.in_front(tree) or box is None or list_signature(box) != self.signature:
            return False
        return any(r["title"] in self.titles for r in tree.rows(self.w.package))

    def run(self, choose):
        w, res = self.w, self.result
        res["list"] = w.run(item=None, reveal="below", capture=self.capture_list, on_tree=self.collect, rewind=True)
        res["items_found"], res["duplicate_titles"] = len(self.titles), sorted(self.duplicates)
        if not res["list"]["end_confirmed"] or not res["list"]["rewind_end_confirmed"]:
            res["stop_reason"] = "the list itself was not read from end to end: " + str(res["list"]["stop_reason"])
            return res
        selected, how = choose(self.titles)
        res["items_selected"] = selected
        w.item = None
        w.add("system_event", "items_selected", {"found": len(self.titles), "selected": selected, "chosen_by": how,
                                                 "list_pictures_kept": self.capture_list,
                                                 "duplicate_titles": res["duplicate_titles"]})
        w.summary = w.new_summary()
        if selected and not w.go_to_far_end():   # back to the first row; rows are then looked for going forward
            res["stop_reason"] = "blocked before opening the first item"
            return res
        for title in selected:
            w.item = None
            if w.should_stop():
                res["stop_reason"] = "stopped by the examiner"
                break
            opened = self.open(title)
            if opened is None:
                res["stop_reason"] = f"blocked while opening {title!r}"
                break
            if not opened:
                res["items_not_opened"].append(title)
                continue
            w.run(item=title, reveal=self.item_reveal, capture=True, rewind=True)
            res["items_walked"] += 1
            w.item = None
            if not self.back_to_list(title):
                res["stop_reason"] = f"could not get back to the list after {title!r}"
                break
        return res

    def open(self, title):
        """Finds the row going forward through the list (once more from the start if it is not found), taps
        its title and checks that another screen came up. True, False (not found or did not open), or None
        when something else is in front."""
        w = self.w
        w.reveal = "below"
        prev, unchanged, restarted, tries = None, 0, False, 0
        for _ in range(self.max_search_scrolls):
            tree = Tree(w.d.tree_raw())
            if not w.in_front(tree):
                if w.blocked(tree):
                    continue
                return None
            row = next((r for r in tree.rows(w.package) if r["title"] == title and r["fully_visible"] and r["whole"]), None)
            if row is not None:
                x, y = row["tap"]
                w.d.tap(x, y)
                w.sleep(max(1.5, w.settle_s))
                after = Tree(w.d.tree_raw())
                opened = w.in_front(after) and not self.is_list(after)
                w.add("auto_event", "open_item", {"title": title, "tap": [x, y], "row_bounds": row["bounds"],
                                                  "another_screen_came_up": opened,
                                                  "title_shown_on_it": any(n.get("text") == title or n.get("content-desc") == title
                                                                           for n in after.nodes(w.package))})
                if opened:
                    return True
                tries += 1
                if tries >= 2:
                    return False
                continue
            fp = tree.fingerprint(w.package)
            unchanged = unchanged + 1 if fp == prev else 0
            if unchanged >= w.stable_stops:
                if restarted:
                    w.add("system_event", "item_not_found", {"title": title})
                    return False
                restarted, prev, unchanged = True, None, 0
                w.summary = w.new_summary()
                if not w.go_to_far_end():
                    return None
                continue
            prev = fp
            w.scroll(tree, w.fraction, phase="find item", ms=600)
            w.sleep(w.settle_s)
        w.add("system_event", "item_not_found", {"title": title, "note": "search cap reached"})
        return False

    def back_to_list(self, title):
        w = self.w
        for presses in range(5):
            tree = Tree(w.d.tree_raw())
            if tree.front_package != w.package:
                w.add("system_event", "left_the_app_going_back", {"front_package": tree.front_package, "after_item": title})
                return False
            if self.is_list(tree):
                return True
            w.d.back()
            w.add("auto_event", "back", {"after_item": title, "press": presses + 1})
            w.sleep(max(1.0, w.settle_s))
        return False


def list_signature(box):
    return (box.get("resource_id"), box.get("class"), box["node"].get("content-desc"))


def chooser(select, matches, ask=None):
    """Returns the function that picks rows: all of them, those whose title contains one of `matches`, or
    whatever the examiner types (numbers, ranges or 'all')."""
    def choose(titles):
        if matches:
            return [t for t in titles if any(m.lower() in t.lower() for m in matches)], "title match: " + ", ".join(matches)
        if select == "all":
            return list(titles), "all rows"
        for i, t in enumerate(titles, 1):
            print(f"{i:4d}  {t}")
        answer = (ask or input)("Rows to document (for example 1,3-5 or all): ").strip().lower()
        if answer in ("all", "*"):
            return list(titles), "examiner: all"
        picked = []
        for part in answer.replace(" ", "").split(","):
            if not part:
                continue
            lo, _, hi = part.partition("-")
            for i in range(int(lo), int(hi or lo) + 1):
                if 1 <= i <= len(titles) and titles[i - 1] not in picked:
                    picked.append(titles[i - 1])
        return picked, "examiner: " + answer
    return choose


# ---- Export and checks ----

CSV_COLUMNS = ["seq", "local_time", "utc_time", "kind", "action", "app_package", "app_version", "screenshot_file",
               "screenshot_sha256", "screenshot_status", "detail", "elapsed_ms", "boot_count", "tz_id", "tz_offset_min",
               "entry_id", "prev_hash", "row_hash"]


def write_export(log, device, package, app_version, summary, args_used):
    out = log.out
    log.close()
    with open(os.path.join(out, "actions.jsonl"), "rb") as f:
        jsonl = f.read()
    with open(os.path.join(out, "actions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in log.rows:
            utc = datetime.fromtimestamp(r["utc_ms"] / 1000, tz=timezone.utc)
            local = datetime.fromtimestamp(r["utc_ms"] / 1000 + r["tz_offset_min"] * 60, tz=timezone.utc)
            shot = r.get("screenshot") or {}
            w.writerow([r["seq"], local.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        r["kind"], r["action"], r["app_package"], r["app_version"], shot.get("file"), shot.get("sha256"),
                        shot.get("status"), r["detail"], r["elapsed_ms"], r["boot_count"], r["tz_id"], r["tz_offset_min"],
                        r["entry_id"], r["prev_hash"], r["row_hash"]])
    with open(os.path.join(out, "text_index.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["step", "item", "entry_id", "resource_id", "class", "text", "content_desc", "bounds"])
        for t in log.trees:
            for n in ET.parse(os.path.join(out, "trees", t["file"])).getroot().iter("node"):
                if n.get("package") == package and (n.get("text") or n.get("content-desc")):
                    w.writerow([t["step"], t["item"], t["entry_id"], n.get("resource-id"), n.get("class"), n.get("text"),
                                n.get("content-desc"), n.get("bounds")])

    def rehash(folder, items):
        for it in items:
            p = os.path.join(out, folder, it["file"])
            it["sha256_at_export"] = sha256(read_bytes(p)) if os.path.exists(p) else None
            it["hash_match"] = it["sha256_at_export"] == it["sha256_at_capture"]
    rehash("screens", log.shots)
    rehash("trees", log.trees)
    ok, _, bad = verify_chain(log.rows)
    stamp = device.stamp()
    prof = dict(device.profile(), id="p1", utc_ms=log.rows[0]["utc_ms"], boot_count=log.rows[0]["boot_count"])
    manifest = {
        "format": "known-data-logger", "schema_version": SCHEMA_VERSION,
        "written_by": "AppWalk, a workstation tool; not the KDL app",
        "logger": {"package": "appwalk (workstation, over adb)", "version_name": VERSION,
                   "first_install_ms": None, "last_update_ms": None},
        "scenario_id": log.scenario_id, "device_label": log.device_label, "profiles": [prof],
        "apps_under_test": [{"package": package, "label": package, "version_name_at_add": app_version,
                             "version_code_at_add": None, "version_name_at_export": device.app_version(package),
                             "first_install_ms": None, "last_update_ms": None, "account": None,
                             "added_utc_ms": log.rows[0]["utc_ms"]}],
        "rules": [], "tolerances_s": {"exact": 120, "before_after": 300, "approximate": 900, "windowed": "window_s"},
        "row_rules": "One row per thing the tool did. Order rows by seq. Each screenshot row's detail holds the "
                     "sha256 of its image and of its view tree, so the row hash chain covers both files. "
                     "utc_ms is the phone's clock; elapsed_ms is the first field of the phone's /proc/uptime; "
                     "host_utc_ms in detail is the workstation's clock.",
        "walk": {"parameters": args_used, "summary": summary},
        "export": stamp,
        "files": {"actions.jsonl": {"sha256": sha256(jsonl), "rows": len(log.rows)},
                  "actions.csv": {"sha256": sha256(read_bytes(os.path.join(out, "actions.csv")))},
                  "text_index.csv": {"sha256": sha256(read_bytes(os.path.join(out, "text_index.csv")))}},
        "hash_chain": {"rows": len(log.rows), "last_seq": log.rows[-1]["seq"], "last_row_hash": log.rows[-1]["row_hash"],
                       "first_break_seq": bad},
        "screenshots": log.shots, "view_trees": log.trees,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    # The transcript is made from the files above and can be made again at any time, so it is not in
    # the chain: the screenshots and view trees are the record, the transcript is a reading aid.
    try:
        import transcript
        _, notes = transcript.write(out)
        manifest["transcript_notes"] = notes
    except Exception as e:  # a reading aid must never cost the export
        manifest["transcript_notes"] = [f"transcript not written: {type(e).__name__}: {e}"]
    try:
        import report_pdf
        report_pdf.build(out)
        manifest["report_note"] = "report.pdf written; its SHA-256 is in report.pdf.sha256"
    except ImportError as e:
        # Name the package to install, which is not always the name imported (PIL comes from Pillow).
        package = {"PIL": "Pillow", "reportlab": "ReportLab"}.get((e.name or "").split(".")[0], e.name or "a module")
        manifest["report_note"] = f"report.pdf not written: {package} is not installed (pip install {package.lower()})"
    except Exception as e:  # nor may the report
        manifest["report_note"] = f"report.pdf not written: {type(e).__name__}: {e}"
    return manifest


# ---- The phone's identity, read from Settings > About phone ----

# Labels seen on About phone screens, lower case, in the languages tried. A value is the text right after
# its label in the view tree. Add a label only from a screen that showed it.
IDENTITY_LABELS = {
    "phone_number": ("phone number", "my phone number", "número de teléfono", "numero de telefono", "mi número de teléfono"),
    "serial_number": ("serial number", "número de serie", "numero de serie"),
    "model": ("model", "model name", "modelo", "nombre del modelo"),
}

# A phone number inside a value: an optional "+", then digits with spaces, dashes or brackets between them.
PHONE_IN_TEXT = re.compile(r"\+?\d[\d\s()-]{5,}\d")


def imei_ok(digits):
    """True for 15 digits whose last is the IMEI check digit (the Luhn formula of 3GPP TS 23.003)."""
    if not re.fullmatch(r"\d{15}", digits):
        return False
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c)
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def identity_from_trees(export):
    """IMEIs, phone numbers, serial and model as the About phone screens' view trees reported them, each
    with the screenshot it was read from. An IMEI counts only when its check digit is right."""
    found = {"imei": [], "phone_number": [], "serial_number": [], "model": []}
    seen = set()
    with open(os.path.join(export, "actions.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    for r in rows:
        if r["action"] != "screenshot":
            continue
        d = json.loads(r["detail"])
        texts = [n.get("text") for n in ET.parse(os.path.join(export, "trees", d["tree_file"])).getroot().iter("node")
                 if n.get("text")]
        for i, t in enumerate(texts):
            digits = re.sub(r"[\s-]", "", t)
            if imei_ok(digits) and ("imei", digits) not in seen:
                seen.add(("imei", digits))
                found["imei"].append({"value": digits, "step": d["step"]})
            # A label can name its SIM after a middle dot: "Número de teléfono · Tarjeta SIM" (moto g47,
            # Android 16, Device details, 14 November 2026). The part before the dot is the label.
            label = t.strip().lower().rstrip(":").split(" · ")[0].strip()
            for key, names in IDENTITY_LABELS.items():
                if i + 1 < len(texts) and (label in names or re.fullmatch(r"(" + "|".join(map(re.escape, names)) + r") ?\d", label)):
                    v = texts[i + 1].strip()
                    if key == "phone_number":
                        # Only the number itself. The moto g47 put the SIM's state first ("Solo emergencia
                        # +54 9 ..."), and gave "No disponible" for an eSIM with no number.
                        m = PHONE_IN_TEXT.search(v)
                        v = m.group(0) if m else ""
                    if v and (key, v) not in seen:
                        seen.add((key, v))
                        found[key].append({"value": v, "step": d["step"], "label": t.strip()})
    return found


def identify(serial, out, device_label, case=None, say=print):
    """Opens Settings > About phone, walks it top to bottom and reads the phone's identity from its view
    trees. Where the phone gives no view tree there (a Pixel 7a on Android 17 did not, 13 November 2026),
    the screenshots are still kept and chained, for the examiner to read. The values found go into a
    chained row; the examiner confirms them before they are used. Returns (export, found, note)."""
    dev = AdbDevice(serial)
    dev.shell(OPEN_ABOUT_PHONE)
    time.sleep(2)
    # Stock Android's About phone shows an Uptime line that changes every second, so the screen is never
    # still and uiautomator never gives its tree (Pixel 7a on Android 17, emulator on Android 15). One quick
    # try decides: a tree now means the walk can read it; none means screenshots only, without waiting.
    probe = dev.adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=90)
    readable = b"</hierarchy>" in probe
    if readable:
        r = run_job(serial, "com.android.settings", out, "phone-identity", device_label, reveal="below", rewind=True,
                    max_screens=15, case=case, say=say)
        export = r["export"]
    else:
        export = empty_export(dev, out, "phone-identity", device_label, case)
    note = ""
    if not readable or r["summary"]["screens"] == 0:
        dev.shell(OPEN_ABOUT_PHONE)
        time.sleep(1)
        # A readable screen can still give no walk: on a moto g47 (14 November 2026) the walk was blocked
        # before it began, com.android.systemui in front. Say that, not that there was no view tree.
        why = None if not readable else "the walk took no screenshot: " + str(r["summary"]["stop_reason"])
        note = screenshots_without_tree(export, dev, why=why)
    found = identity_from_trees(export)
    if readable and not found["imei"]:
        opened = walk_details_row(dev, export, say)
        if opened:
            found = identity_from_trees(export)
            note = (note + " " if note else "") + f'About phone named no IMEI; the row "{opened}" was opened and walked too.'
    log = AppendLog(export, dev)
    log.add("system_event", "identity_read", {"found": found, "note": note,
                                               "how": "From the view trees of Settings > About phone and any row opened from it; an IMEI only when its check digit is right."})
    log.close()
    refresh_manifest(export, log.rows, dev)
    return export, found, note


# About phone in a task of its own (NEW_TASK and CLEAR_TASK). Without it Android brings Settings to the front
# as it was left: a moto g47 still on Device details was walked as if that were About phone (14 November 2026).
OPEN_ABOUT_PHONE = "am start -f 0x10008000 -a android.settings.DEVICE_INFO_SETTINGS"


# Rows of About phone that lead to the IMEI, lower case. Add a label only from a screen that showed it.
DETAILS_ROW_LABELS = ("detalles del dispositivo",)   # moto g47, Android 16, es-US, 14 November 2026


def walk_details_row(dev, export, say=print, tries=8):
    """When About phone names no IMEI, finds a row that leads to it by its text, taps it and walks the
    screen it opens, into the same export. On a moto g47 the IMEIs are in Device details, one screen in,
    and About phone is laid out as cards, which the list reader does not take as rows. Returns the row's
    text, or None when no such row was found or it did not open."""
    dev.shell(OPEN_ABOUT_PHONE)
    time.sleep(2)
    for _ in range(tries):
        tree = Tree(dev.tree_raw())
        hit = next((n for n in tree.nodes("com.android.settings")
                    if (n.get("text") or "").strip().lower() in DETAILS_ROW_LABELS), None)
        if hit is not None:
            break
        dev.swipe(540, 1600, 540, 900, 600)
        time.sleep(1.5)
    else:
        return None
    l, t, r, b = map(int, BOUNDS.match(hit.get("bounds")).groups())
    x, y, title = (l + r) // 2, (t + b) // 2, hit.get("text").strip()
    before = tree.fingerprint("com.android.settings")
    dev.tap(x, y)
    time.sleep(2)
    after = Tree(dev.tree_raw())
    log = AppendLog(export, dev)
    opened = after.front_package == "com.android.settings" and after.fingerprint("com.android.settings") != before
    log.add("auto_event", "open_item", {"title": title, "tap": [x, y], "row_bounds": [l, t, r, b], "opened": opened,
                                        "why": "About phone named no IMEI; this row leads to it on some phones"})
    if opened:
        walker = Walker(dev, log, "com.android.settings", reveal="below", max_screens=15, rewind=True, say=say)
        walker.run(item=title, reveal="below", capture=True, rewind=True)
    log.close()
    refresh_manifest(export, log.rows, dev)
    return title if opened else None


def nearly_same(a, b):
    """True when two screenshots differ in under 1% of their pixels: a clock that ticks, not a scroll."""
    if a is None or b is None:
        return False
    if a == b:
        return True
    if not pixels_available():
        return False
    from PIL import Image, ImageChops
    x, y = Image.open(io.BytesIO(a)).convert("L"), Image.open(io.BytesIO(b)).convert("L")
    if x.size != y.size:
        return False
    diff = ImageChops.difference(x, y).point(lambda v: 255 if v > 24 else 0)
    return diff.histogram()[255] < 0.01 * x.size[0] * x.size[1]


def empty_export(dev, out, scenario, device_label, case=None):
    """An export with no walk in it yet: the folder, the log's first rows and a manifest, for steps that
    add their own rows (the About phone screenshots)."""
    stamp = dev.stamp()
    t = datetime.fromtimestamp(stamp["utc_ms"] / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = lambda x: re.sub(r"[^A-Za-z0-9._-]", "_", x) or "unnamed"
    export = os.path.join(out, f"kdl_{safe(scenario)}_{safe(device_label)}_{t}")
    os.makedirs(export)
    log = Log(export, scenario, device_label, dev)
    case = {k: v for k, v in (case or {}).items() if v}
    if case:
        log.add("system_event", "case_information", case)
    log.add("system_event", "device_state", dict(device_facts(dev.serial), when="before the run"))
    summary = {"item": None, "screens": 0, "scrolls": 0, "rewind_scrolls": 0, "rewind_end_confirmed": None,
               "end_confirmed": None, "gaps": 0, "gaps_retried": 0, "unsettled": 0, "blank": 0, "pauses": 0,
               "stop_reason": "no walk: the screen's view tree could not be read, so only screenshots were taken"}
    write_export(log, dev, "com.android.settings", dev.app_version("com.android.settings"), summary,
                 {"mode": "phone identity", "serial": dev.serial})
    return export


def screenshots_without_tree(export, dev, most=8, why=None):
    """Screenshots only, scrolling down, each hash in a chained row, until a scroll no longer changes the
    screen (a ticking clock aside): for a screen whose view tree cannot be read, or whose walk took none
    (`why` says so then). Returns a note saying which."""
    # Settings can reopen the page where it was left: go to the top first, until a scroll changes nothing.
    top = dev.screenshot()
    for _ in range(10):
        dev.swipe(540, 900, 540, 1700, 400)
        time.sleep(0.8)
        now = dev.screenshot()
        if nearly_same(now, top):
            break
        top = now
    log = AppendLog(export, dev)
    prev, n = None, 0
    for i in range(most):
        png = dev.screenshot()
        if nearly_same(png, prev):
            break
        n += 1
        name = f"nt{n:02d}_{uuid.uuid4().hex}.png"
        with open(os.path.join(export, "screens", name), "wb") as f:
            f.write(png)
        log.add("auto_event", "screenshot_without_tree", {"png_file": name, "png_sha256": sha256(png), "n": n,
                                                          "why": why or "the phone gave no view tree for this screen"})
        prev = png
        dev.swipe(540, 1700, 540, 900, 600)
        time.sleep(1.5)
    log.close()
    refresh_manifest(export, log.rows, dev)
    if why:
        return (f"About phone could not be walked ({why}), so nothing could be read from it; {n} screenshot(s) "
                "of it are kept and chained, for the values to be read from them.")
    return (f"The phone gave no view tree for About phone, so nothing could be read from it; {n} screenshot(s) "
            "of it are kept and chained, for the values to be read from them.")


# ---- Files added to an export after the walk ----

class AppendLog(Log):
    """The log of an export already written, opened to add rows to the end of its chain."""
    def __init__(self, out_dir, device):
        self.out, self.device, self.host_clock = out_dir, device, time.time
        with open(os.path.join(out_dir, "actions.jsonl"), encoding="utf-8") as f:
            self.rows = [json.loads(x) for x in f if x.strip()]
        self.scenario_id, self.device_label = self.rows[0]["scenario_id"], self.rows[0]["device_label"]
        self.shots, self.trees = [], []
        self.fh = open(os.path.join(out_dir, "actions.jsonl"), "ab")


class HostClock:
    """For a file added with no phone connected: the workstation's clock, and no phone uptime or boot count."""
    def stamp(self):
        now = time.time()
        off = int((datetime.fromtimestamp(now).astimezone().utcoffset() or timedelta(0)).total_seconds() // 60)
        return {"utc_ms": int(now * 1000), "elapsed_ms": None, "tz_id": "workstation", "tz_offset_min": off, "boot_count": -1,
                "time_quality": "workstation clock"}


def zip_members(path):
    """(name, bytes, crc32 as hex, date in the zip) of each member, or None when the file is not a zip."""
    import zipfile
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as z:
        return [{"name": i.filename, "bytes": i.file_size, "crc32": f"{i.CRC:08x}",
                 "date_in_zip": "%04d-%02d-%02d %02d:%02d:%02d" % i.date_time} for i in z.infolist()]


def attach_file(export, phone_path=None, serial=None, local_path=None, note=None):
    """Adds a file to an export: one the examiner made on the phone (an app's own chat export, for example),
    pulled with adb, or one already on the workstation. The file is kept in attachments/, and a row added to
    the end of the hash chain holds its SHA-256 and size (and, for a zip, each member's name, size and
    CRC-32), so the latest row hash covers it as it covers the screenshots. A file pulled from the phone is
    also hashed on the phone, so the copy can be shown to match it. The manifest, actions.csv, transcript
    and report are written again. Returns the row's detail."""
    import shutil
    folder = os.path.join(export, "attachments")
    os.makedirs(folder, exist_ok=True)
    if phone_path:
        dev = AdbDevice(serial)
        name = os.path.basename(phone_path.rstrip("/"))
    else:
        dev = None
        name = os.path.basename(local_path)
    dest = os.path.join(folder, name)
    if os.path.exists(dest):
        raise FileExistsError(f"attachments/{name} is already in this export")
    detail = {"file": name, "note": note or ""}
    if phone_path:
        quoted = "'" + phone_path.replace("'", "'\\''") + "'"
        phone_sum = dev.shell(f"sha256sum {quoted}").split()[0] if phone_path else None
        dev.adb("pull", phone_path, dest, timeout=3600)
        detail.update(source="phone", phone_path=phone_path, sha256_on_phone=phone_sum)
    else:
        shutil.copy2(local_path, dest)
        detail.update(source="workstation", workstation_path=os.path.abspath(local_path))
    data = read_bytes(dest)
    detail.update(sha256=sha256(data), bytes=len(data))
    if phone_path and detail["sha256_on_phone"] != detail["sha256"]:
        os.remove(dest)
        raise RuntimeError(f"the copy of {name} does not match the file on the phone (SHA-256 differs); nothing was added")
    members = zip_members(dest)
    if members is not None:
        detail["zip_members"] = members
    log = AppendLog(export, dev or HostClock())
    log.add("system_event", "file_attached", detail)
    log.close()
    refresh_manifest(export, log.rows, dev)
    return detail


def refresh_manifest(export, rows, dev=None):
    """After rows were added: actions.csv, the manifest's file hashes and chain end, then the transcript and
    report, written again."""
    with open(os.path.join(export, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(export, "actions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            utc = datetime.fromtimestamp(r["utc_ms"] / 1000, tz=timezone.utc)
            local = datetime.fromtimestamp(r["utc_ms"] / 1000 + r["tz_offset_min"] * 60, tz=timezone.utc)
            shot = r.get("screenshot") or {}
            w.writerow([r["seq"], local.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        r["kind"], r["action"], r["app_package"], r["app_version"], shot.get("file"), shot.get("sha256"),
                        shot.get("status"), r["detail"], r["elapsed_ms"], r["boot_count"], r["tz_id"], r["tz_offset_min"],
                        r["entry_id"], r["prev_hash"], r["row_hash"]])
    ok, _, bad = verify_chain(rows)
    manifest["files"]["actions.jsonl"] = {"sha256": sha256(read_bytes(os.path.join(export, "actions.jsonl"))), "rows": len(rows)}
    manifest["files"]["actions.csv"] = {"sha256": sha256(read_bytes(os.path.join(export, "actions.csv")))}
    manifest["hash_chain"] = {"rows": len(rows), "last_seq": rows[-1]["seq"], "last_row_hash": rows[-1]["row_hash"],
                              "first_break_seq": bad}
    manifest["attachments"] = [json.loads(r["detail"]) for r in rows if r["action"] == "file_attached"]
    manifest["screenshots_without_tree"] = [json.loads(r["detail"]) for r in rows if r["action"] == "screenshot_without_tree"]
    found = [json.loads(r["detail"]) for r in rows if r["action"] == "identity_read"]
    if found:
        manifest["identity"] = found[-1]
    with open(os.path.join(export, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    try:
        import transcript
        transcript.write(export)
    except Exception:  # a reading aid must never cost the export
        pass
    try:
        import report_pdf
        report_pdf.build(export)
    except Exception:  # nor may the report
        pass
    return manifest


def verify_export(folder):
    """Checks an export against itself: file hash, chain, and every image and tree against the hash in its
    chained row. Returns a list of (check, result, ok)."""
    res = []
    manifest = json.loads(read_bytes(os.path.join(folder, "manifest.json")).decode("utf-8"))
    raw = read_bytes(os.path.join(folder, "actions.jsonl"))
    rows = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    want = manifest["files"]["actions.jsonl"]["sha256"]
    res.append(("actions.jsonl sha256", "match" if want == sha256(raw) else "MISMATCH", want == sha256(raw)))
    ok, msg, _ = verify_chain(rows)
    if ok and manifest["hash_chain"]["last_row_hash"] != rows[-1]["row_hash"]:
        ok, msg = False, "MISMATCH with manifest: rows were added or removed after the manifest was written"
    res.append(("hash chain", msg, ok))
    n = bad = 0
    listed = set()
    for r in rows:
        if r["action"] != "screenshot":
            continue
        d = json.loads(r["detail"])
        for sub, key_f, key_h in (("screens", "png_file", "png_sha256"), ("trees", "tree_file", "tree_sha256")):
            # The files are named after the row, so the name is rebuilt from the row, not trusted from detail.
            ext = ".png" if sub == "screens" else ".xml"
            name = f"{d['step']:04d}_{r['entry_id']}{ext}"
            listed.add((sub, name))
            p = os.path.join(folder, sub, name)
            n += 1
            if not os.path.exists(p) or sha256(read_bytes(p)) != d[key_h]:
                bad += 1
                res.append((f"{sub}/{name}", "MISSING" if not os.path.exists(p) else "MISMATCH with the hash in its chained row", False))
    res.append(("images and view trees against chained rows", f"{n - bad} of {n} match", bad == 0))
    nt = [json.loads(r["detail"]) for r in rows if r["action"] == "screenshot_without_tree"]
    if nt:
        bad_nt = [d["png_file"] for d in nt if not os.path.exists(os.path.join(folder, "screens", d["png_file"]))
                  or sha256(read_bytes(os.path.join(folder, "screens", d["png_file"]))) != d["png_sha256"]]
        res.append(("screenshots without a view tree against chained rows", f"{len(nt) - len(bad_nt)} of {len(nt)} match", not bad_nt))
        listed |= {("screens", d["png_file"]) for d in nt}
    att = [json.loads(r["detail"]) for r in rows if r["action"] == "file_attached"]
    if att:
        bad_att = [d["file"] for d in att if not os.path.exists(os.path.join(folder, "attachments", d["file"]))
                   or sha256(read_bytes(os.path.join(folder, "attachments", d["file"]))) != d["sha256"]]
        res.append(("attached files against chained rows", f"{len(att) - len(bad_att)} of {len(att)} match"
                    + ("" if not bad_att else ": MISMATCH or MISSING " + ", ".join(bad_att)), not bad_att))
        listed |= {("attachments", d["file"]) for d in att}
    subs = ("screens", "trees") + (("attachments",) if os.path.isdir(os.path.join(folder, "attachments")) else ())
    extra = [(s, f) for s in subs for f in sorted(os.listdir(os.path.join(folder, s))) if (s, f) not in listed]
    res.append(("files with no chained row", "none" if not extra else "EXTRA: " + ", ".join(f"{s}/{f}" for s, f in extra), not extra))
    return res


# ---- Command line ----

def ask_examiner(allow_secret):
    def ask(why):
        print(f"\nStopped: {why}.\nDeal with it on the phone and bring the screen to document back to the front.", flush=True)
        if allow_secret:
            prompt = "Press Enter when done, type the code to have it typed on the phone (not recorded), or q to end: "
            answer = getpass.getpass(prompt) if sys.stdin.isatty() else (print(prompt, flush=True) or sys.stdin.readline().rstrip("\n"))
        else:
            answer = input("Press Enter when done, or q to end: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
        if answer.strip().lower() == "q":
            return None
        return answer if allow_secret else ""
    return ask


def list_devices(adb=None):
    """[(serial, state, description)] as adb lists them."""
    adb = adb or find_adb()
    p = subprocess.run([adb, "devices", "-l"], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    out = []
    for line in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((x.split(":", 1)[1] for x in parts[2:] if x.startswith("model:")), "")
            out.append((parts[0], parts[1], model))
    return out


def device_facts(serial, adb=None):
    """What the examiner should see before a run and what the log keeps before and after it: model,
    Android version, battery, airplane mode, Wi-Fi, and how far the phone's clock is from this computer's.
    Each value is as the phone reports it; None when it did not answer."""
    script = ("getprop ro.product.manufacturer; getprop ro.product.model; getprop ro.build.version.release; "
              "settings get global airplane_mode_on; settings get global wifi_on; settings get system screen_off_timeout; "
              "dumpsys battery | grep -m1 level; date +%s%N")
    before = time.time()
    adb = adb or find_adb()
    p = subprocess.run([adb, "-s", serial, "shell", script], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    after = time.time()
    lines = [x.strip() for x in p.stdout.decode("utf-8", "replace").splitlines()]
    lines += [""] * (8 - len(lines))
    num = lambda v: int(v) if v.lstrip("-").isdigit() else None
    level = re.search(r"(\d+)", lines[6])
    phone_ms = num(lines[7][:13]) if len(lines[7]) >= 13 else None
    return {"manufacturer": lines[0] or None, "model": lines[1] or None, "android": lines[2] or None,
            "airplane_mode_on": num(lines[3]), "wifi_on": num(lines[4]), "screen_off_timeout_ms": num(lines[5]),
            "battery_percent": int(level.group(1)) if level else None,
            "phone_clock_minus_computer_s": None if phone_ms is None else round(phone_ms / 1000 - (before + after) / 2, 1)}


def app_in_front(serial, adb=None):
    """Package of the app in front on the phone, from the focused window, or None."""
    adb = adb or find_adb()
    p = subprocess.run([adb, "-s", serial, "shell", "dumpsys", "window"], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    m = re.search(r"mCurrentFocus=Window\{[^}]*\s([A-Za-z0-9_.]+)/", p.stdout.decode("utf-8", "replace"))
    return m.group(1) if m else None


def run_job(serial, package, out, scenario, device_label, mode="screen", reveal="above", rewind=True, fraction=0.70,
            swipe_ms=1200, max_screens=200, item_reveal="above", capture_list=True, choose=None, ask=None, say=print,
            on_screenshot=None, should_stop=None, pause_gate=None, case=None, stop_at=None):
    """One whole run, for the command line and the window alike. mode is "screen" (walk what is open) or
    "list" (choose rows, open and walk each). Returns a dict: export, ok, lines (the summary a person
    reads), summary, manifest."""
    dev = AdbDevice(serial)
    stamp = dev.stamp()
    t = datetime.fromtimestamp(stamp["utc_ms"] / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = lambda x: re.sub(r"[^A-Za-z0-9._-]", "_", x) or "unnamed"
    export = os.path.join(out, f"kdl_{safe(scenario)}_{safe(device_label)}_{t}")
    os.makedirs(export)
    log = Log(export, scenario, device_label, dev)
    walker = Walker(dev, log, package, reveal=reveal, fraction=fraction, swipe_ms=swipe_ms, max_screens=max_screens,
                    on_blocked="ask" if ask else "stop", rewind=rewind, ask=ask, say=say, stop_at=stop_at)
    if should_stop:
        walker.should_stop = should_stop
    if pause_gate:
        walker.pause_gate = pause_gate
    case = {k: v for k, v in (case or {}).items() if v}
    if case:
        # Who ran it and under what reference, as the examiner typed it. In the chain like every other row.
        log.add("system_event", "case_information", case)
    log.add("system_event", "device_state", dict(device_facts(serial), when="before the run"))
    if on_screenshot:
        # The window shows each picture as it is taken.
        saved = walker.save

        def save_and_tell(*a, **k):
            saved(*a, **k)
            on_screenshot(os.path.join(export, "screens", log.shots[-1]["file"]), walker.step, walker.item)
        walker.save = save_and_tell
    params = dict(serial=serial, package=package, mode=mode, reveal=reveal, rewind=rewind, fraction=fraction, swipe_ms=swipe_ms,
                  max_screens=max_screens, item_reveal=item_reveal, capture_list=capture_list, on_blocked="ask" if ask else "stop",
                  stop_at=stop_at)

    def clean(s, need_rewind=True):
        return bool(s["gaps"] == 0 and s["unsettled"] == 0 and s["end_confirmed"] and (not need_rewind or s["rewind_end_confirmed"]))

    if mode == "screen":
        summary = walker.run()
        ok = clean(summary, rewind)
        lines = [f"Screens {summary['screens']}, scrolls {summary['scrolls']} (plus {summary['rewind_scrolls']} to reach the far end), "
                 f"gaps {summary['gaps']} (plus {summary.get('gaps_retried', 0)} closed by going back and taking them again), "
                 f"blank {summary['blank']}, unsettled {summary['unsettled']}. "
                 f"Far end confirmed: {summary['rewind_end_confirmed']}. Stopped: {summary['stop_reason']}"]
    else:
        lw = ListWalk(walker, item_reveal=item_reveal, capture_list=capture_list)
        summary = lw.run(choose or chooser("all", []))
        items = [s for s in walker.walks if s["item"]]
        summary["walks"] = items
        ok = (summary["stop_reason"] is None and not summary["items_not_opened"] and clean(summary["list"])
              and len(items) == len(summary["items_selected"]) and all(clean(s) for s in items))
        lines = [f"Rows found {summary['items_found']}, chosen {len(summary['items_selected'])}, opened and walked {summary['items_walked']}, "
                 f"not opened {len(summary['items_not_opened'])}. Screenshots {walker.step}."]
        for s in items:
            lines.append(f"  {s['item']}: {s['screens']} screens, gaps {s['gaps']}, far end {s['rewind_end_confirmed']}, end {s['end_confirmed']}")
        if summary["duplicate_titles"]:
            lines.append("Rows sharing a title (only the first of each was opened): " + ", ".join(summary["duplicate_titles"]))
        if summary["stop_reason"]:
            lines.append("Stopped: " + summary["stop_reason"])
    blank = sum(1 for s in log.shots if s["status"] == "blank")
    if blank:
        ok = False
        lines.append(f"{blank} of {len(log.shots)} screenshots are black: the app blocks screenshots there. "
                     "Any text for those screens is in the view trees only.")
    log.add("system_event", "device_state", dict(device_facts(serial), when="after the run"))
    params["case"] = case
    manifest = write_export(log, dev, package, walker.app_version, summary, params)
    lines.append(f"Latest row hash (note it somewhere else): {manifest['hash_chain']['last_row_hash']}")
    lines += ["Transcript: " + n for n in manifest.get("transcript_notes", [])]
    if manifest.get("report_note"):
        lines.append("Report: " + manifest["report_note"])
    if not ok:
        lines.append("WARNING: this run is not complete or not confirmed. Read the stop reasons and the log.")
    return {"export": export, "ok": ok, "lines": lines, "summary": summary, "manifest": manifest}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", required=True, help="adb serial of the phone")
    common.add_argument("--package", required=True, help="package of the app to document; the walk stops if another app is in front")
    common.add_argument("--out", required=True, help="folder to create the export in")
    common.add_argument("--scenario", required=True)
    common.add_argument("--device-label", required=True)
    common.add_argument("--fraction", type=float, default=0.70, help="scroll distance as a part of the list height (default 0.70; 0.30 is the cautious setting)")
    common.add_argument("--swipe-ms", type=int, default=1200)
    common.add_argument("--max-screens", type=int, default=200, help="cap on screenshots per walk")
    common.add_argument("--stop-at", metavar="TEXT", help="end the walk at the first screenshot that shows this text "
                        "(for the newest part of a long conversation); the end is then not confirmed")
    common.add_argument("--on-blocked", choices=["stop", "ask"], default="stop", help="what to do when a lock, a password prompt or another app is in front")
    common.add_argument("--allow-secret-entry", action="store_true", help="with --on-blocked ask: let the examiner type a code at the workstation for the tool to enter")
    w = sub.add_parser("walk", parents=[common], help="document the one screen that is open on the phone")
    w.add_argument("--reveal", choices=["above", "below"], default="above", help="where the content not yet seen is (default: above, as in a message thread opened at its newest message)")
    w.add_argument("--no-rewind", action="store_true", help="start from the screen as it is, without first scrolling to the far end")
    wl = sub.add_parser("walk-list", parents=[common], help="with a list open on the phone (conversations, for example): choose rows, and the tool opens and documents each")
    wl.add_argument("--select", choices=["ask", "all"], default="ask", help="ask the examiner which rows, or take all of them")
    wl.add_argument("--match", action="append", default=[], help="take the rows whose title contains this text (repeatable); overrides --select")
    wl.add_argument("--skip-list-capture", action="store_true", help="read the list's rows without keeping screenshots or view trees of the list itself, which show a line of every row, chosen or not")
    wl.add_argument("--item-reveal", choices=["above", "below"], default="above", help="direction to walk each opened item (default: above)")
    v = sub.add_parser("verify", help="check an export against itself")
    v.add_argument("export")
    tr = sub.add_parser("transcript", help="write transcript.csv and transcript.html again for an export")
    tr.add_argument("export")
    rp = sub.add_parser("report", help="write report.pdf for an export (needs ReportLab)")
    rp.add_argument("export")
    idp = sub.add_parser("identify", help="open Settings > About phone and read the phone's identity (IMEI, phone "
                         "number, serial) from it, into an export of its own")
    idp.add_argument("--serial", required=True)
    idp.add_argument("--out", required=True)
    idp.add_argument("--device-label", default="phone")
    at = sub.add_parser("attach", help="add a file to an export, such as an app's own chat export, pulled from the "
                        "phone or copied from this computer; it goes into the hash chain")
    at.add_argument("export")
    src = at.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-phone", metavar="PATH", help="the file's path on the phone, for example /sdcard/Download/chat.zip")
    src.add_argument("--file", metavar="PATH", help="a file already on this computer")
    at.add_argument("--serial", help="adb serial of the phone (with --from-phone)")
    at.add_argument("--note", help="what the file is and how it was made, in words, kept in its row")
    tp = sub.add_parser("teach", help="open the Teach window on an export: a profile for its app from a few clicks")
    tp.add_argument("export")
    a = ap.parse_args(argv)

    if a.cmd == "verify":
        res = verify_export(a.export)
        for name, result, ok in res:
            print(("ok      " if ok else "WARNING ") + f"{name}: {result}")
        return 0 if all(ok for _, _, ok in res) else 1

    if a.cmd == "identify":
        export, found, note = identify(a.serial, a.out, a.device_label)
        print("Export:", export)
        if note:
            print(note)
        for key in ("model", "imei", "phone_number", "serial_number"):
            for v in found[key]:
                print(f"{key}: {v['value']}  (screenshot S{v['step']})")
        if not any(found.values()):
            print("Nothing read. Check the screenshots of About phone in the export.")
        return 0

    if a.cmd == "attach":
        if a.from_phone and not a.serial:
            ap.error("--from-phone needs --serial")
        d = attach_file(a.export, phone_path=a.from_phone, serial=a.serial, local_path=a.file, note=a.note)
        print(f"attached attachments/{d['file']}: {d['bytes']} bytes, sha256 {d['sha256']}")
        if d.get("sha256_on_phone"):
            print("the copy matches the file on the phone (same SHA-256)")
        if "zip_members" in d:
            print(f"a zip of {len(d['zip_members'])} files")
        with open(os.path.join(a.export, "manifest.json"), encoding="utf-8") as f:
            print("Latest row hash (note it somewhere else):", json.load(f)["hash_chain"]["last_row_hash"])
        return 0

    if a.cmd == "teach":
        import tkinter as tk
        import teach_gui
        import appwalk_gui_classic
        root = tk.Tk()
        root.withdraw()
        appwalk_gui_classic.window_icon(root)
        dlg = teach_gui.TeachDialog(root, a.export, on_saved=print)
        dlg.win.protocol("WM_DELETE_WINDOW", root.destroy)
        dlg.win.bind("<Destroy>", lambda e: e.widget is dlg.win and root.after(10, root.destroy))
        root.mainloop()
        return 0

    if a.cmd == "report":
        import report_pdf
        path, digest = report_pdf.build(a.export)
        print(f"wrote {path}\nsha256 {digest}")
        return 0

    if a.cmd == "transcript":
        import transcript
        rows, notes = transcript.write(a.export)
        print(f"{len(rows)} lines written to transcript.csv and transcript.html")
        for n in notes:
            print("note:", n)
        return 0

    job = dict(serial=a.serial, package=a.package, out=a.out, scenario=a.scenario, device_label=a.device_label,
               mode="list" if a.cmd == "walk-list" else "screen", reveal=getattr(a, "reveal", "above"),
               rewind=not getattr(a, "no_rewind", False), fraction=a.fraction, swipe_ms=a.swipe_ms, max_screens=a.max_screens,
               item_reveal=getattr(a, "item_reveal", "above"), capture_list=not getattr(a, "skip_list_capture", False),
               choose=chooser(a.select, a.match) if a.cmd == "walk-list" else None, stop_at=a.stop_at,
               ask=ask_examiner(a.allow_secret_entry) if a.on_blocked == "ask" else None)
    r = run_job(**job)
    print(f"\nExport: {r['export']}")
    print("\n".join(r["lines"]))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
