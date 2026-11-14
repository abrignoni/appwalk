"""Tests for the walker with a made-up phone: a list of numbered items behind a viewport.

No adb and no device are used. Every text here is fictional.
"""
import io
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
                 scrollable=True, front=PKG, password=False, colour=(255, 255, 255), text=True, photo=False, draw=False):
        self.count, self.item_h, self.view_h = count, item_h, view_h
        self.max_offset = max(0, count * item_h - view_h)
        self.offset = {"bottom": self.max_offset, "top": 0, "middle": self.max_offset // 2}[start]
        self.take_swipes, self.swipe_gain, self.scrollable = take_swipes, swipe_gain, scrollable
        self.front, self.password, self.colour = front, password, colour
        # text: each item has a line of text. photo: each item also has a view described only as "Photo",
        # the same on every item (as picture labels and delivery ticks are in Signal). draw: screenshots
        # show each item as its own pattern, so two screenshots can be compared.
        self.text, self.photo, self.draw = text, photo, draw
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
            b = f'bounds="[0,{max(0, top) + 200}][1080,{min(self.view_h, top + self.item_h) + 200}]"'
            if self.text:
                items.append(f'<node class="android.widget.TextView" package="{self.front}" resource-id="{self.front}:id/message_text" '
                             f'text="item {i + 1:03d}" content-desc="" password="false" scrollable="false" checked="false" selected="false" {b} />')
            if self.photo:
                items.append(f'<node class="android.widget.ImageView" package="{self.front}" resource-id="{self.front}:id/image" '
                             f'text="" content-desc="Photo" password="false" scrollable="false" checked="false" selected="false" {b} />')
        secret = (f'<node class="android.widget.EditText" package="{self.front}" resource-id="{self.front}:id/pin" text="90417263" '
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
        if not self.draw:
            return png(self.colour)
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (1080, 1400), (250, 250, 250))
        d = ImageDraw.Draw(im)
        for i in range(self.count):
            top = i * self.item_h - self.offset
            if top + self.item_h <= 0 or top >= self.view_h:
                continue
            grey = 30 + (i * 53) % 180
            for y in range(max(0, top), min(self.view_h, top + self.item_h)):
                shade = grey if (y - top) % 20 < 10 else min(255, grey + 50)
                d.line([(40 + (i * 37) % 300, y + 200), (700 + (i * 41) % 300, y + 200)], fill=(shade, shade, shade))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

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

    def test_stop_at_ends_the_walk_at_the_screen_that_shows_the_text(self):
        # A long thread of which only the newest part is wanted: the walk starts at the newest item and stops at
        # the first screenshot that shows the marker, without walking the older items.
        out, log, s = run(FakePhone(count=80), stop_at="item 070")
        self.assertIn("item 070", texts_seen(out))
        self.assertNotIn("item 001", texts_seen(out))
        self.assertIn("stopped where the examiner asked", s["stop_reason"])
        self.assertFalse(s["end_confirmed"])
        self.assertTrue(all(ok for _, _, ok in appwalk.verify_export(out)))

    def test_one_screen_of_content_is_reported_as_such(self):
        s = run(FakePhone(count=5, scrollable=False))[2]
        self.assertTrue(s["end_confirmed"])
        self.assertIn("one screen of content", s["stop_reason"])
        self.assertEqual(s["screens"], 3)

    def test_a_scroll_that_jumps_past_content_is_logged_and_retried(self):
        phone = FakePhone(count=80, swipe_gain=4.0)
        out, log, s = run(phone)
        self.assertGreater(s["gaps_retried"], 0)
        self.assertEqual(s["gaps"], 0)                   # the retry joined the frame before the gap
        self.assertIn("continuity_gap", [r["action"] for r in log.rows])
        self.assertEqual(texts_seen(out), {f"item {i:03d}" for i in range(1, 81)})

    def test_cap_stops_the_walk_and_says_the_end_was_not_confirmed(self):
        s = run(FakePhone(count=200), max_screens=3)[2]
        self.assertEqual(s["screens"], 3)
        self.assertIsNone(s["end_confirmed"])
        self.assertIn("cap of 3", s["stop_reason"])

    def test_a_view_tree_that_cannot_be_read_ends_the_walk_with_an_export(self):
        # As Instagram did on a Pixel 7a: after some screens uiautomator returns no tree. The walk used to
        # end with a traceback and no manifest; it now stops, says why, and the export checks out.
        class Stuck(FakePhone):
            calls = 0

            def tree_raw(self):
                Stuck.calls += 1
                if Stuck.calls > 12:
                    raise RuntimeError("uiautomator returned no view tree: ERROR: could not get idle state.")
                return super().tree_raw()
        out, log, s = run(Stuck(count=80))
        self.assertIn("did not answer as expected", s["stop_reason"])
        self.assertGreater(s["screens"], 0)
        self.assertIn("walk_error", [r["action"] for r in log.rows])
        checks = appwalk.verify_export(out)
        self.assertTrue(all(ok for _, _, ok in checks), checks)

    def test_an_app_busy_at_the_top_is_walked_up_to_its_spinner(self):
        # As Instagram offline: at the very top of a conversation a loading spinner keeps turning and the
        # view tree cannot be read there. The walk steps back a little, takes that screen, and stops saying why.
        class Spinner(FakePhone):
            def tree_raw(self):
                if self.offset == 0:
                    raise RuntimeError("uiautomator returned no view tree: ERROR: could not get idle state.")
                return super().tree_raw()
        out, log, s = run(Spinner(count=60))
        self.assertIn("loading indicator", s["stop_reason"])
        self.assertFalse(s["end_confirmed"])
        self.assertIn("app_busy_at_end", [r["action"] for r in log.rows])
        self.assertEqual(texts_seen(out), {f"item {i:03d}" for i in range(1, 61)})   # the oldest item is still read
        self.assertTrue(all(ok for _, _, ok in appwalk.verify_export(out)))

    def test_another_app_in_front_stops_the_walk_without_a_screenshot(self):
        out, log, s = run(FakePhone(front="com.example.other"))
        self.assertEqual(s["screens"], 0)
        self.assertIn("blocked", s["stop_reason"])
        self.assertEqual(os.listdir(os.path.join(out, "screens")), [])

    def test_password_prompt_stops_the_walk_and_its_text_is_never_stored(self):
        out, log, s = run(FakePhone(password=True))
        self.assertEqual(s["screens"], 0)
        self.assertNotIn("90417263", read(os.path.join(out, "actions.jsonl"), "r"))

    def test_code_typed_by_the_examiner_is_entered_and_not_recorded(self):
        phone = FakePhone(password=True, count=5, scrollable=False)
        out, log, s = run(phone, on_blocked="ask", ask=lambda why: "77319058")
        self.assertEqual(phone.typed, ["77319058"])
        self.assertGreater(s["screens"], 0)
        # Eight digits, not four: an export holds about 9,000 four-character windows of random hex (row ids,
        # hashes), so a four-digit code turned up there by chance (once in CI, windows-x64, 13 November 2026).
        where = [os.path.relpath(os.path.join(dp, f), out) for dp, _, fs in os.walk(out) for f in fs
                 if b"77319058" in read(os.path.join(dp, f))]
        self.assertEqual(where, [])
        self.assertIn("secret_entered", [r["action"] for r in log.rows])

    def test_password_field_text_is_removed_from_a_tree(self):
        t = appwalk.Tree(FakePhone(password=True).tree_raw())
        self.assertEqual(t.secret_fields, 1)
        self.assertNotIn(b"90417263", t.xml)

    def test_only_a_password_field_is_emptied_not_text_that_mentions_passwords_or_codes(self):
        raw = FakePhone(password=True).tree_raw().replace(
            b'text="item 040"', b'text="my password is tulip-9 and the PIN code is 4821"')
        t = appwalk.Tree(raw)
        self.assertNotIn(b"90417263", t.xml)                                   # what was typed in the password field
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

    def test_a_screen_the_walk_went_back_from_does_not_break_the_order(self):
        # Each swipe goes four times as far as asked: the walk finds a gap, goes back and comes forward in
        # smaller moves. The screen it went back from is set aside, so lines are not repeated or marked missing.
        out, log, s = run(FakePhone(count=80, swipe_gain=4.0))
        self.assertGreater(s["gaps_retried"], 0)
        got = [r["text"] for r in self.lines(out)]
        self.assertEqual(got, [f"item {i:03d}" for i in range(1, 81)])
        self.assertTrue(all(r["joined_to_previous_frame"] in ("", "yes") for r in self.lines(out)))
        import transcript
        notes = transcript.build(out)[1]
        self.assertTrue(any("went back and came forward again" in n for n in notes), notes)
        try:
            import report_pdf
        except ImportError:
            return
        rows = [json.loads(x) for x in open(os.path.join(out, "actions.jsonl"), encoding="utf-8")]
        said = " ".join(report_pdf.summary_lines(json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8")), rows))
        self.assertIn("went back and took that part again", said)
        self.assertNotIn("Look at this", said)

    def test_a_screenshot_with_more_lines_than_a_page_holds_still_makes_a_report(self):
        # Lines with a grey note under each (two or three lines of type) beside one screenshot: 28 of them,
        # the old fixed cut, are taller than a page, and the layout failed (LayoutError, seen on Ubuntu).
        try:
            import report_pdf
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.platypus import Paragraph
        except ImportError:
            self.skipTest("needs ReportLab")
        style = ParagraphStyle("x", fontSize=9.5, leading=12, spaceAfter=3)
        note = '<br/><font size="7">received · the app\'s own description · ' + "said a long thing " * 6 + "</font>"
        paras = [Paragraph(f"S1 line {i} with some words in it{note}", style) for i in range(60)]
        width, limit = 300, 8.3 * 72
        tall = lambda ps: sum(p.wrap(width, 10_000)[1] + p.getSpaceAfter() for p in ps)
        self.assertGreater(tall(paras[:28]), limit)               # the old cut did not fit
        chunks = report_pdf.by_height(paras, width, [limit])
        self.assertEqual(sum(len(c) for c in chunks), 60)
        self.assertTrue(all(tall(c) <= limit for c in chunks), [tall(c) for c in chunks])

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

    def test_a_line_that_changed_between_frames_still_joins_once(self):
        import transcript
        mk = lambda items: [{"text": x, "rid": r, "desc": "", "bounds": (0, i, 9, i + 1), "side": "left"}
                            for i, (x, r) in enumerate(items)]
        held = mk([("2 min", "t"), ("m5", "x"), ("4 min", "t"), ("m6", "x"), ("m7", "x")])
        # The next frame up shows m3 and m4, then the top of what is held, where "2 min" now reads "3 min".
        new = mk([("m3", "x"), ("1 min", "t"), ("m4", "x"), ("3 min", "t"), ("m5", "x"), ("4 min", "t")])
        merged, shared = transcript.join(list(held), new, "above")
        self.assertGreater(shared, 0)
        self.assertEqual([x["text"] for x in merged], ["m3", "1 min", "m4", "2 min", "m5", "4 min", "m6", "m7"])

    def test_a_line_cut_off_at_the_edge_does_not_break_the_join(self):
        import transcript
        mk = lambda texts: [{"text": x, "rid": "x", "desc": "", "bounds": (0, i, 9, i + 1), "side": "left"} for i, x in enumerate(texts)]
        held = mk(["m5", "m6", "m7"])
        new = mk(["m3", "m4", "m5", "m6", "half-visible line"])
        merged, shared = transcript.join(list(held), new, "above")
        self.assertEqual(shared, 2)
        self.assertEqual([x["text"] for x in merged], ["m3", "m4", "m5", "m6", "m7"])

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

    def test_profile_authors_unnamed_messages_and_described_items(self):
        import transcript
        prof = {"message_ids": [], "message_parent_ids": ["accessories_view"], "author_ids": ["author_name"],
                "described_ids": ["image"], "direction_rules": [{"by": "side", "value": "right", "direction": "sent"}]}
        line = lambda **k: dict({"text": "t", "rid": "", "parent_rid": "a:id/accessories_view", "desc": "", "side": "left"}, **k)
        self.assertEqual(transcript.classify(line(), prof)[0], "message")                       # no id, known by its parent
        self.assertEqual(transcript.classify(line(parent_rid="a:id/embed"), prof)[0], "other")  # no id, other parent
        self.assertEqual(transcript.classify(line(rid="a:id/author_name"), prof)[0], "author")
        self.assertEqual(transcript.classify(line(rid="a:id/image", described=True, side="right"), prof),
                         ("described", "sent", "side of the screen (inferred)"))
        # A described view nested in another named one is one line; text always wins over a description.
        tree = os.path.join(tempfile.mkdtemp(), "t.xml")
        with open(tree, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?><hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="p" '
                    'resource-id="p:id/list" scrollable="true" bounds="[0,0][1000,2000]" text="" content-desc="">'
                    '<node class="FrameLayout" package="p" resource-id="p:id/image" bounds="[600,100][950,500]" text="" content-desc="Photo">'
                    '<node class="ImageView" package="p" resource-id="p:id/image" bounds="[600,100][950,500]" text="" content-desc="Photo"/></node>'
                    '<node class="TextView" package="p" resource-id="p:id/msg" bounds="[50,600][500,650]" text="hello" content-desc="x"/>'
                    '</node></hierarchy>')
        got = transcript.frame_lines(tree, "p", {"described_ids": ["image"]})
        self.assertEqual([(x["text"], x["described"]) for x in got], [("Photo", True), ("hello", False)])
        self.assertEqual([x["text"] for x in transcript.frame_lines(tree, "p")], ["hello"])   # without a profile, as before
        shutil.rmtree(os.path.dirname(tree))

    def test_side_comes_from_the_bubble_when_the_profile_names_one(self):
        import transcript
        # A reply: the quote widens the bubble to the right edge, the short answer sits at its left.
        tree = os.path.join(tempfile.mkdtemp(), "t.xml")
        with open(tree, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?><hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="p" '
                    'resource-id="p:id/list" scrollable="true" bounds="[0,0][1080,2000]" text="" content-desc="">'
                    '<node class="FrameLayout" package="p" resource-id="p:id/body_bubble" bounds="[205,100][1038,400]" text="" content-desc="">'
                    '<node class="TextView" package="p" resource-id="p:id/quote_text" bounds="[253,120][941,180]" text="quoted" content-desc=""/>'
                    '<node class="TextView" package="p" resource-id="p:id/body" bounds="[237,200][709,260]" text="ok" content-desc=""/>'
                    '</node></node></hierarchy>')
        side = lambda prof: {x["text"]: x["side"] for x in transcript.frame_lines(tree, "p", prof)}["ok"]
        self.assertEqual(side({}), "left")                                  # by the text's own edges: wrong
        self.assertEqual(side({"bubble_ids": ["body_bubble"]}), "right")   # by the bubble: right
        shutil.rmtree(os.path.dirname(tree))

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


@unittest.skipUnless(appwalk.pixels_available(), "needs Pillow")
class ContinuityTests(unittest.TestCase):
    """Two frames continue each other only on evidence: a shared line of text, or screenshots that overlap."""

    def walk(self, **kw):
        out, log, s = run(FakePhone(count=40, **{k: v for k, v in kw.items() if k != "fraction"}), fraction=kw.get("fraction", 0.5))
        joins = [json.loads(r["detail"])["continuity_with_previous_step"] for r in log.rows if r["action"] == "screenshot"]
        return s, [j for j in joins if j]

    def test_a_shared_description_does_not_hide_a_gap(self):
        # Every item reads "Photo" and no item has text; each swipe moves three times as far as asked.
        # Items of 130 px, so a scroll changes where they sit and the tree shows that something moved.
        s, joins = self.walk(text=False, photo=True, swipe_gain=3.0, item_h=130)
        self.assertGreater(s["gaps"], 0)                 # the old rule saw "Photo" on both frames and found none
        self.assertTrue(all(j["by"] == "screenshots" for j in joins))

    def test_screenshots_join_frames_that_share_no_text(self):
        # Items of 100 px moved by whole items: the tree is the same before and after every scroll, and only
        # the screenshots show that the list moved. Before, the walk stopped after three screens.
        s, joins = self.walk(text=False, photo=True, draw=True)
        self.assertGreater(s["screens"], 5)
        self.assertEqual(s["gaps"], 0)
        self.assertTrue(joins and all(j["joined"] and j["by"] == "screenshots" and j["overlap_px"] for j in joins))

    def test_screenshots_that_do_not_overlap_are_a_gap_and_are_taken_again(self):
        s, joins = self.walk(text=False, photo=True, draw=True, swipe_gain=3.0, item_h=130)
        self.assertGreater(s["gaps_retried"], 0)
        self.assertEqual(s["gaps"], 0)                   # the smaller moves overlap, so nothing is left open

    def test_shared_text_still_joins_without_looking_at_pictures(self):
        s, joins = self.walk()
        self.assertEqual(s["gaps"], 0)
        self.assertTrue(joins and all(j["by"] == "text" for j in joins))


class TeachTests(unittest.TestCase):
    """A profile from clicks on a made-up view tree, as the Teach dialog makes it."""

    def export(self, nodes):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, "trees"))
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"apps_under_test": [{"package": "p", "version_name_at_add": "1.0"}]}, f)
        with open(os.path.join(d, "actions.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"action": "device_state", "detail": json.dumps({"manufacturer": "M", "model": "X", "android": "15"})}) + "\n")
        tree = os.path.join(d, "trees", "t.xml")
        with open(tree, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?><hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="p" '
                    'resource-id="p:id/list" scrollable="true" bounds="[0,0][1080,2000]" text="" content-desc="">'
                    + nodes + "</node></hierarchy>")
        return d, tree

    def node(self, rid, b, text="", desc="", inner=""):
        return (f'<node class="V" package="p" resource-id="{"p:id/" + rid if rid else ""}" bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" '
                f'text="{text}" content-desc="{desc}">{inner}</node>')

    def test_bubbles_sides_and_a_nested_picture(self):
        import teach
        n = self.node
        rows = (n("row", (0, 100, 1080, 400), inner=n("bubble", (205, 110, 1038, 390), inner=n("quote", (253, 120, 941, 180), "q")
                                                                                        + n("body", (237, 200, 709, 260), "ok")))
                + n("row", (0, 500, 1080, 700), inner=n("bubble", (40, 510, 600, 690), inner=n("body", (60, 520, 580, 600), "hi")
                                                                                       + n("when", (60, 620, 200, 660), "9:00")))
                + n("row", (0, 800, 1080, 1300), inner=n("image", (500, 810, 1038, 1290), desc="Photo",
                                                         inner=n("thumb", (510, 820, 1028, 1280), desc="Photo"))))
        d, tree = self.export(rows)
        click = lambda x, y: teach.view_at(tree, "p", x, y)
        answers = {"sent": click(300, 230), "received": click(100, 560), "time": click(100, 640),
                   "picture": click(700, 1000), "author": None}
        prof, warnings = teach.make_profile(d, answers)
        self.assertEqual(prof["message_ids"], ["body"])
        self.assertEqual(prof["bubble_ids"], ["bubble"])          # the short reply reads as right only by its bubble
        self.assertEqual({r["value"]: r["direction"] for r in prof["direction_rules"]}, {"right": "sent", "left": "received"})
        self.assertEqual(prof["time_ids"], ["when"])
        self.assertEqual(prof["described_ids"], ["image"])        # the outer named view, so a picture is one line
        self.assertIsNone(click(5, 5))                            # nothing labelled there
        self.assertEqual(warnings, [])

    def test_a_time_over_a_picture_and_a_click_on_the_picture(self):
        # A time drawn over a photo sits inside the photo's views, which carry a description and no text.
        # A click a little off the time must not pick the photo as the time, nor a picture as a name.
        import teach
        n = self.node
        rows = (n("row", (0, 100, 1080, 600), inner=n("image", (500, 110, 1038, 590), desc="Photo",
                                                      inner=n("blur", (510, 120, 1028, 580), desc="Photo")
                                                      + n("when", (900, 540, 1020, 575), "2:54 PM")))
                + n("row", (0, 700, 1080, 800), inner=n("body", (600, 710, 1040, 790), "ok")))
        d, tree = self.export(rows)
        self.assertEqual(teach.view_at(tree, "p", 950, 560, text_only=True)["rid"], "when")
        self.assertIsNone(teach.view_at(tree, "p", 700, 300, text_only=True))     # bare picture: no text there
        self.assertEqual(teach.view_at(tree, "p", 700, 300)["rid"], "blur")      # the picture step still takes it
        self.assertTrue(teach.wants_text("time") and teach.wants_text("author") and not teach.wants_text("picture"))
        # A view with no text given for time or author (as an older dialog allowed) is left out, with a warning.
        pic = teach.view_at(tree, "p", 700, 300)
        prof, warnings = teach.make_profile(d, {"sent": teach.view_at(tree, "p", 800, 750), "received": None,
                                                "time": pic, "picture": pic, "author": pic})
        self.assertNotIn("time_ids", prof)
        self.assertNotIn("author_ids", prof)
        self.assertEqual(prof["described_ids"], ["image"])
        self.assertEqual(sum("no text" in w for w in warnings), 2)
        with open(os.path.join(d, "actions.jsonl"), "a", encoding="utf-8") as f:   # one walk of that one screen
            f.write(json.dumps({"action": "walk_start", "detail": json.dumps({"item": None, "reveal": "above"})}) + "\n")
            f.write(json.dumps({"action": "screenshot", "entry_id": "e1", "screenshot": {"file": "s.png"},
                                "detail": json.dumps({"step": 1, "tree_file": "t.xml", "screen_in_this_walk": 1})}) + "\n")
        found = teach.preview(d, prof)
        self.assertTrue(any(x.startswith("Messages: 1 lines") for x in found), found)

    def test_one_column_and_text_with_no_id(self):
        import teach
        n = self.node
        rows = (n("", (0, 100, 1080, 300), inner=n("author_name", (150, 110, 300, 150), "Ana")
                                              + n("accessories_view", (150, 160, 1060, 290), inner=n("", (150, 170, 1060, 210), "hello")))
                + n("", (0, 400, 1080, 600), inner=n("accessories_view", (150, 410, 1060, 590), inner=n("", (150, 420, 1060, 460), "hey"))))
        d, tree = self.export(rows)
        click = lambda x, y: teach.view_at(tree, "p", x, y)
        prof, warnings = teach.make_profile(d, {"sent": click(500, 190), "received": click(500, 440), "time": None,
                                                "picture": None, "author": click(200, 130)})
        self.assertEqual(prof["message_ids"], [])
        self.assertEqual(prof["message_parent_ids"], ["accessories_view"])
        self.assertTrue(prof.get("single_column"))
        self.assertNotIn("direction_rules", prof)                 # one column: no direction is claimed
        self.assertEqual(prof["author_ids"], ["author_name"])
        self.assertTrue(any("one column" in w for w in warnings))


class FindAdbTests(unittest.TestCase):
    """A program opened from the Finder or the Start menu does not have a terminal's PATH."""

    def test_adb_is_found_in_the_android_sdk_when_not_on_the_path(self):
        sdk = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, sdk)
        os.makedirs(os.path.join(sdk, "platform-tools"))
        name = "adb.exe" if os.name == "nt" else "adb"
        fake = os.path.join(sdk, "platform-tools", name)
        write(fake, b"#!/bin/sh\n")
        os.chmod(fake, 0o755)
        saved = {k: os.environ.get(k) for k in ("PATH", "ANDROID_HOME", "APPWALK_ADB")}
        try:
            os.environ["PATH"] = tempfile.gettempdir()    # no adb there
            os.environ["ANDROID_HOME"] = sdk
            os.environ.pop("APPWALK_ADB", None)
            self.assertEqual(appwalk.find_adb(), fake)
            os.environ["APPWALK_ADB"] = "/somewhere/else/adb"   # an explicit choice wins
            self.assertEqual(appwalk.find_adb(), "/somewhere/else/adb")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class ReportTextTests(unittest.TestCase):
    def test_characters_the_font_lacks_are_written_as_codes(self):
        try:
            import report_pdf
        except ImportError:
            self.skipTest("needs report_pdf")
        saved = set(report_pdf.COVERED)
        try:
            report_pdf.COVERED.clear()
            report_pdf.COVERED.update(range(32, 256))
            report_pdf.COVERED.add(0x2764)
            # style selector dropped, narrow space drawn as a space, a missing emoji written as its code
            self.assertEqual(report_pdf.drawable("7:26\u202fPM \u2764\ufe0f \U0001FAC2"), "7:26 PM \u2764 [U+1FAC2]")
            # Direction marks around a name are invisible on the phone too: dropped. A private-use
            # character has no Unicode meaning: kept as its code.
            self.assertEqual(report_pdf.drawable("\u2066Ana\u2069 \ue063 x"), "Ana [U+E063] x")
        finally:
            report_pdf.COVERED.clear()
            report_pdf.COVERED.update(saved)


@unittest.skipUnless(appwalk.pixels_available(), "needs Pillow")
class AttachTests(unittest.TestCase):
    """A file added after the walk (an app's own chat export) goes into the hash chain."""

    def export_with_zip(self):
        import zipfile
        out = run(FakePhone(count=12))[0]
        z = os.path.join(tempfile.mkdtemp(), "chat export.zip")
        self.addCleanup(shutil.rmtree, os.path.dirname(z))
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("_chat.txt", "11/13/26, 21:48 - A: hello\n")
            f.writestr("PTT-20261113-WA0001.opus", b"\x00" * 300)
        return out, z

    def test_an_attached_zip_is_chained_listed_and_checked(self):
        out, z = self.export_with_zip()
        before = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))["hash_chain"]["last_row_hash"]
        d = appwalk.attach_file(out, local_path=z, note="WhatsApp, Export chat, Include media")
        self.assertEqual(d["sha256"], appwalk.sha256(read(z)))
        self.assertEqual(sorted(m["name"] for m in d["zip_members"]), ["PTT-20261113-WA0001.opus", "_chat.txt"])
        m = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        self.assertNotEqual(m["hash_chain"]["last_row_hash"], before)           # the chain grew
        self.assertEqual(m["attachments"][0]["file"], "chat export.zip")
        checks = appwalk.verify_export(out)
        self.assertTrue(all(ok for _, _, ok in checks), checks)
        self.assertIn("attached files against chained rows", [c[0] for c in checks])
        # A changed attachment, and a file nobody chained, are both caught.
        with open(os.path.join(out, "attachments", "chat export.zip"), "ab") as f:
            f.write(b"x")
        write(os.path.join(out, "attachments", "stray.txt"), b"not chained")
        bad = {c[0]: c[2] for c in appwalk.verify_export(out)}
        self.assertFalse(bad["attached files against chained rows"])
        self.assertFalse(bad["files with no chained row"])

    def test_the_same_name_is_not_added_twice(self):
        out, z = self.export_with_zip()
        appwalk.attach_file(out, local_path=z)
        with self.assertRaises(FileExistsError):
            appwalk.attach_file(out, local_path=z)


