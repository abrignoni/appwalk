#!/usr/bin/env python3
"""Checks a walk against a list of texts that should be in it, whatever app was walked.

The texts come from ALEAPP's SMS artifact (--aleapp, one row per text with its number) or from a JSON
file of {"number": ..., "text": ...} records (--expected). A text counts as captured when it is the text
or the description of a node in a saved view tree, or is contained in one. With a walk-list export a
text is looked for only in the trees of the row whose title holds the same phone number digits; with
a one-screen export (--number) in all its trees.

No view ids are used, so the same check works for any app. That also makes it weaker than a check by
id: a text shown only as the list's preview line would count, so list trees are left out.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def digits(s):
    return re.sub(r"\D", "", s or "")[-10:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True)
    ap.add_argument("--aleapp", help="ALEAPP output folder; expected texts are its get_sms_mms rows")
    ap.add_argument("--expected", help="JSON list of records with number and text")
    ap.add_argument("--number", help="one-screen export: the number whose texts are expected")
    ap.add_argument("--prefix", help="only expect texts starting with this")
    a = ap.parse_args()

    expected = {}
    if a.aleapp:
        hit = glob.glob(os.path.join(a.aleapp, "**", "_lava_artifacts.db"), recursive=True)
        db = sqlite3.connect(Path(hit[0]).as_uri() + "?mode=ro", uri=True)
        for addr, body in db.execute("select address, body from get_sms_mms"):
            expected.setdefault(digits(addr), []).append(body)
    else:
        for r in json.load(open(a.expected, encoding="utf-8")):
            expected.setdefault(digits(r["number"]), []).append(r["text"])
    if a.prefix:
        expected = {k: [t for t in v if t.startswith(a.prefix)] for k, v in expected.items()}
        expected = {k: v for k, v in expected.items() if v}

    manifest = json.load(open(os.path.join(a.export, "manifest.json"), encoding="utf-8"))
    package = manifest["apps_under_test"][0]["package"]
    strings = {}   # item -> set of node texts and descriptions
    nodes_with_text = 0
    for t in manifest["view_trees"]:
        item = t.get("item")
        if item is None and not a.number:
            continue   # trees of the list itself
        key = digits(a.number) if a.number else digits(item)
        for n in ET.parse(os.path.join(a.export, "trees", t["file"])).getroot().iter("node"):
            if n.get("package") != package:
                continue
            for v in (n.get("text"), n.get("content-desc")):
                if v:
                    strings.setdefault(key, set()).add(v)
                    nodes_with_text += 1
    shots = manifest["screenshots"]
    print(f"app {package}; screenshots {len(shots)}, blank {sum(1 for s in shots if s['status'] == 'blank')}; "
          f"view trees {len(manifest['view_trees'])}; nodes with text in the walked trees {nodes_with_text}")
    total = found = 0
    for key in sorted(strings):
        want = expected.get(key, [])
        have = strings[key]
        blob = "\n".join(have)
        missing = [w for w in want if w not in have and w not in blob]
        total += len(want)
        found += len(want) - len(missing)
        print(f"   {key}: expected {len(want)}, captured {len(want) - len(missing)}, not captured {len(missing)} {missing[:2]}")
    not_walked = sorted(k for k in expected if k not in strings)
    print(f"total expected in the walked rows {total}, captured {found}, not captured {total - found}")
    print(f"numbers with expected texts that were not walked: {len(not_walked)}")
    return 0 if total and found == total else 1


if __name__ == "__main__":
    sys.exit(main())
