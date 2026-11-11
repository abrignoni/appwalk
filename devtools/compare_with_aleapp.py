#!/usr/bin/env python3
"""Checks a walk against ALEAPP's output for the same phone, and lists what changed between extractions.

Part 1, completeness: every message ALEAPP's GoogleMessages artifact reports for the walked
conversation must appear, as the text of a message bubble, in at least one view tree the walk saved.

Part 2, changes: for each extraction in order, the read state of the texts (ALEAPP's SMS artifact and
the bugle_db `messages.read` and `seen` columns as stored), the usage records for the app, the
recent-task record, and the adb key file.

Usage:
    compare_with_aleapp.py --export EXPORT --conversation "(555) 123-0001" \
        --aleapp LABEL=ALEAPP_OUTPUT_DIR[,STAGED_DIR] [--aleapp ...]
The last --aleapp is the one the walk is checked against.
"""
import argparse
import csv
import glob
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PKG = "com.google.android.apps.messaging"


def rid_is(row, name):
    """The same app gave message bubbles the id "<package>:id/message_text" in one session and plain
    "message_text" in the next (seen on the emulator, same app version), so both forms are accepted."""
    return row["resource_id"] == name or row["resource_id"].endswith("/" + name)


def lava(out_dir):
    hits = glob.glob(os.path.join(out_dir, "**", "_lava_artifacts.db"), recursive=True)
    if not hits:
        sys.exit(f"no _lava_artifacts.db under {out_dir}")
    return sqlite3.connect(Path(hits[0]).as_uri() + "?mode=ro", uri=True)


def has_table(db, name):
    return db.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone() is not None


