#!/usr/bin/env python3
"""Teach AppWalk an app: a profile made from a few clicks on an export's screenshots, not written by hand.

The examiner clicks, on any screenshot of the export, a message they know was sent from this phone, one
they know was received, a time, and if the app shows them, a picture and the name of who wrote a message.
For each click the saved view tree of that screenshot says which view was there. The profile is made from
those views:

- message text: the view's id, or, where the app gives message text no id (Discord), its parent's id
- the bubble: the outermost named view around the message that is narrower than the list; the side of a
  message is then the side of its bubble (a short reply in a wide bubble sits at the bubble's far edge)
- sent and received: the side each clicked message is on; when both are on the same side, the app shows
  every message in one column and no direction is claimed
- a picture: the outermost named view around the click that carries a description
- a time and an author name: the view's id

Nothing is guessed beyond what was clicked: a step that was skipped, or a click on a view with no id,
leaves that part out of the profile, and the warnings say so.

Taught profiles are saved in ~/.appwalk/profiles/, outside the program, and are used before the ones that
ship with it.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcript as T  # noqa: E402

USER_PROFILES = T.USER_PROFILES
STEPS = [
    ("sent", "a message you know was sent from this phone", True),
    ("received", "a message you know was received", True),
    ("time", "a time shown with a message", True),
    ("picture", "a picture or other attachment, if there is one", False),
    ("author", "the name of who wrote a message, if the app shows names", False),
]


def screenshots(export):
    """(step, png path, tree path) for every screenshot of the export, in capture order."""
    out = []
    with open(os.path.join(export, "actions.jsonl"), encoding="utf-8") as f:
        for raw in f:
            r = json.loads(raw)
            if r["action"] == "screenshot":
                d = json.loads(r["detail"])
                out.append((d["step"], os.path.join(export, "screens", os.path.basename(r["screenshot"]["file"])),
                            os.path.join(export, "trees", d["tree_file"])))
    return out


def package_of(export):
    with open(os.path.join(export, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)["apps_under_test"][0]


def view_at(tree_path, package, x, y):
    """What the app reported at the point (x, y) of the screenshot (screen pixels, as in the tree's bounds):
    the smallest view inside the list there that has text or a description, with its ancestors up to the
    list. None when the point is outside the list or on nothing labelled."""
    root = T.ET.parse(tree_path).getroot()
    node, cb = T.container(root, package)
    if node is None:
        return None
    parent = {ch: p for p in node.iter() for ch in p}
    best = None
    for n in node.iter("node"):
        b = T.box(n)
        if n is node or not b or not (b[0] <= x < b[2] and b[1] <= y < b[3]):
            continue
        if not (n.get("text") or n.get("content-desc")):
            continue
        area = (b[2] - b[0]) * (b[3] - b[1])
        if best is None or area < best[0]:
            best = (area, n)
    if best is None:
        return None
    n = best[1]
    chain, q = [], parent.get(n)
    while q is not None and q is not node:
        chain.append({"rid": T.short_id(q.get("resource-id") or ""), "bounds": T.box(q), "desc": q.get("content-desc") or ""})
        q = parent.get(q)
    return {"rid": T.short_id(n.get("resource-id") or ""), "bounds": T.box(n), "text": n.get("text") or "",
            "desc": n.get("content-desc") or "", "class": (n.get("class") or "").split(".")[-1],
            "ancestors": chain, "list_bounds": cb}


def bubble_of(view):
    """The outermost named view around a message that is narrower than the list, or None."""
    lw = view["list_bounds"][2] - view["list_bounds"][0]
    found = None
    for a in view["ancestors"]:
        b = a["bounds"]
        if a["rid"] and b and (b[2] - b[0]) < 0.97 * lw:
            found = a
    return found


def picture_of(view):
    """The outermost named view around the click that carries a description (one line per picture)."""
    found = {"rid": view["rid"], "bounds": view["bounds"]} if view["rid"] and view["desc"] else None
    for a in view["ancestors"]:
        if a["rid"] and a["desc"]:
            found = a
    return found


def side(view, bubble_ids):
    b = view["bounds"]
    for a in view["ancestors"]:
        if a["rid"] in bubble_ids and a["bounds"]:
            b = a["bounds"]
            break
    return T.side_of(b, view["list_bounds"])


def make_profile(export, answers, now=None):
    """answers: {step name: view from view_at, or None if skipped}. Returns (profile, warnings)."""
    app = package_of(export)
    warnings, prof = [], {"package": app["package"]}
    msgs = [v for k, v in answers.items() if k in ("sent", "received") and v]
    message_ids, parent_ids, bubbles = [], [], []
    for v in msgs:
        if v["rid"]:
            message_ids.append(v["rid"])
        elif v["ancestors"] and v["ancestors"][0]["rid"]:
            parent_ids.append(v["ancestors"][0]["rid"])
        else:
            warnings.append("A clicked message has no view id and neither has the view around it, so messages cannot be told apart from other text.")
        bub = bubble_of(v)
        if bub:
            bubbles.append(bub["rid"])
    prof["message_ids"] = sorted(set(message_ids))
    if parent_ids:
        prof["message_parent_ids"] = sorted(set(parent_ids))
    t = answers.get("time")
    if t and t["rid"]:
        prof["time_ids"] = [t["rid"]]
    elif t:
        warnings.append("The clicked time has no view id, so times are not marked.")
    p = answers.get("picture")
    if p:
        pic = picture_of(p)
        if pic:
            prof["described_ids"] = [pic["rid"]]
        else:
            warnings.append("The clicked picture has no named view with a description, so pictures stay out of the text.")
    a = answers.get("author")
    if a and a["rid"]:
        prof["author_ids"] = [a["rid"]]
    elif a:
        warnings.append("The clicked name has no view id, so author names are not marked.")
    # The bubble counts only when every clicked message has the same one.
    if bubbles and len(bubbles) == len(msgs) and len(set(bubbles)) == 1:
        prof["bubble_ids"] = [bubbles[0]]
    s, r = answers.get("sent"), answers.get("received")
    bids = prof.get("bubble_ids", [])
    ss, rs = (side(s, bids) if s else None), (side(r, bids) if r else None)
    if ss and rs and ss != rs and {ss, rs} == {"left", "right"}:
        prof["direction_rules"] = [{"by": "side", "value": ss, "direction": "sent"},
                                   {"by": "side", "value": rs, "direction": "received"}]
    elif ss and rs and ss == rs:
        prof["single_column"] = True
        warnings.append("The sent and the received message are on the same side, so the app shows one column: "
                        "no message will be called sent or received. Mark an author name if the app shows one.")
    else:
        warnings.append("Sent and received could not be told apart from the clicks (one was skipped, or one sits in "
                        "the middle of the screen), so no direction is claimed.")
    when = (now or datetime.datetime.now()).strftime("%d %B %Y").lstrip("0")
    phone = ""
    try:
        with open(os.path.join(export, "actions.jsonl"), encoding="utf-8") as f:
            for raw in f:
                r0 = json.loads(raw)
                if r0["action"] == "device_state":
                    d = json.loads(r0["detail"])
                    phone = f" on {d.get('manufacturer') or ''} {d.get('model') or ''}, Android {d.get('android') or '?'}".replace("  ", " ")
                    break
    except (OSError, ValueError):
        pass
    prof["checked_with"] = f"{app.get('version_name_at_add') or 'version not recorded'}{phone}, taught in AppWalk by clicks on {when}"
    if not prof["message_ids"] and not prof.get("message_parent_ids"):
        warnings.insert(0, "No message was marked, so this profile would not find any messages.")
    return prof, warnings


def save(profile, folder=None):
    folder = folder or USER_PROFILES
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, profile["package"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def describe(profile):
    """The profile in sentences, for the examiner to read before saving."""
    out = []
    if profile.get("message_ids"):
        out.append("Messages: views named " + ", ".join(profile["message_ids"]) + ".")
    if profile.get("message_parent_ids"):
        out.append("Messages: text with no name of its own inside " + ", ".join(profile["message_parent_ids"]) + ".")
    if profile.get("bubble_ids"):
        out.append("A message's side is the side of its bubble, " + profile["bubble_ids"][0] + ".")
    for r in profile.get("direction_rules", []):
        out.append(f"On the {r['value']}: {r['direction']}.")
    if profile.get("single_column"):
        out.append("One column: no message is called sent or received.")
    if profile.get("time_ids"):
        out.append("Times: " + ", ".join(profile["time_ids"]) + ".")
    if profile.get("described_ids"):
        out.append("Pictures, by their description: " + ", ".join(profile["described_ids"]) + ".")
    if profile.get("author_ids"):
        out.append("Author names: " + ", ".join(profile["author_ids"]) + ".")
    return out
