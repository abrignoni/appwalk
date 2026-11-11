"""Tests for the walker with a made-up phone: a list of numbered items behind a viewport.

No adb and no device are used. Every text here is fictional.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import appwalk  # noqa: E402

PKG = "com.example.chat"


def png(rgb):
    """A valid 4x4 PNG of one colour, built with the standard library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakePhone:
    """A list of `count` items, each `item_h` pixels high, seen through a window `view_h` high.
    `offset` is the distance from the top of the list to the top of the window."""

    def __init__(self, count=40, item_h=100, view_h=1000, start="bottom", take_swipes=True, swipe_gain=1.0,
                 scrollable=True, front=PKG, password=False, colour=(255, 255, 255)):
        self.count, self.item_h, self.view_h = count, item_h, view_h
        self.max_offset = max(0, count * item_h - view_h)
        self.offset = {"bottom": self.max_offset, "top": 0, "middle": self.max_offset // 2}[start]
        self.take_swipes, self.swipe_gain, self.scrollable = take_swipes, swipe_gain, scrollable
        self.front, self.password, self.colour = front, password, colour
        self.clock = 1_800_000_000_000
        self.swipes, self.typed = [], []

    def stamp(self):
        self.clock += 250
        return {"utc_ms": self.clock, "elapsed_ms": self.clock - 1_799_999_000_000, "tz_id": "Etc/UTC",
                "tz_offset_min": 0, "boot_count": 3}

    def tree_raw(self):
        items = []
        for i in range(self.count):
            top = i * self.item_h - self.offset
            if top + self.item_h <= 0 or top >= self.view_h:
                continue
            items.append(f'<node class="android.widget.TextView" package="{self.front}" resource-id="{self.front}:id/message_text" '
                         f'text="item {i + 1:03d}" content-desc="" password="false" scrollable="false" checked="false" selected="false" '
                         f'bounds="[0,{max(0, top) + 200}][1080,{min(self.view_h, top + self.item_h) + 200}]" />')
        secret = (f'<node class="android.widget.EditText" package="{self.front}" resource-id="{self.front}:id/pin" text="9041" '
                  f'content-desc="" password="true" scrollable="false" checked="false" selected="false" bounds="[0,0][1080,100]" />'
                  if self.password else "")
        return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
                f'<node class="android.widget.FrameLayout" package="{self.front}" resource-id="" text="" content-desc="" password="false" '
                f'scrollable="false" checked="false" selected="false" bounds="[0,0][1080,1400]">'
                f'<node class="android.widget.TextView" package="{self.front}" resource-id="{self.front}:id/title" text="Fictional thread" '
                f'content-desc="" password="false" scrollable="false" checked="false" selected="false" bounds="[0,0][1080,200]" />{secret}'
                f'<node class="androidx.recyclerview.widget.RecyclerView" package="{self.front}" resource-id="{self.front}:id/list" text="" '
                f'content-desc="" password="false" scrollable="{"true" if self.scrollable else "false"}" checked="false" selected="false" '
                f'bounds="[0,200][1080,{200 + self.view_h}]">{"".join(items)}</node></node></hierarchy>').encode("utf-8")

    def screenshot(self):
        return png(self.colour)

    def swipe(self, x1, y1, x2, y2, ms):
        self.swipes.append((y1, y2, ms))
        if self.take_swipes:
            # A finger moving down (y2 > y1) brings earlier items into view: the offset gets smaller.
            self.offset = int(min(self.max_offset, max(0, self.offset - (y2 - y1) * self.swipe_gain)))

    def type_secret(self, secret):
        self.typed.append(secret)
        self.password = False

    def profile(self):
        return {"manufacturer": "Example", "model": "Fake 1", "device": "fake", "fingerprint": "example/fake", "release": "15",
                "incremental": "1", "sdk_int": 35, "security_patch": "2026-01-01", "kernel": "0", "baseband": None,
                "carrier": None, "notes": None}

    def app_version(self, package):
        return "1.0-fake"


TEMP_DIRS = []


def tearDownModule():
    for d in TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


