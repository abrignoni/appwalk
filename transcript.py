#!/usr/bin/env python3
"""Builds a reading-order transcript from an AppWalk export: the text of each walked screen, in order,
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


def frame_lines(tree_path, package, profile=None):
    """Text nodes inside the scrolling list of one view tree, top to bottom. Nodes with no area are
    left out: a web view lists text that is not on the screen with empty bounds.

    A profile can also name views that carry only a description (a picture, for example) in
    described_ids. Each becomes a line whose text is that description, marked as described. A named
    view inside another named one is left out, so a picture is one line however deeply it is nested."""
    root = tree_path if hasattr(tree_path, "iter") else ET.parse(tree_path).getroot()
    node, cb = container(root, package)
    if node is None:
        return []
    described = set((profile or {}).get("described_ids", []))
    bubbles = set((profile or {}).get("bubble_ids", []))
    # Apps whose views carry no ids can still say what a box is, in its description (Threads on a Pixel 7a,
    # 13 November 2026: "Chat text message" around each text bubble, "Photo preview" on a picture). A
    # profile can name those descriptions: text inside such a box is a message, and the box's side is the
    # message's side; a box described as media becomes one line holding its description.
    msg_descs = set((profile or {}).get("message_box_descriptions", []))
    media_descs = set((profile or {}).get("described_box_descriptions", []))
    msg_boxes = sorted({box(n) for n in node.iter("node") if msg_descs and (n.get("content-desc") or "") in msg_descs and box(n)},
                       key=lambda x: x[1])
    # A reply shows the message it answers just above it, in a box with the same description, drawn so it
    # runs under the reply's box. Bubbles of their own never overlap (Threads, Pixel 7a: 2 of 2 quotes
    # overlapped the reply by 42 px, 0 of 33 other message boxes overlapped anything), so a box that runs
    # into the next one is a quote, and its text is not a message.
    msg_boxes = [x for i, x in enumerate(msg_boxes) if not any(y[1] < x[3] and y[1] > x[1] for y in msg_boxes[i + 1:])]
    parent = {ch: p for p in node.iter() for ch in p}
    taken = set()
    media_taken = []
    out = []
    for n in node.iter("node"):
        b = box(n)
        if n is node or n.get("package") != package or not b:
            continue
        if b[2] <= b[0] or b[3] <= b[1]:
            continue
        rid = n.get("resource-id") or ""
        up = parent.get(n)
        parent_rid = (up.get("resource-id") or "") if up is not None else ""
        # The side of a message is the side of its bubble where the profile names one: short text in a wide
        # bubble (a reply, whose quote widens it) sits at the bubble's far edge, not the screen's.
        sb, q = b, up
        while bubbles and q is not None and q is not node:
            if short_id(q.get("resource-id") or "") in bubbles and box(q):
                sb = box(q)
                break
            q = parent.get(q)
        in_box = next((x for x in msg_boxes if x[0] <= b[0] and x[1] <= b[1] and b[2] <= x[2] and b[3] <= x[3]), None)
        if in_box:
            sb = in_box
        if n.get("text"):
            out.append({"text": n.get("text"), "desc": n.get("content-desc") or "", "rid": rid, "parent_rid": parent_rid,
                        "bounds": b, "side": side_of(sb, cb), "described": False, "in_message_box": bool(in_box),
                        "bubble": sb if sb is not b else None})
        elif n.get("content-desc") and n.get("content-desc") in media_descs:
            # One line per box: Threads repeats a description on a nested view of the same size, and puts
            # "Video preview" inside a shared post that is already a line.
            if any(x[0] <= b[0] and x[1] <= b[1] and b[2] <= x[2] and b[3] <= x[3] for x in media_taken):
                continue
            # A box cut by the list's edge is taken from the screenshot that shows it whole: cut off, its
            # line sits above text the other screenshot holds, and the two no longer line up to be joined.
            if cb and (b[1] <= cb[1] or b[3] >= cb[3]):
                continue
            media_taken.append(b)
            out.append({"text": n.get("content-desc"), "desc": n.get("content-desc"), "rid": rid, "parent_rid": parent_rid,
                        "bounds": b, "side": side_of(sb, cb), "described": True, "described_by_box": True})
        elif n.get("content-desc") and short_id(rid) in described:
            q, nested = up, False
            while q is not None:
                if id(q) in taken:
                    nested = True
                    break
                q = parent.get(q)
            taken.add(id(n))
            if not nested:
                out.append({"text": n.get("content-desc"), "desc": n.get("content-desc"), "rid": rid, "parent_rid": parent_rid,
                            "bounds": b, "side": side_of(sb, cb), "described": True, "bubble": sb if sb is not b else None})
    out.sort(key=lambda x: (x["bounds"][1], x["bounds"][0]))
    return out


def sides_from_edges(lines, tol=6):
    """Gives a side to bubbles side_of placed across, from the edge they line up with.

    A chat app lines its bubbles up on one edge per side, whatever their width: on WhatsApp the sent ones
    end the same distance from the right (2.7% of the list on a moto g47, 3.3% on a Samsung SM-G990E) and
    the received ones start the same distance from the left (10.2% and 12.5%; in a group the sender's
    picture sits before them). A long received bubble then sits near the middle and side_of calls it
    across (moto g47: 10.2% from the left, 11.8% from the right). Here the edges are learned from the
    bubbles of the same walk that side_of did place: the left edge most left bubbles start at, the right
    edge most right ones end at, each shared by two bubbles or more. An across bubble that starts at the
    left edge and does not end at the right one is left, and the other way round. Returns how many
    bubbles were placed."""
    from collections import Counter
    def edge(side, i):
        c = Counter(b[i] for b in {tuple(x["bubble"]) for x in lines if x.get("bubble") and x["side"] == side})
        best = c.most_common(1)
        return best[0][0] if best and best[0][1] >= 2 else None
    left, right = edge("left", 0), edge("right", 2)
    moved = set()
    for x in lines:
        if x["side"] != "across" or not x.get("bubble") or left is None or right is None:
            continue
        at_left, at_right = abs(x["bubble"][0] - left) <= tol, abs(x["bubble"][2] - right) <= tol
        if at_left != at_right:
            x["side"] = "left" if at_left else "right"
            moved.add(tuple(x["bubble"]))
    return len(moved)


def key(line):
    return (line["text"], short_id(line["rid"]))


def text_shift(prev, cur, list_bounds, reveal, tol=6):
    """Whether the text of two frames says the second continues the first, from where the lines sit.

    prev and cur are frame_lines output. One line of text on both frames is not enough: a title that
    stays put while the list scrolls, an author's name in a group or a voice note's "0:07" can be on two
    frames that share nothing (reported by a tester on Samsung and Motorola phones, 14 November 2026). So:
    - a line at the same place on both frames did not scroll with the list and is left out (fixed);
    - only lines found once on each frame are used to find the shift, and it must go the way the walk
      scrolls (reveal above: the old lines move down), or the other way by under 5% of the list;
    - the shift most of them agree on is checked against every line of the old frame that it would
      still keep inside the list: each found there on the new frame agrees, each missing disagrees.
    - when no line moved and most of the old frame's lines are where they were, the screen did not move:
      shift 0, and those lines agree.
    Returns {"shift", "agree", "disagree", "fixed", "fixed_keys"}; shift is None when no line moved the
    right way. text_joins says whether that is enough."""
    def keyed(lines):
        out = {}
        for x in lines:
            if not x.get("described"):
                out.setdefault(key(x), []).append(x["bounds"])
        return out
    a, b = keyed(prev), keyed(cur)
    near = lambda u, v: abs(u - v) <= tol
    # Fixed means not moved at all. A list at its end can move by only a few pixels (LinkedIn, Pixel 7a:
    # 18 lines moved 6 px), and those lines did scroll.
    fixed = {k for k in a.keys() & b.keys()
             if any(abs(p[1] - q[1]) <= 1 and abs(p[3] - q[3]) <= 1 for p in a[k] for q in b[k])}
    sign = 1 if reveal == "above" else -1
    # A small move the other way is a list settling back at its end (LinkedIn, Pixel 7a: 18 lines 6 px
    # up on a walk that reveals above); the frames then share nearly everything, so it joins too.
    small = (list_bounds[3] - list_bounds[1]) * 5 // 100 if list_bounds else 0
    votes = []
    for k in a.keys() & b.keys():
        if k in fixed or len(a[k]) != 1 or len(b[k]) != 1:
            continue
        p, q = a[k][0], b[k][0]
        # A line cut by the list's edge keeps one edge in place, so either edge can carry the shift.
        votes.append({d for d in (q[1] - p[1], q[3] - p[3]) if d * sign > 1 or 1 < abs(d) <= small})
    cands = sorted({d for v in votes for d in v})
    best, best_n = None, 0
    for d in cands:
        n = sum(1 for v in votes if any(near(d, e) for e in v))
        if n > best_n:
            best, best_n = d, n
    if best is None:
        # Nothing moved: most of the old frame is where it was (a list at its end, read again after a
        # time on it changed: WhatsApp, Pixel 7a, 15 of 22 lines in place). That is the same screen. One
        # title in place among lines that changed is not.
        if len(fixed) >= 2 and len(fixed) * 2 >= len(a):
            return {"shift": 0, "agree": len(fixed), "disagree": len(a) - len(fixed), "fixed": len(fixed), "fixed_keys": fixed}
        return {"shift": None, "agree": 0, "disagree": 0, "fixed": len(fixed), "fixed_keys": fixed}
    top, bottom = (list_bounds[1], list_bounds[3]) if list_bounds else (None, None)
    agree = disagree = 0
    for k, boxes in a.items():
        if k in fixed:
            continue
        for p in boxes:
            t, btm = p[1] + best, p[3] + best
            if top is not None and (t < top or btm > bottom):
                continue  # moved off the list, or only partly on it
            if any(near(t, q[1]) or near(btm, q[3]) for q in b.get(k, [])):
                agree += 1
            else:
                disagree += 1
    return {"shift": best, "agree": agree, "disagree": disagree, "fixed": len(fixed), "fixed_keys": fixed}


def text_joins(shift):
    """Two lines or more that moved together, and more lines agreeing than not. One line is not enough.

    Measured on 247 neighbouring screenshot pairs from walks of seven apps on a Pixel 7a and an emulator,
    14 November 2026: of the 86 pairs the one-line rule joined by text, 57 join by two lines or more and
    28 by the screenshots; one, joined on a single 3-character line cut by the list's edge, does not.
    On 241 pairs three steps apart, which mostly share nothing, this rule joins 60 (the one-line rule
    96): 59 are shown to overlap, by the screenshots or by the shifts of the steps between adding up to
    the same distance, and 1 could not be checked either way."""
    return shift["agree"] >= 2 and shift["agree"] > shift["disagree"]


def join(merged, frame, reveal):
    """Adds one frame to the sequence. Returns (new sequence, number of shared lines).

    The new frame overlaps the end the sequence grows from. Lines can change between two screenshots
    (a relative time such as "2 min" becomes "3 min"), and a line cut off at the frame's edge can be in
    one frame and not the other, so the overlap is found by alignment, not by an exact match: the
    longest run of lines the two share, starting near the edge they meet at. Lines in the overlap that
    changed keep the version already held."""
    if not merged:
        return list(frame), None
    if reveal != "above":
        out, shared = join(list(reversed(merged)), list(reversed(frame)), "above")
        return list(reversed(out)), shared
    # Reveal above: the new frame shows earlier lines, then the top of what is already held. Try every place
    # the held sequence could start in the frame; score each by lines that agree minus lines that do not,
    # position by position. A changed line or one cut off at the edge costs one point instead of the join.
    a, b = [key(x) for x in merged], [key(x) for x in frame]
    best, best_score, best_shared = None, 0, 0
    for d in range(len(b)):
        n = min(len(b) - d, len(a))
        agree = sum(1 for i in range(n) if b[d + i] == a[i])
        score = agree - (n - agree)
        if agree and score > best_score:
            best, best_score, best_shared = d, score, agree
    if best is None:
        return frame + merged, 0
    return frame[:best] + merged, best_shared


USER_PROFILES = os.path.join(os.path.expanduser("~"), ".appwalk", "profiles")


def load_profile(package, folder=None):
    """The profile for package: from folder if one is given; otherwise a taught one in ~/.appwalk/profiles,
    then the one that ships with AppWalk. The file it came from is kept in "_from"."""
    folders = [folder] if folder else [USER_PROFILES, os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")]
    for d in folders:
        p = os.path.join(d, package + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                prof = json.load(f)
            prof["_from"] = "taught on this computer" if os.path.abspath(d) == os.path.abspath(USER_PROFILES) else "shipped with AppWalk"
            return prof
    return None


def classify(line, profile):
    """role, direction, basis. Without a profile every line is plain text and has no direction."""
    if not profile:
        return "text", "", ""
    rid = short_id(line["rid"])
    if rid in profile.get("time_ids", []):
        return "time", "", ""
    if rid in profile.get("ignore_ids", []) or any(line["text"].startswith(p) for p in profile.get("ignore_text_prefixes", [])):
        return "other", "", ""
    if rid in profile.get("author_ids", []):
        return "author", "", ""
    role = "described" if line.get("described") else "message"
    # A message view with no id of its own, known by its parent's id (Discord names no message text view).
    unnamed_message = not rid and short_id(line.get("parent_rid", "")) in profile.get("message_parent_ids", [])
    # Text with no view id and no named parent, known only by where it sits and how tall its box is
    # (Instagram 455 on a Pixel 7a: plain text bubbles have no id; on the left or right and at least
    # 94 px tall in a known-data chat, while names, labels and the reel's account line were 52 to 68 px
    # or centred). Opt-in, per profile, because it is a shape, not something the app names.
    unnamed_message = unnamed_message or bool(line.get("in_message_box"))
    least = profile.get("unnamed_message_min_height")
    if not unnamed_message and least and not rid and line["side"] in ("left", "right") and line.get("bounds"):
        b = line["bounds"]
        unnamed_message = (b[3] - b[1]) >= least
    if role == "message" and rid not in profile.get("message_ids", []) and not unnamed_message:
        return "other", "", ""
    for rule in profile.get("direction_rules", []):
        if rule["by"] == "description_prefix" and line["desc"].startswith(rule["value"]) and role == "message":
            return role, rule["direction"], "the app's own description"
        if rule["by"] == "description_contains" and rule["value"] in line["desc"] and role == "message":
            return role, rule["direction"], "the app's own description"
        if rule["by"] == "side" and line["side"] == rule["value"]:
            return role, rule["direction"], "side of the screen (inferred)"
    return role, "", ""


def picture_overlap(prev_png, cur_png, list_bounds, reveal):
    """How many pixels of the list two screenshots share, measured from the pictures, or None when no
    clear overlap is found or Pillow is missing.

    For screens whose shared part holds no text (a photo, a sticker), the text join has nothing to
    work with. The list area of both pictures is shrunk, and the shift at which the old picture lines
    up with the new one is searched for. The top and bottom 7% of the list (floating labels, buttons
    over the list) and a strip at the right edge (the scrollbar) are left out. A match counts only
    when the shared part is at least 5% of the list's height and is not flat (a blank area lines up at
    any shift), the best shift differs on average by under 12 grey levels out of 255, and it is less
    than half the difference at any shift more than 10 rows away (a real overlap is one sharp best).

    The limits were set on Signal on a Pixel 7a, 12 November 2026: 67 of 69 neighbouring screenshot
    pairs were confirmed, and 0 of 129 pairs two or three steps apart, which share nothing. Other apps
    were not measured."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return None
    import io
    opened = lambda x: Image.open(io.BytesIO(x) if isinstance(x, (bytes, bytearray)) else x)   # a path, or the bytes the walker holds
    try:
        a, b = opened(prev_png).convert("L"), opened(cur_png).convert("L")
    except OSError:
        return None
    if a.size != b.size:
        return None
    l, t, r, btm = list_bounds
    h = btm - t
    t, btm = t + h * 7 // 100, btm - h * 7 // 100
    r -= max(4, a.width * 15 // 1000)
    if r - l < 40 or btm - t < 100:
        return None
    scale = 4
    size = ((r - l) // scale, (btm - t) // scale)
    a = a.crop((l, t, r, btm)).resize(size)
    b = b.crop((l, t, r, btm)).resize(size)
    w, hh = size
    least = max(2, hh * 5 // 100)
    tried = []
    for s in range(1, hh - least):
        # Reveal above: older content comes in at the top, so the old picture's top now sits s rows lower.
        old, new = (a.crop((0, 0, w, hh - s)), b.crop((0, s, w, hh))) if reveal == "above" else \
                   (a.crop((0, s, w, hh)), b.crop((0, 0, w, hh - s)))
        if ImageStat.Stat(old).stddev[0] < 4:
            continue
        tried.append((ImageStat.Stat(ImageChops.difference(old, new)).mean[0], s))
    if not tried:
        return None
    best = min(tried)
    elsewhere = [d for d, s in tried if abs(s - best[1]) > 10]
    if best[0] >= 12 or not elsewhere or best[0] >= 0.5 * min(elsewhere):
        return None
    return (hh - best[1]) * scale


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
                current.setdefault("screen_step", {})[d.get("screen_in_this_walk")] = d["step"]
                by_entry[r["entry_id"]] = d
            elif r["action"] == "continuity_gap" and current is not None and d.get("going_back_to_retry"):
                # The walker found this screen did not continue the one before, went back and came forward
                # again in smaller moves. The screen it went back from is not part of the sequence.
                current.setdefault("retried_screens", set()).add(d.get("before_screen"))
    rows, notes = [], []
    for w in walks:
        merged, unjoined, by_picture, prev_png, prev_frame = [], 0, 0, None, None
        retried = {w.get("screen_step", {}).get(n) for n in w.get("retried_screens", set())} - {None}
        set_aside = []
        for step, entry, png, tree in w["frames"]:
            frame = frame_lines(os.path.join(export, "trees", tree), package, profile)
            for x in frame:
                x.update(step=step, png=png, joined=None)
            if step in retried:
                set_aside.append((step, frame))
                continue
            before = {id(x) for x in merged}
            if prev_frame is None:
                merged, shared = join(merged, frame, w["reveal"])
            else:
                root = ET.parse(os.path.join(export, "trees", tree)).getroot()
                _, cb = container(root, package)
                ts = text_shift(prev_frame, frame, cb, w["reveal"])
                # A line that did not move with the list (a title inside it) is held once, from the first frame.
                prev_boxes = {(key(x), x["bounds"][1], x["bounds"][3]) for x in prev_frame}
                kept = frame if ts["shift"] == 0 else \
                    [x for x in frame if not (key(x) in ts["fixed_keys"] and (key(x), x["bounds"][1], x["bounds"][3]) in prev_boxes)]
                px = None
                if not (text_joins(ts) or not frame):
                    px = picture_overlap(os.path.join(export, "screens", os.path.basename(prev_png)),
                                         os.path.join(export, "screens", os.path.basename(png)), cb, w["reveal"]) if prev_png and cb else None
                if text_joins(ts) or px or not frame:
                    merged, shared = join(merged, kept, w["reveal"])
                    if px:
                        shared = "picture"
                        by_picture += 1
                else:
                    # Not shown to continue: placed beyond what is held, not aligned on a line both happen to show.
                    merged, shared = (kept + merged if w["reveal"] == "above" else merged + kept), 0
                    unjoined += 1
            prev_png, prev_frame = png, frame
            for x in merged:
                if id(x) not in before:
                    x["joined"] = shared
        for step, frame in set_aside:
            held = {key(x) for x in merged}
            alone = [x for x in frame if key(x) not in held]
            tail = (f". {len(alone)} line(s) seen only on S{step} are listed after this walk's other lines" if alone
                    else ", and every line on it was seen again on the screenshots after it")
            notes.append(f"{w['item'] or '(the screen walked)'}: screenshot S{step} shared nothing with the one before, so "
                         f"the walk went back and came forward again in smaller moves; S{step} is not used to order the "
                         f"lines{tail}")
            for x in alone:
                x["joined"] = 0
            merged = merged + alone
        if unjoined:
            notes.append(f"{w['item'] or '(the screen walked)'}: {unjoined} frame(s) not shown to continue the one before "
                         "(no two lines of text moved together and the screenshots did not clearly overlap); "
                         "lines may be missing or repeated there")
        if by_picture:
            notes.append(f"{w['item'] or '(the screen walked)'}: {by_picture} frame(s) shared too little text with the one before; "
                         "the screenshots show the two overlap, so those frames were joined by the pictures")
        lined_up = sides_from_edges(merged)
        if lined_up:
            notes.append(f"{w['item'] or '(the screen walked)'}: {lined_up} bubble(s) sat near the middle of the list; each was "
                         "given the side of the edge it lines up with, the edge the other bubbles of that side share")
        last_time = ""
        for i, x in enumerate(merged, 1):
            role, direction, basis = classify(x, profile)
            if profile and profile.get("single_column") and role in ("message", "author", "described"):
                x["side"] = "left"
            if role == "time":
                last_time = x["text"]
            rows.append({"item": w["item"] or "", "line": i, "role": role, "direction": direction, "direction_basis": basis,
                         "side": x["side"], "text": x["text"], "app_description": x["desc"],
                         "shown_time": last_time if role == "message" and profile and profile.get("time_follows_message") is False else "",
                         "first_seen_step": x["step"], "screenshot": x["png"], "resource_id": x["rid"],
                         "bounds_on_that_screenshot": "[%d,%d][%d,%d]" % x["bounds"],
                         "joined_to_previous_frame": "" if x["joined"] is None else (
                             "by the screenshots" if x["joined"] == "picture" else "yes" if x["joined"] else "NO")})
    if profile and profile.get("single_column"):
        notes.insert(0, "This app shows every message in one column, whoever wrote it, so its lines are placed on the "
                        "left and the side column does not say where they sat. Who wrote a line is the bold author line before it.")
    if profile:
        notes.insert(0, f"Profile for {package} used, {profile.get('_from', 'from the folder given')} (checked with {profile.get('checked_with', 'version not stated')}; "
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
    out = ["<!doctype html><meta charset='utf-8'><title>AppWalk transcript</title><style>",
           "body{font:15px system-ui,sans-serif;margin:24px;color:#111}table{border-collapse:collapse;width:100%;max-width:820px}",
           "td{vertical-align:top;padding:4px 8px;border-top:1px solid #ddd}td.pic{width:240px;border-top:2px solid #888}img{width:220px;border:1px solid #999}",
           ".right{text-align:right}.across{text-align:center;color:#555}.other,.time{color:#666;font-size:13px}"
           ".author{font-weight:600;font-size:13px}.described{font-style:italic;color:#444}",
           ".meta{color:#666;font-size:12px}h2{margin-top:32px}.note{background:#fff3cd;padding:8px;margin:6px 0}</style>",
           f"<h1>AppWalk transcript: {e(manifest['apps_under_test'][0]['package'])}</h1>",
           "<p>Text from the view trees the app reported, in reading order, beside the screenshot each line was first "
           "seen on. The screenshots are the record of what was on the screen; this text is what the app reported. "
           "Left and right are where a line sat on the screen. A direction is given only where the note below says how. "
           "A line in square brackets is the description the app gave an item with no text, such as a picture.</p>"]
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
            shown = f"[{r['text']}]" if r["role"] == "described" else r["text"]
            # The app's description only where it says more than the line (a time's description is the time).
            desc = r["app_description"] if r["app_description"].strip() not in (r["text"].strip(), shown.strip()) else ""
            out.append(f"<tr>{pic}<td class='{e(r['side'])} {e(r['role'])}'>{e(shown)}"
                       f"<div class='meta'>{e(label)}{' · ' if label and desc else ''}{e(desc)}"
                       f"{' · NOT JOINED to the frame before' if r['joined_to_previous_frame'] == 'NO' else ''}"
                       f"{' · joined to the frame before by the screenshots (no shared text)' if r['joined_to_previous_frame'] == 'by the screenshots' else ''}</div></td></tr>")
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