class UnnamedMessageTests(unittest.TestCase):
    """Text with no view id, as Instagram 455 draws plain text bubbles. The shapes are those of a
    known-data chat on a Pixel 7a (13 November 2026): message boxes at least 94 px tall on the left or
    right; names, labels, a reel's account line and the time 52 to 94 px, or centred."""

    LINES = [  # (id, text, left, top, right, bottom, is it a message?)
        ("", "Name", 380, 300, 700, 368, False),                       # centred, 68 px
        ("ig_text", "View profile", 400, 380, 680, 439, False),        # centred, has an id
        ("", "8:31 PM", 470, 460, 610, 554, False),                    # centred, 94 px
        ("", "one line received", 147, 600, 500, 715, True),           # left, 115 px
        ("", "one line sent", 600, 740, 1059, 855, True),              # right, 115 px
        ("", "long sent", 310, 880, 1059, 1247, True),                 # right, 367 px
        ("direct_context_reply_context_info_text_view", "Replied to you", 700, 1270, 1000, 1322, False),
        ("", "account.name", 147, 1340, 420, 1399, False),             # left, 59 px: a reel's account line
        ("", "\U0001F44D", 147, 1420, 260, 1514, True),                # left, 94 px: a single emoji
        ("seen_state_text", "Seen just now", 700, 1540, 1000, 1592, False),
    ]

    def lines(self):
        import transcript
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        nodes = "".join(f'<node class="android.widget.TextView" package="p" resource-id="{"p:id/" + r if r else ""}" '
                        f'text="{t}" content-desc="" bounds="[{l},{tp}][{rt},{b}]" />'
                        for r, t, l, tp, rt, b, _ in self.LINES)
        tree = os.path.join(d, "t.xml")
        with open(tree, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?><hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="p" '
                    'resource-id="p:id/message_list" scrollable="true" bounds="[0,250][1080,2100]" text="" content-desc="">'
                    + nodes + "</node></hierarchy>")
        return transcript, transcript.frame_lines(tree, "p")

    def test_height_marks_unnamed_text_bubbles_as_messages(self):
        transcript, got = self.lines()
        prof = {"message_ids": ["direct_text_message_text_view"], "unnamed_message_min_height": 80,
                "direction_rules": [{"by": "side", "value": "right", "direction": "sent"},
                                    {"by": "side", "value": "left", "direction": "received"}]}
        want = {t: m for _, t, *_, m in self.LINES}
        for x in got:
            role, direction, _ = transcript.classify(x, prof)
            self.assertEqual(role == "message", want[x["text"]], x["text"])
            if role == "message":
                self.assertEqual(direction, {"left": "received", "right": "sent"}[x["side"]], x["text"])

    def test_without_the_option_unnamed_text_stays_other(self):
        transcript, got = self.lines()
        prof = {"message_ids": ["direct_text_message_text_view"]}
        self.assertEqual({transcript.classify(x, prof)[0] for x in got}, {"other"})


