#!/usr/bin/env python3
"""Seeds known data for the prototype on an EMULATOR: one Google Messages thread with numbered texts.

Incoming texts are sent through the emulator console (adb emu sms send). Outgoing texts are typed and
sent through the open thread. Every text carries a token (KD-A-001 ...) so it can be found again in
the app's database, in ALEAPP's output and in the walk's view trees. The list of what was sent is
written as JSON.

For emulators only: `adb emu` does not exist on a phone, and this script types into the app.
"""
import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PKG = "com.google.android.apps.messaging"


def adb(serial, *args):
    p = subprocess.run(["adb", "-s", serial, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
    if p.returncode != 0:
        sys.exit(f"adb {' '.join(args[:3])} failed: {p.stderr.decode('utf-8', 'replace')[:200]}")
    return p.stdout


def tree(serial):
    raw = adb(serial, "exec-out", "uiautomator", "dump", "/dev/tty")
    return ET.fromstring(raw[:raw.rfind(b"</hierarchy>") + 12])


def center(node):
    b = node.get("bounds").replace("][", ",").strip("[]").split(",")
    l, t, r, btm = map(int, b)
    return (l + r) // 2, (t + btm) // 2


def find(root, rid_suffix=None, desc=None):
    for n in root.iter("node"):
        if rid_suffix and n.get("resource-id", "").endswith(rid_suffix):
            return n
        if desc and n.get("content-desc") == desc:
            return n
    return None


def body(prefix, i):
    base = f"{prefix}-{i:03d} test message number {i}"
    if i % 7 == 0:
        base += ". This one is longer on purpose so the bubble takes several lines on the screen and the items in the list do not all have the same height."
    return base


def send_out(serial, text):
    root = tree(serial)
    field = find(root, rid_suffix="/compose_message_text")
    if field is None:
        sys.exit("compose field not found: is the thread open?")
    x, y = center(field)
    adb(serial, "shell", f"input tap {x} {y}")
    time.sleep(0.8)
    adb(serial, "shell", "input text '" + text.replace(" ", "%s") + "'")
    time.sleep(0.8)
    root = tree(serial)
    send = None
    for kw in ({"desc": "Send SMS"}, {"desc": "Send message"}, {"rid_suffix": "/send_message_button_icon"}):
        send = find(root, **kw)
        if send is not None:  # an Element with no children is falsy, so "or" cannot be used here
            break
    if send is None:
        labels = sorted({n.get("content-desc") for n in root.iter("node") if n.get("content-desc")})
        sys.exit("send button not found; descriptions on screen: " + "; ".join(labels))
    x, y = center(send)
    adb(serial, "shell", f"input tap {x} {y}")
    time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--serial", required=True)
    ap.add_argument("--number", default="5551230001")
    ap.add_argument("--prefix", default="KD-A")
    ap.add_argument("--first", type=int, required=True)
    ap.add_argument("--last", type=int, required=True)
    ap.add_argument("--out-every", type=int, default=0, help="type and send an outgoing text after every Nth incoming one (thread must be open)")
    ap.add_argument("--record", required=True, help="JSON file to append what was sent to")
    a = ap.parse_args()
    if not a.serial.startswith("emulator-"):
        sys.exit("emulators only")
    try:
        old = json.load(open(a.record, encoding="utf-8"))
    except FileNotFoundError:
        old = []
    sent = []

    def record(direction, text):
        # Written after every text, so a failure part way leaves a true record of what went out.
        sent.append({"direction": direction, "number": a.number, "text": text})
        json.dump(old + sent, open(a.record, "w", encoding="utf-8"), indent=1)

    for i in range(a.first, a.last + 1):
        text = body(a.prefix, i)
        adb(a.serial, "emu", "sms", "send", a.number, text)
        record("in", text)
        time.sleep(0.5)
        if a.out_every and i % a.out_every == 0:
            out = f"{a.prefix}-OUT-{i:03d} reply after {i}"
            send_out(a.serial, out)
            record("out", out)
    print(f"sent {len(sent)} ({sum(1 for s in sent if s['direction'] == 'out')} outgoing); record has {len(old) + len(sent)}")


if __name__ == "__main__":
    main()
