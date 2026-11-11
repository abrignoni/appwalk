#!/usr/bin/env python3
"""Builds a reading-order transcript from an appwalk export: the text of each walked screen, in order,
each line beside the screenshot it was first seen on.

What it does for any app, with no knowledge of the app:
- takes the text nodes inside the scrolling list of each saved view tree, top to bottom;
- joins the frames of a walk into one sequence, using the lines two consecutive frames share, so a
  line that was on three overlapping screenshots appears once;
- records where each line sat (left, right, or across the screen) and what the app's own description
  of it says, if anything.

It does not decide who sent a message unless a profile for the app says how to tell. Without one, the
side of the screen is given as the side of the screen and nothing more.

A profile (profiles/<package>.json) names the view ids of message text and time lines and how a sent
message is marked. Profiles break when an app changes, so each one says which version it was checked
with, and a line matched by a profile still carries the basis for its direction.
"""
import csv
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
LIST_CLASSES = ("RecyclerView", "ListView", "ScrollView", "GridView", "ViewPager", "WebView")
COLUMNS = ["item", "line", "role", "direction", "direction_basis", "side", "text", "app_description", "shown_time",
           "first_seen_step", "screenshot", "resource_id", "bounds_on_that_screenshot", "joined_to_previous_frame"]


def box(n):
    m = BOUNDS.match(n.get("bounds") or "")
    return tuple(map(int, m.groups())) if m else None


def short_id(rid):
    return (rid or "").split("/")[-1]


def container(root, package):
    """The same choice the walker makes: the largest scrolling node, else the largest list-like node,
    else the whole window (a thread that fits on one screen may report no list at all)."""
    best = None
    for test in (lambda n: n.get("scrollable") == "true", lambda n: (n.get("class") or "").endswith(LIST_CLASSES),
                 lambda n: True):
        for n in root.iter("node"):
            b = box(n)
            if n.get("package") != package or not b or not test(n):
                continue
            area = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
            if area and (best is None or area > best[0]):
                best = (area, n, b)
        if best:
            return best[1], best[2]
    return None, None


def side_of(b, cb):
    """Where a line sits across the list: left, right, or across. From its edges, nothing else."""
    width = cb[2] - cb[0]
    if width <= 0:
        return ""
    left_gap, right_gap = (b[0] - cb[0]) / width, (cb[2] - b[2]) / width
    # Across only when it spans the list or sits in its middle. A long bubble nearly fills the width
    # and still leans to one side by a few percent, so the lean decides, however small.
    if (left_gap < 0.04 and right_gap < 0.04) or abs(left_gap - right_gap) < 0.02:
        return "across"
    return "left" if left_gap < right_gap else "right"


def frame_lines(tree_path, package):
    """Text nodes inside the scrolling list of one view tree, top to bottom. Nodes with no area are
    left out: a web view lists text that is not on the screen with empty bounds."""
    root = ET.parse(tree_path).getroot()
    node, cb = container(root, package)
    if node is None:
        return []
    out = []
    for n in node.iter("node"):
        b = box(n)
        if n is node or n.get("package") != package or not n.get("text") or not b:
            continue
        if b[2] <= b[0] or b[3] <= b[1]:
            continue
        out.append({"text": n.get("text"), "desc": n.get("content-desc") or "", "rid": n.get("resource-id") or "",
                    "bounds": b, "side": side_of(b, cb)})
    out.sort(key=lambda x: (x["bounds"][1], x["bounds"][0]))
    return out


def key(line):
    return (line["text"], short_id(line["rid"]))


def join(merged, frame, reveal):
    """Adds one frame to the sequence. The frame overlaps the end it grows from; the longest run they
    share is the join. Returns (new sequence, number of shared lines)."""
    if not merged:
        return list(frame), None
    a, b = [key(x) for x in merged], [key(x) for x in frame]
    if reveal == "above":   # each new frame shows earlier lines, then the top of what is already held
        for k in range(min(len(a), len(b)), 0, -1):
            if b[-k:] == a[:k]:
                return frame[:-k] + merged, k
        return frame + merged, 0
    for k in range(min(len(a), len(b)), 0, -1):
        if a[-k:] == b[:k]:
            return merged + frame[k:], k
    return merged + frame, 0