class BoxDescriptionTests(unittest.TestCase):
    """Threads (Pixel 7a, 13 November 2026) gives its chat views no ids, but describes each bubble:
    "Chat text message" around a text bubble, "Photo preview" on a picture. Times and labels sit on the
    same sides, outside any such box."""

    def lines(self):
        import transcript
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)

        def v(desc, b, inner=""):
            return (f'<node class="android.view.View" package="p" resource-id="" text="" content-desc="{desc}" '
                    f'bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]">{inner}</node>')

        def t(text, b, rid=""):
            return (f'<node class="android.widget.TextView" package="p" resource-id="{"p:id/" + rid if rid else ""}" '
                    f'text="{text}" content-desc="" bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" />')
        nodes = (t("8:58 PM", (900, 300, 1040, 340), "ig_text")                 # a time on the right, in no box
                 + v("Chat text message", (100, 360, 600, 440)) + t("how are you", (130, 370, 400, 430))
                 + v("Chat text message", (500, 460, 1050, 640)) + t("a reply that wraps", (530, 470, 1020, 630))
                 + v("Chat emoji text message", (100, 660, 300, 840)) + t("\U0001F602", (120, 670, 280, 830), "ig_text")
                 + v("Photo preview", (100, 860, 600, 1360))
                 + v("Chat text message", (100, 1440, 800, 1730)) + t("the quoted message", (130, 1460, 780, 1700))
                 + v("Chat text message", (100, 1688, 600, 1800)) + t("the reply", (130, 1700, 560, 1790))
                 + t("Seen 9:00 PM", (100, 1380, 400, 1420), "messaging_seen_indicator"))
        tree = os.path.join(d, "t.xml")
        with open(tree, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?><hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="p" '
                    'resource-id="" scrollable="true" bounds="[0,250][1080,2100]" text="" content-desc="">'
                    + nodes + "</node></hierarchy>")
        prof = {"message_box_descriptions": ["Chat text message", "Chat emoji text message"],
                "described_box_descriptions": ["Photo preview"],
                "direction_rules": [{"by": "side", "value": "right", "direction": "sent"},
                                    {"by": "side", "value": "left", "direction": "received"}]}
        self.tree = tree
        return transcript, transcript.frame_lines(tree, "p", prof), prof

    def test_messages_and_pictures_by_the_apps_own_descriptions(self):
        transcript, got, prof = self.lines()
        said = {x["text"]: transcript.classify(x, prof)[:2] for x in got}
        self.assertEqual(said["how are you"], ("message", "received"))
        self.assertEqual(said["a reply that wraps"], ("message", "sent"))
        self.assertEqual(said["\U0001F602"], ("message", "received"))
        self.assertEqual(said["Photo preview"], ("described", "received"))
        self.assertEqual(said["8:58 PM"][0], "other")            # a time on the right is not a sent message
        self.assertEqual(said["Seen 9:00 PM"][0], "other")
        self.assertEqual(said["the reply"], ("message", "received"))
        self.assertEqual(said["the quoted message"][0], "other")   # its box runs under the reply's: a quote

    def test_without_the_options_nothing_is_a_message(self):
        import transcript
        self.lines()
        bare = {"direction_rules": [{"by": "side", "value": "right", "direction": "sent"}]}
        got = transcript.frame_lines(self.tree, "p", bare)
        self.assertNotIn("Photo preview", [x["text"] for x in got])
        self.assertEqual({transcript.classify(x, bare)[0] for x in got}, {"other"})