def bugle_counts(staged):
    """Reads bugle_db from a scratch copy (with its -wal), so the staged file is never opened in place."""
    src = glob.glob(os.path.join(staged, "**", PKG, "databases", "bugle_db"), recursive=True)
    if not src:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(src[0] + suffix):
                shutil.copy2(src[0] + suffix, os.path.join(tmp, "bugle_db" + suffix))
        db = sqlite3.connect(os.path.join(tmp, "bugle_db"))
        cols = {r[1] for r in db.execute("pragma table_info(messages)")}
        out = {}
        if {"read", "seen", "conversation_id"} <= cols:
            for conv, n, read, seen in db.execute(
                    "select conversation_id, count(*), sum(read), sum(seen) from messages group by conversation_id order by conversation_id"):
                out[conv] = {"messages": n, "read": read, "seen": seen}
        db.close()
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True)
    ap.add_argument("--conversation", help="the conversation name as ALEAPP reports it, for an export of one walk. "
                    "Leave it out for a walk-list export: each opened row is then checked against the conversation of the same name")
    ap.add_argument("--aleapp", action="append", required=True)
    a = ap.parse_args()
    runs = []
    for item in a.aleapp:
        label, rest = item.split("=", 1)
        out, _, staged = rest.partition(",")
        runs.append((label, out, staged or None))

    # Part 1
    label, out, _ = runs[-1]
    db = lava(out)
    cols = [r[1] for r in db.execute("pragma table_info(get_googlemessages)")]
    conv_col = next(c for c in cols if "conversation_name" in c)
    if not a.conversation:
        per_item(a.export, db, conv_col, label)
        changes(runs)
        return
    rows = db.execute(f'select message, direction, message_timestamp from get_googlemessages where "{conv_col}" = ?',
                      (a.conversation,)).fetchall()
    others = db.execute(f'select count(*) from get_googlemessages where "{conv_col}" != ?', (a.conversation,)).fetchone()[0]
    seen = {}
    with open(os.path.join(a.export, "text_index.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if rid_is(r, "message_text"):
                seen.setdefault(r["text"], set()).add(int(r["step"]))
    missing = [m for m, _, _ in rows if m not in seen]
    not_in_aleapp = [t for t in seen if t not in {m for m, _, _ in rows}]
    print("== Completeness, against ALEAPP on", label)
    print(f"ALEAPP GoogleMessages rows for {a.conversation}: {len(rows)} "
          f"({sum(1 for r in rows if 'out' in (r[1] or '').lower() or 'sent' in (r[1] or '').lower())} outgoing by its direction column); "
          f"rows for other conversations: {others}")
    print(f"distinct message bubbles in the walk's view trees: {len(seen)}")
    print(f"ALEAPP messages not found in any view tree: {len(missing)} {missing[:5]}")
    print(f"bubbles in the view trees that ALEAPP does not report for this conversation: {len(not_in_aleapp)} {not_in_aleapp[:5]}")
    times = sorted(len(v) for v in seen.values())
    print(f"times each message was captured: min {times[0]}, median {times[len(times) // 2]}, max {times[-1]}")
    directions = sorted({r[1] for r in rows})
    print("direction values as ALEAPP reports them:", directions)

    changes(runs)


def per_item(export, db, conv_col, label):
    """A walk-list export: each opened row against ALEAPP's rows for the conversation of that name."""
    bubbles, snippets = {}, {}
    with open(os.path.join(export, "text_index.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if rid_is(r, "message_text"):
                bubbles.setdefault(r["item"], set()).add(r["text"])
            elif rid_is(r, "conversation_snippet"):
                snippets.setdefault(r["item"], set()).add(r["text"])
    by_conv = {}
    for conv, msg in db.execute(f'select "{conv_col}", message from get_googlemessages'):
        by_conv.setdefault(conv, []).append(msg)
    print("== Completeness per opened row, against ALEAPP on", label)
    print(f"conversations ALEAPP reports: {len(by_conv)}, with {sum(len(v) for v in by_conv.values())} messages; rows the walk opened: {len([k for k in bubbles if k])}")
    total_missing = total_extra = 0
    for item in sorted(k for k in bubbles if k):
        want = by_conv.get(item, [])
        missing = [m for m in want if m not in bubbles[item]]
        extra = [b for b in bubbles[item] if b not in set(want)]
        total_missing += len(missing)
        total_extra += len(extra)
        print(f"   {item}: ALEAPP {len(want)}, bubbles captured {len(bubbles[item])}, not captured {len(missing)}, captured but not in ALEAPP {len(extra)}"
              + (f" {sorted(extra)[:3]}" if extra else ""))
    print(f"totals: not captured {total_missing}, captured but not in ALEAPP for that conversation {total_extra}")
    not_opened = sorted(c for c in by_conv if c not in bubbles)
    all_bubbles = set().union(*bubbles.values()) if bubbles else set()
    leaked = sum(1 for c in not_opened for m in by_conv[c] if m in all_bubbles)
    print(f"conversations not opened: {len(not_opened)}; their messages found as message bubbles anywhere: {leaked}")
    listed = set().union(*snippets.values()) if snippets else set()
    shown = sum(1 for c in not_opened if any(s and any(m.startswith(s.rstrip("…. ")[:40]) for m in by_conv[c]) for s in listed))
    print(f"conversations not opened whose latest line shows in the pictures of the list: {shown} (0 when the list was not captured)")


def changes(runs):
    # Part 2
    print("\n== Changes across extractions")
    for label, out, staged in runs:
        db = lava(out)
        print(f"-- {label}")
        if has_table(db, "get_sms_mms"):
            for addr, n, read in db.execute("select address, count(*), sum(case when read in (1, '1', 'Read', 'read') then 1 else 0 end) "
                                            "from get_sms_mms group by address order by address"):
                vals = [v[0] for v in db.execute("select distinct read from get_sms_mms where address = ?", (addr,))]
                print(f"   SMS provider (ALEAPP): {addr}: {n} rows, read values as reported {vals}, counted read {read}")
        if staged:
            b = bugle_counts(staged)
            if b:
                for conv, v in b.items():
                    print(f"   bugle_db messages, conversation {conv}: {v['messages']} rows, sum(read) {v['read']}, sum(seen) {v['seen']}")
        if has_table(db, "get_usagestats"):
            n, last = db.execute("select count(*), max(timestamp_last_time_active) from get_usagestats where package = ?", (PKG,)).fetchone()
            print(f"   usage stats rows for {PKG}: {n}, latest time {last}")
            kinds = db.execute("select event_type, count(*) from get_usagestats where package = ? group by event_type order by 2 desc", (PKG,)).fetchall()
            print("     by event type:", kinds[:8])
            shell = db.execute("select package, count(*) from get_usagestats where package like '%shell%' or package like '%uiautomator%' group by package").fetchall()
            print("     rows for shell or uiautomator packages:", shell)
        if has_table(db, "get_recentactivity"):
            for r in db.execute("select real_activity, first_active_time, last_active_time, last_time_moved from get_recentactivity"):
                print("   recent task:", r)
        if has_table(db, "adb_hosts"):
            print("   adb hosts:", db.execute("select count(*) from adb_hosts").fetchone()[0], "row(s)")
        if staged:
            for p in sorted(glob.glob(os.path.join(staged, "data", "misc", "adb", "*"))):
                print(f"   {os.path.relpath(p, staged)}: {os.path.getsize(p)} bytes, sha256 {hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]}")


if __name__ == "__main__":
    main()