def load_profile(package, folder=None):
    folder = folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
    p = os.path.join(folder, package + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def classify(line, profile):
    """role, direction, basis. Without a profile every line is plain text and has no direction."""
    if not profile:
        return "text", "", ""
    rid = short_id(line["rid"])
    if rid in profile.get("time_ids", []):
        return "time", "", ""
    if rid in profile.get("ignore_ids", []) or any(line["text"].startswith(p) for p in profile.get("ignore_text_prefixes", [])):
        return "other", "", ""
    if rid not in profile.get("message_ids", []):
        return "other", "", ""
    for rule in profile.get("direction_rules", []):
        if rule["by"] == "description_prefix" and line["desc"].startswith(rule["value"]):
            return "message", rule["direction"], "the app's own description"
        if rule["by"] == "description_contains" and rule["value"] in line["desc"]:
            return "message", rule["direction"], "the app's own description"
        if rule["by"] == "side" and line["side"] == rule["value"]:
            return "message", rule["direction"], "side of the screen (inferred)"
    return "message", "", ""


def build(export, profile_folder=None):
    """Returns (rows, notes). Rows are dicts with COLUMNS as keys."""
    with open(os.path.join(export, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    package = manifest["apps_under_test"][0]["package"]
    profile = load_profile(package, profile_folder)
    # Which way each screenshot's walk was going, and which walk it belongs to, from the log itself.
    walks, current, by_entry = [], None, {}
    with open(os.path.join(export, "actions.jsonl"), encoding="utf-8") as f:
        for raw in f:
            r = json.loads(raw)
            d = json.loads(r["detail"]) if r.get("detail") else {}
            if r["action"] == "walk_start":
                current = {"item": d.get("item"), "reveal": d.get("reveal", "above"), "frames": []}
                walks.append(current)
            elif r["action"] == "screenshot" and current is not None:
                current["frames"].append((d["step"], r["entry_id"], r["screenshot"]["file"], d["tree_file"]))
                by_entry[r["entry_id"]] = d
    rows, notes = [], []
    for w in walks:
        merged, unjoined = [], 0
        for step, entry, png, tree in w["frames"]:
            frame = frame_lines(os.path.join(export, "trees", tree), package)
            for x in frame:
                x.update(step=step, png=png, joined=None)
            before = {id(x) for x in merged}
            merged, shared = join(merged, frame, w["reveal"])
            if shared == 0 and frame:
                unjoined += 1
            for x in merged:
                if id(x) not in before:
                    x["joined"] = shared
        if unjoined:
            notes.append(f"{w['item'] or '(the screen walked)'}: {unjoined} frame(s) shared no line with the one before; "
                         "lines may be missing or repeated there")
        last_time = ""
        for i, x in enumerate(merged, 1):
            role, direction, basis = classify(x, profile)
            if role == "time":
                last_time = x["text"]
            rows.append({"item": w["item"] or "", "line": i, "role": role, "direction": direction, "direction_basis": basis,
                         "side": x["side"], "text": x["text"], "app_description": x["desc"],
                         "shown_time": last_time if role == "message" and profile and profile.get("time_follows_message") is False else "",
                         "first_seen_step": x["step"], "screenshot": x["png"], "resource_id": x["rid"],
                         "bounds_on_that_screenshot": "[%d,%d][%d,%d]" % x["bounds"],
                         "joined_to_previous_frame": "" if x["joined"] is None else ("yes" if x["joined"] else "NO")})
    if profile:
        notes.insert(0, f"Profile for {package} used (checked with {profile.get('checked_with', 'version not stated')}; "
                        f"this export is version {manifest['apps_under_test'][0].get('version_name_at_add')}).")
    else:
        notes.insert(0, f"No profile for {package}: lines are not sorted into messages and other text, and no direction is given.")
    return rows, notes, manifest


def write(export, profile_folder=None):
    rows, notes, manifest = build(export, profile_folder)
    with open(os.path.join(export, "transcript.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    e = html.escape
    out = ["<!doctype html><meta charset='utf-8'><title>Transcript</title><style>",
           "body{font:15px system-ui,sans-serif;margin:24px;color:#111}table{border-collapse:collapse;width:100%;max-width:820px}",
           "td{vertical-align:top;padding:4px 8px;border-top:1px solid #ddd}td.pic{width:240px;border-top:2px solid #888}img{width:220px;border:1px solid #999}",
           ".right{text-align:right}.across{text-align:center;color:#555}.other,.time{color:#666;font-size:13px}",
           ".meta{color:#666;font-size:12px}h2{margin-top:32px}.note{background:#fff3cd;padding:8px;margin:6px 0}</style>",
           f"<h1>Transcript: {e(manifest['apps_under_test'][0]['package'])}</h1>",
           "<p>Text from the view trees the app reported, in reading order, beside the screenshot each line was first "
           "seen on. The screenshots are the record of what was on the screen; this text is what the app reported. "
           "Left and right are where a line sat on the screen. A direction is given only where the note below says how.</p>"]
    out += [f"<p class='note'>{e(n)}</p>" for n in notes]
    # Lines first seen on the same screenshot sit beside it: the picture cell spans those rows.
    groups = []
    for r in rows:
        if groups and groups[-1][0]["item"] == r["item"] and groups[-1][0]["screenshot"] == r["screenshot"]:
            groups[-1].append(r)
        else:
            groups.append([r])
    item = object()
    for g in groups:
        if g[0]["item"] != item:
            if item is not object:
                out.append("</table>")
            item = g[0]["item"]
            out.append(f"<h2>{e(item or 'Screen walked')}</h2><table>")
        for i, r in enumerate(g):
            pic = ""
            if i == 0:
                shot = r["screenshot"]
                pic = (f"<td class='pic' rowspan='{len(g)}'><a href='screens/{e(shot)}'><img src='screens/{e(shot)}' loading='lazy'></a>"
                       f"<div class='meta'>step {r['first_seen_step']}</div></td>")
            label = " · ".join(x for x in (r["direction"], r["direction_basis"]) if x)
            out.append(f"<tr>{pic}<td class='{e(r['side'])} {e(r['role'])}'>{e(r['text'])}"
                       f"<div class='meta'>{e(label)}{' · ' if label and r['app_description'] else ''}{e(r['app_description'])}"
                       f"{' · NOT JOINED to the frame before' if r['joined_to_previous_frame'] == 'NO' else ''}</div></td></tr>")
    out.append("</table>")
    with open(os.path.join(export, "transcript.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return rows, notes


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: transcript.py <export folder>")
    rows, notes = write(sys.argv[1])
    print(f"{len(rows)} lines written to transcript.csv and transcript.html")
    for n in notes:
        print("note:", n)