class PictureOverlapTests(unittest.TestCase):
    """Two screenshots of a list whose shared part holds no text are joined only by the pictures."""

    def setUp(self):
        import random
        from PIL import Image
        random.seed(7)
        self.tmp = tempfile.mkdtemp()
        # A tall made-up page of grey blocks, like pictures in a chat, and 1000-pixel windows onto it.
        page = Image.new("L", (400, 4000), 255)
        for y in range(0, 4000, 40):
            for x in range(0, 400, 40):
                page.paste(random.randint(0, 255), (x, y, x + 40, y + 40))
        self.page = page

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def window(self, top, name):
        path = os.path.join(self.tmp, name)
        self.page.crop((0, top, 400, top + 1000)).save(path)
        return path

    def test_a_real_overlap_is_found(self):
        import transcript
        # Revealing above: the new screenshot shows the page 700 pixels higher, so 300 pixels are shared.
        old, new = self.window(2000, "old.png"), self.window(1300, "new.png")
        px = transcript.picture_overlap(old, new, (0, 0, 400, 1000), "above")
        self.assertIsNotNone(px)
        self.assertLess(abs(px - (300 - 140)), 12)   # the shared part, less the 7% left out at top and bottom

    def test_screenshots_that_share_nothing_are_not_joined(self):
        import transcript
        old, new = self.window(2500, "old.png"), self.window(1300, "new.png")
        self.assertIsNone(transcript.picture_overlap(old, new, (0, 0, 400, 1000), "above"))


if __name__ == "__main__":
    unittest.main()