def read(path, mode="rb"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def run(phone, **kw):
    tmp = tempfile.mkdtemp(prefix="appwalk_test_")
    TEMP_DIRS.append(tmp)
    out = os.path.join(tmp, "export")
    os.makedirs(out)
    log = appwalk.Log(out, "test", "fake", phone, host_clock=lambda: 1_800_000_000.0)
    walker = appwalk.Walker(phone, log, PKG, sleep=lambda s: None, say=lambda *a: None, **kw)
    summary = walker.run()
    appwalk.write_export(log, phone, PKG, walker.app_version, summary, {})
    return out, log, summary


def texts_seen(out):
    seen = set()
    for name in os.listdir(os.path.join(out, "trees")):
        seen |= {n.get("text") for n in appwalk.Tree(read(os.path.join(out, "trees", name))).nodes(PKG)
                 if (n.get("resource-id") or "").endswith("/message_text")}
    return seen


class WalkTests(unittest.TestCase):
    def test_covers_the_whole_list_from_any_starting_position(self):
        for start in ("bottom", "top", "middle"):
            phone = FakePhone(start=start)
            out, log, s = run(phone)
            self.assertEqual(texts_seen(out), {f"item {i:03d}" for i in range(1, 41)}, start)
            self.assertTrue(s["end_confirmed"], start)
            self.assertTrue(s["rewind_end_confirmed"], start)
            self.assertEqual(s["gaps"], 0)

    def test_ignored_swipes_are_not_read_as_the_end_of_the_list(self):
        s = run(FakePhone(take_swipes=False))[2]
        self.assertFalse(s["rewind_end_confirmed"])
        self.assertFalse(s["end_confirmed"])
        self.assertIn("NOT CONFIRMED", s["stop_reason"])

    def test_one_screen_of_content_is_reported_as_such(self):
        s = run(FakePhone(count=5, scrollable=False))[2]
        self.assertTrue(s["end_confirmed"])
        self.assertIn("one screen of content", s["stop_reason"])
        self.assertEqual(s["screens"], 3)

    def test_a_scroll_that_jumps_past_content_is_logged_and_retried(self):
        phone = FakePhone(count=80, swipe_gain=4.0)
        out, log, s = run(phone)
        self.assertGreater(s["gaps"], 0)
        self.assertIn("continuity_gap", [r["action"] for r in log.rows])
        self.assertEqual(texts_seen(out), {f"item {i:03d}" for i in range(1, 81)})

    def test_cap_stops_the_walk_and_says_the_end_was_not_confirmed(self):
        s = run(FakePhone(count=200), max_screens=3)[2]
        self.assertEqual(s["screens"], 3)
        self.assertIsNone(s["end_confirmed"])
        self.assertIn("cap of 3", s["stop_reason"])

    def test_another_app_in_front_stops_the_walk_without_a_screenshot(self):
        out, log, s = run(FakePhone(front="com.example.other"))
        self.assertEqual(s["screens"], 0)
        self.assertIn("blocked", s["stop_reason"])
        self.assertEqual(os.listdir(os.path.join(out, "screens")), [])

    def test_password_prompt_stops_the_walk_and_its_text_is_never_stored(self):
        out, log, s = run(FakePhone(password=True))
        self.assertEqual(s["screens"], 0)
        self.assertNotIn("9041", read(os.path.join(out, "actions.jsonl"), "r"))

    def test_code_typed_by_the_examiner_is_entered_and_not_recorded(self):
        phone = FakePhone(password=True, count=5, scrollable=False)
        out, log, s = run(phone, on_blocked="ask", ask=lambda why: "7731")
        self.assertEqual(phone.typed, ["7731"])
        self.assertGreater(s["screens"], 0)
        everything = b"".join(read(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
        self.assertNotIn(b"7731", everything)
        self.assertIn("secret_entered", [r["action"] for r in log.rows])

    def test_password_field_text_is_removed_from_a_tree(self):
        t = appwalk.Tree(FakePhone(password=True).tree_raw())
        self.assertEqual(t.secret_fields, 1)
        self.assertNotIn(b"9041", t.xml)

    def test_only_a_password_field_is_emptied_not_text_that_mentions_passwords_or_codes(self):
        raw = FakePhone(password=True).tree_raw().replace(
            b'text="item 040"', b'text="my password is tulip-9 and the PIN code is 4821"')
        t = appwalk.Tree(raw)
        self.assertNotIn(b"9041", t.xml)                                   # what was typed in the password field
        self.assertIn(b"my password is tulip-9 and the PIN code is 4821", t.xml)  # an ordinary message
        self.assertIn(b":id/pin", t.xml)                                   # the field itself is still listed

    def test_black_frame_is_recorded_as_blank(self):
        out, log, s = run(FakePhone(count=5, scrollable=False, colour=(0, 0, 0)))
        if appwalk.is_blank(png((0, 0, 0))) is None:
            self.skipTest("Pillow not installed")
        self.assertEqual(s["blank"], s["screens"])
        self.assertEqual(json.loads(read(os.path.join(out, "manifest.json"), "r"))["screenshots"][0]["status"], "blank")


class FakeChat(FakePhone):
    """A made-up chat app: a list of conversations, each opening a thread of numbered messages.
    Tapping a row opens its thread somewhere in the middle; Back returns to the list."""

    def __init__(self, conversations, row_h=180, view_h=1000, dead_rows=(), back_leaves_app=False):
        super().__init__()
        self.conversations, self.row_h, self.view_h = dict(conversations), row_h, view_h
        self.dead_rows, self.back_leaves_app = set(dead_rows), back_leaves_app
        self.screen = None  # None is the list, otherwise a conversation title
        self.offsets = {None: max(0, len(conversations) * row_h - view_h) // 2}
        self.taps, self.backs = [], 0

    def _geometry(self):
        if self.screen is None:
            return len(self.conversations), self.row_h
        return self.conversations[self.screen], self.item_h

    def _max(self):
        n, h = self._geometry()
        return max(0, n * h - self.view_h)

    def tree_raw(self):
        n, h = self._geometry()
        off = self.offsets.setdefault(self.screen, self._max() // 2)
        kids = []
        titles = list(self.conversations)
        for i in range(n):
            top = i * h - off
            if top + h <= 0 or top >= self.view_h:
                continue
            b = f'bounds="[0,{max(0, top) + 200}][1080,{min(self.view_h, top + h) + 200}]"'
            common = f'package="{self.front}" content-desc="" password="false" scrollable="false" checked="false" selected="false"'
            if self.screen is None:
                # A row cut off at the top of the list has lost its name line, as a real list does.
                name = (f'<node class="android.widget.TextView" resource-id="{PKG}:id/name" text="{titles[i]}" {common} {b} />'
                        if top > -60 else "")
                kids.append(f'<node class="android.view.ViewGroup" resource-id="{PKG}:id/row" text="" {common} {b}>{name}'
                            f'<node class="android.widget.TextView" resource-id="{PKG}:id/snippet" text="{titles[i]} msg {self.conversations[titles[i]]:03d}" {common} {b} />'
                            f'</node>')
            else:
                kids.append(f'<node class="android.widget.TextView" resource-id="{PKG}:id/message_text" text="{self.screen} msg {i + 1:03d}" {common} {b} />')
        desc = "Conversation list" if self.screen is None else "Message list"
        head = "Chats" if self.screen is None else self.screen
        return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
                f'<node class="android.widget.FrameLayout" package="{self.front}" resource-id="" text="" content-desc="" password="false" '
                f'scrollable="false" checked="false" selected="false" bounds="[0,0][1080,1400]">'
                f'<node class="android.widget.TextView" package="{self.front}" resource-id="{PKG}:id/title" text="{head}" '
                f'content-desc="" password="false" scrollable="false" checked="false" selected="false" bounds="[0,0][1080,200]" />'
                f'<node class="androidx.recyclerview.widget.RecyclerView" package="{self.front}" resource-id="{PKG}:id/list" text="" '
                f'content-desc="{desc}" password="false" scrollable="{"true" if self._max() > 0 else "false"}" checked="false" selected="false" '
                f'bounds="[0,200][1080,{200 + self.view_h}]">{"".join(kids)}</node></node></hierarchy>').encode("utf-8")

    def swipe(self, x1, y1, x2, y2, ms):
        self.offsets[self.screen] = int(min(self._max(), max(0, self.offsets.get(self.screen, 0) - (y2 - y1))))

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.screen is None:
            i = (y - 200 + self.offsets[None]) // self.row_h
            title = list(self.conversations)[i]
            if title not in self.dead_rows:
                self.screen = title
                self.offsets.pop(title, None)

    def back(self):
        self.backs += 1
        if self.screen is not None and not self.back_leaves_app:
            self.screen = None
        else:
            self.front = "com.example.launcher"


CHATS = {"Ana": 30, "Bruno": 4, "Carla": 55, "Dev": 12, "Elif": 1, "Farid": 22, "Gus": 9, "Hana": 40, "Ines": 3, "Jo": 17}


def run_list(phone, select="all", matches=(), capture_list=True, answer=None):
    tmp = tempfile.mkdtemp(prefix="appwalk_test_")
    TEMP_DIRS.append(tmp)
    out = os.path.join(tmp, "export")
    os.makedirs(out)
    log = appwalk.Log(out, "test", "fake", phone, host_clock=lambda: 1_800_000_000.0)
    walker = appwalk.Walker(phone, log, PKG, sleep=lambda s: None, say=lambda *a: None)
    lw = appwalk.ListWalk(walker, capture_list=capture_list)
    result = lw.run(appwalk.chooser(select, list(matches), ask=(lambda prompt: answer) if answer else None))
    result["walks"] = [s for s in walker.walks if s["item"]]
    appwalk.write_export(log, phone, PKG, walker.app_version, result, {})
    return out, log, result


def bubbles_by_item(out):
    import csv
    got = {}
    with open(os.path.join(out, "text_index.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["resource_id"].endswith("/message_text"):
                got.setdefault(r["item"], set()).add(r["text"])
    return got


class ListWalkTests(unittest.TestCase):
    def test_every_row_is_opened_and_every_thread_is_covered(self):
        out, log, res = run_list(FakeChat(CHATS))
        self.assertEqual(res["items_found"], len(CHATS))
        self.assertEqual(res["items_walked"], len(CHATS))
        self.assertIsNone(res["stop_reason"])
        got = bubbles_by_item(out)
        for title, n in CHATS.items():
            self.assertEqual(got[title], {f"{title} msg {i:03d}" for i in range(1, n + 1)}, title)
        for s in res["walks"]:
            self.assertTrue(s["end_confirmed"] and s["rewind_end_confirmed"] and s["gaps"] == 0, s)
        self.assertTrue(all(ok for _, _, ok in appwalk.verify_export(out)))

    def test_only_chosen_rows_are_opened(self):
        phone = FakeChat(CHATS)
        out, log, res = run_list(phone, matches=["carla", "Elif"])
        self.assertEqual(res["items_selected"], ["Carla", "Elif"])
        self.assertEqual(set(bubbles_by_item(out)), {"Carla", "Elif"})
        self.assertEqual(len(phone.taps), 2)

    def test_without_list_capture_nothing_of_the_other_rows_is_kept(self):
        out, log, res = run_list(FakeChat(CHATS), matches=["Dev"], capture_list=False)
        everything = b"".join(read(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
        self.assertIn(b"Dev msg 012", everything)
        for other in (t for t in CHATS if t != "Dev"):
            self.assertNotIn(other.encode() + b" msg", everything, other)
            self.assertNotIn(b'"' + other.encode() + b'"', everything, other)

    def test_with_list_capture_the_list_is_documented_but_other_threads_are_not(self):
        out, log, res = run_list(FakeChat(CHATS), matches=["Dev"])
        self.assertEqual(set(bubbles_by_item(out)), {"Dev"})
        everything = b"".join(read(os.path.join(out, "trees", f)) for f in os.listdir(os.path.join(out, "trees")))
        self.assertIn(b'text="Hana"', everything)        # the row is in the pictures of the list
        self.assertNotIn(b'text="Hana msg 001"', everything)  # its thread was never opened

    def test_a_title_match_takes_every_row_that_contains_the_text(self):
        res = run_list(FakeChat(CHATS), matches=["ana"])[2]
        self.assertEqual(res["items_selected"], ["Ana", "Hana"])

    def test_the_examiner_can_pick_rows_by_number(self):
        out, log, res = run_list(FakeChat(CHATS), select="ask", answer="1, 3-4")
        self.assertEqual(res["items_selected"], ["Ana", "Carla", "Dev"])
        self.assertEqual(res["items_walked"], 3)

    def test_a_row_that_does_not_open_is_reported_and_the_rest_go_on(self):
        out, log, res = run_list(FakeChat(CHATS, dead_rows=["Bruno"]), select="ask", answer="1-3")
        self.assertEqual(res["items_not_opened"], ["Bruno"])
        self.assertEqual(res["items_walked"], 2)
        self.assertEqual(set(bubbles_by_item(out)), {"Ana", "Carla"})

    def test_leaving_the_app_on_back_stops_the_run(self):
        out, log, res = run_list(FakeChat(CHATS, back_leaves_app=True), select="ask", answer="1-2")
        self.assertIn("could not get back to the list", res["stop_reason"])
        self.assertEqual(res["items_walked"], 1)


class TranscriptTests(unittest.TestCase):
    def lines(self, out, item=""):
        import csv
        with open(os.path.join(out, "transcript.csv"), encoding="utf-8", newline="") as f:
            return [r for r in csv.DictReader(f) if r["item"] == item]

    def test_each_line_appears_once_in_reading_order_whichever_way_the_walk_went(self):
        for reveal in ("above", "below"):
            for start in ("bottom", "middle"):
                out = run(FakePhone(start=start), reveal=reveal)[0]
                got = [r["text"] for r in self.lines(out)]
                self.assertEqual(got, [f"item {i:03d}" for i in range(1, 41)], (reveal, start))
                self.assertTrue(all(r["joined_to_previous_frame"] in ("", "yes") for r in self.lines(out)))

    def test_every_line_names_a_screenshot_that_exists_and_showed_it(self):
        out = run(FakePhone())[0]
        for r in self.lines(out):
            tree = os.path.join(out, "trees", r["screenshot"].replace(".png", ".xml"))
            self.assertTrue(os.path.exists(os.path.join(out, "screens", r["screenshot"])))
            self.assertIn(('text="%s"' % r["text"]).encode(), read(tree))

    def test_a_jump_past_content_is_flagged_in_the_transcript(self):
        import transcript
        a = [{"text": f"m{i}", "rid": "x", "desc": "", "bounds": (0, i, 9, i + 1), "side": "left"} for i in range(5, 9)]
        b = [{"text": f"m{i}", "rid": "x", "desc": "", "bounds": (0, i, 9, i + 1), "side": "left"} for i in range(0, 3)]
        merged, shared = transcript.join(list(a), b, "above")
        self.assertEqual(shared, 0)
        self.assertEqual([x["text"] for x in merged], ["m0", "m1", "m2", "m5", "m6", "m7", "m8"])

    def test_repeated_identical_messages_keep_their_count(self):
        import transcript
        mk = lambda texts: [{"text": t, "rid": "x", "desc": "", "bounds": (0, i, 9, i + 1), "side": "left"} for i, t in enumerate(texts)]
        merged, shared = transcript.join(mk(["ok", "ok", "bye"]), mk(["hi", "ok", "ok", "ok"]), "above")
        # Frames overlap on two "ok"; the earlier frame adds one more above them.
        self.assertEqual([x["text"] for x in merged], ["hi", "ok", "ok", "ok", "bye"])
        self.assertEqual(shared, 2)

    def test_side_is_where_the_line_sat_and_a_long_bubble_still_leans(self):
        import transcript
        cb = (0, 0, 1000, 2000)
        self.assertEqual(transcript.side_of((30, 0, 700, 50), cb), "left")
        self.assertEqual(transcript.side_of((400, 0, 980, 50), cb), "right")
        self.assertEqual(transcript.side_of((70, 0, 900, 50), cb), "left")     # long bubble, received
        self.assertEqual(transcript.side_of((300, 0, 700, 50), cb), "across")  # a centred date line
        self.assertEqual(transcript.side_of((10, 0, 990, 50), cb), "across")

    def test_without_a_profile_no_direction_is_claimed(self):
        out = run(FakePhone(count=12))[0]
        self.assertTrue(all(r["role"] == "text" and r["direction"] == "" for r in self.lines(out)))

    def test_a_profile_gives_direction_and_says_on_what_basis(self):
        import transcript
        prof = {"message_ids": ["message_text"], "time_ids": ["when"], "direction_rules": [
            {"by": "description_prefix", "value": "You said", "direction": "sent"},
            {"by": "side", "value": "right", "direction": "sent"}, {"by": "side", "value": "left", "direction": "received"}]}
        line = lambda **k: dict({"text": "t", "rid": "a:id/message_text", "desc": "", "side": "left"}, **k)
        self.assertEqual(transcript.classify(line(desc="You said t"), prof), ("message", "sent", "the app's own description"))
        self.assertEqual(transcript.classify(line(side="right"), prof), ("message", "sent", "side of the screen (inferred)"))
        self.assertEqual(transcript.classify(line(), prof), ("message", "received", "side of the screen (inferred)"))
        self.assertEqual(transcript.classify(line(rid="a:id/when"), prof)[0], "time")
        self.assertEqual(transcript.classify(line(rid="a:id/title"), prof)[0], "other")

    def test_list_mode_gives_one_section_per_opened_row(self):
        out = run_list(FakeChat(CHATS), select="ask", answer="2-3")[0]
        self.assertEqual([r["text"] for r in self.lines(out, "Bruno")], [f"Bruno msg {i:03d}" for i in range(1, 5)])
        self.assertEqual([r["text"] for r in self.lines(out, "Carla")], [f"Carla msg {i:03d}" for i in range(1, 56)])
        self.assertTrue(os.path.exists(os.path.join(out, "transcript.html")))


class ExportTests(unittest.TestCase):
    def test_export_verifies_and_each_kind_of_tampering_is_caught(self):
        out, log, s = run(FakePhone(count=12))
        self.assertTrue(all(ok for _, _, ok in appwalk.verify_export(out)))
        shot = sorted(os.listdir(os.path.join(out, "screens")))[0]
        p = os.path.join(out, "screens", shot)
        original = read(p)
        write(p, png((1, 2, 3)))
        self.assertFalse(all(ok for _, _, ok in appwalk.verify_export(out)), "a changed image must fail")
        write(p, original)
        self.assertTrue(all(ok for _, _, ok in appwalk.verify_export(out)))
        write(os.path.join(out, "screens", "9999_extra.png"), original)
        self.assertFalse(all(ok for _, _, ok in appwalk.verify_export(out)), "an added image must fail")
        os.remove(os.path.join(out, "screens", "9999_extra.png"))
        lines = read(os.path.join(out, "actions.jsonl"), "r").splitlines()
        write(os.path.join(out, "actions.jsonl"), ("\n".join(lines[:-1]) + "\n").encode("utf-8"))
        self.assertFalse(all(ok for _, _, ok in appwalk.verify_export(out)), "a removed row must fail")

    def test_chain_is_the_one_kdl_checks(self):
        """KDL's own documentation script must accept the chain, when that repository is beside this one."""
        tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "known-data-logger", "tools")
        if not os.path.exists(os.path.join(tools, "make_documentation.py")):
            self.skipTest("known-data-logger is not beside this repository")
        sys.path.insert(0, tools)
        try:
            import make_documentation
        except SystemExit:
            self.skipTest("openpyxl not installed")
        out, log, s = run(FakePhone(count=12))
        e = make_documentation.load_export(out)
        self.assertEqual([c for c in e["checks"] if make_documentation.failed(c[1])], [])
        self.assertTrue(dict(e["checks"])["hash chain"].startswith("intact"))


if __name__ == "__main__":
    unittest.main()
