<p align="center"><img src="assets/appwalk_256.png" width="128" height="128" alt="AppWalk logo: footprints walking down a phone screen"></p>

# AppWalk (prototype)

AppWalk is a prototype, tried on one Android 15 emulator and nothing else. The name is written AppWalk;
the repository, the command and the module are `appwalk`.

It documents an Android app's content with screenshots, from a workstation, over adb, on a phone the
examiner is allowed to examine. There are two ways to use it:

- `walk`: the examiner opens one screen (a message thread, a list). The tool scrolls it from one end
  to the other, saving a screenshot and the screen's view tree at each step.
- `walk-list`: the examiner opens a list (the conversation list, for example) and chooses rows. The
  tool opens each chosen row, walks what it opens the same way, and goes back for the next.

Both write a log of everything the tool did.

For the window step by step, see the [quick start](docs/QUICKSTART.md); for everything, the
[user manual](docs/USER_MANUAL.md).

It is for a device examined with the owner's consent or other lawful authority. It does not unlock
anything, guess anything or get around any lock. When a lock screen, a password prompt or another
app is in front, it stops. With `--on-blocked ask` it waits for the examiner, who can deal with it
on the phone or type the code at the workstation for the tool to enter.

## What it uses on the phone

USB debugging, and three programs that are already on the phone: `uiautomator` (view tree),
`screencap` (screenshot) and `input` (swipes). It installs no app. Screenshots and view trees are
sent straight to the workstation. On the test emulator, `uiautomator dump /dev/tty` left no file on
the phone's storage.

## Use

```bash
python3 appwalk.py walk --serial <adb serial> --package <app package> --out <folder> --scenario <name> --device-label <label>
```

```bash
python3 appwalk.py walk-list --serial <adb serial> --package <app package> --out <folder> --scenario <name> --device-label <label>
```

```bash
python3 appwalk.py verify <export folder>
```

Choosing rows with `walk-list`:

- With nothing else, it reads the list, prints the row titles with numbers and asks which ones
  (`1,3-5` or `all`).
- `--select all` takes every row. `--match TEXT` (repeatable) takes the rows whose title contains the text.
- `--skip-list-capture` reads the list's rows without keeping screenshots or view trees of the list
  itself. A picture of the list shows a line of every conversation, chosen or not. With this option
  only the chosen rows' titles and their opened content are in the export.

How `walk-list` opens a row: it finds the row going forward through the list, taps its title, checks
that another screen came up, walks it, then presses Back until the list is in front again. A row that
does not open is reported and the rest go on. If Back leaves the app, the run stops.

Or use the window, which runs the same code with no terminal:

```bash
python3.12 appwalk_gui.py
```

It has four steps:

1. **Phone.** Shows the connected phone and its state: model, Android version, battery, airplane mode,
   Wi-Fi, and how far its clock is from the computer's. It warns when the phone is online, the battery
   is low or the screen times out quickly. The examiner's name, a case reference, a label for the
   phone and a note are typed here and written into the log.
2. **What to document.** Fills in the app that is in front, and offers the open screen or rows chosen
   from the open list.
3. **Run.** Start, Pause and Stop. Each screenshot shows as it is taken. A picker lists the rows found,
   with a box to narrow the list. If a lock or another app comes to the front, a dialog waits for the
   examiner. If the app in front is no longer the one chosen when Start is pressed, it asks which one
   is meant.
4. **Result.** What was captured, in sentences, with anything to look at in a warning colour. Buttons
   open the PDF report, the transcript and the folder, check the export, and copy the latest row hash.
   Next becomes Close AppWalk here.

Light and dark themes: it starts in the system's setting on macOS and Windows, and the Light or Dark
button in the header switches. Examiner, phone label, folder and theme are remembered in
`~/.appwalk/settings.json`; the case reference and note are not.

The window is drawn with CustomTkinter when it is installed (`pip install customtkinter`). Without it,
the plain Tk window in `appwalk_gui_classic.py` opens instead; both run the same code underneath.

Every run from the window or the command line now also logs the phone's state before and after it,
and a pause as its own row.

It needs a Python whose Tk is 8.6; on macOS the python.org 3.14 build has Tk 9, and the window
refuses to start there.

Driven through its own controls against an Android 15 emulator (`devtools/drive_gui.py`), 12 November
2026: 38 of 38 checks with the CustomTkinter window on macOS (Python 3.12, CustomTkinter 5.2.2), Ubuntu
26.04 (Python 3.14, system Pillow without ImageTk) and Windows 11 (Python 3.11), the last two on ARM64
with CustomTkinter 6.0.0, and the teach checks with the plain window on macOS. The checks cover each
step, Pause, Stop, the lock dialog with a typed code, the question about a different app, the
screenshot on the Run step, the PDF report, Check this export, Copy, the theme switch, teaching a
profile by clicks, and the logo in the header and as the window icon.

Needs Python 3 and adb. Pillow, ReportLab and CustomTkinter are optional; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
says what each adds and what happens without it.

What a walk does:

1. Scrolls to the far end of the list without taking screenshots, because an app can reopen a list
   wherever it was left. `--no-rewind` skips this.
2. At each step: takes a screenshot, reads the view tree, takes a second screenshot. The first is
   kept as settled only when the list's area is the same in both pictures. Without Pillow it reads the
   tree twice around one screenshot and compares the trees instead. The first way was chosen because
   on a Pixel 7a a tree read took about 2.3 s and a screenshot 0.3 s. Before the tree read it waits
   until two quick screenshots in a row match, and a thin strip at the right edge, where a scrollbar
   fades out about 2 s after a scroll, is left out of every comparison. On the Pixel 7a, one Signal
   conversation took 175 s for 26 screenshots (6.7 s each), against 449 s for 60 screenshots (7.5 s
   each) at 0.30 with the tree comparison.
   At 0.70 in that conversation, the part of the screen two screenshots share held no text at some
   steps (18 lines in one run), so the text join had nothing to match. See "Transcript" for how those
   frames are now joined by the pictures.
3. Scrolls about 70% of the list height (`--fraction`, default 0.70), so each screenshot is mostly new
   and consecutive frames still overlap. 0.30 is the cautious setting.
   Two frames continue each other when they share a line of text. A shared description does not
   count: labels such as a delivery tick or "Photo" repeat on many bubbles, and in Signal two frames
   that shared no text still shared five of them. With no shared text, the two screenshots are
   compared and must clearly overlap. Otherwise the walker logs a gap, goes back and comes forward in
   smaller moves. Each screenshot's row records how it was joined to the one before. Without Pillow,
   any shared label still counts, as before.
4. Stops when two scrolls in a row change nothing, at `--max-screens`, or when blocked. When every
   item looks alike to the view tree and a scroll moves by whole items, the tree is the same after a
   scroll; the screenshots are then compared, and a clear shift counts as a move, so such a list is
   not taken for its end. (In a made-up test, the walk had stopped after three screens.)
5. Before calling an end confirmed, it scrolls a little the other way. "Nothing moved" can also
   mean the swipe was not taken, and that looked the same as the end of the list in the first run.
   If the screen does not move either way, the stop reason says the end is not confirmed and the
   command exits with 2.

## What it writes

A folder in the known-data logger (KDL) export format, `kdl_<scenario>_<device>_<UTC time>`:

- `actions.jsonl`: one row per thing the tool did (start, each scroll, each screenshot, each stop
  or pause), with the phone's clock, time zone, time since boot and boot count, and a SHA-256 chain
  over the rows, the same chain KDL uses.
- `screens/` and `trees/`: the screenshots and the view trees, named after their row.
- Each screenshot row's `detail` holds the SHA-256 of its image and of its view tree. `detail` is
  inside the chain, so the newest row hash covers every image. Write that hash down somewhere else.
- `text_index.csv`: the text of every labelled item in every tree, for searching.
- `actions.csv` and `manifest.json`.

- `report.pdf`, with `report.pdf.sha256` beside it: the case details, each conversation in reading
  order with every screenshot beside the lines first seen on it (S-numbers), the screenshots that
  added no line after the text, and the result of the export check. On the emulator's 16
  conversations: 32 pages for 66 screenshots, 43 of them distinct. Needs ReportLab. Like the transcript, it is made from the saved files and is not
  in the hash chain.
- `transcript.html` and `transcript.csv`: the text of each walked screen in reading order, each line
  once, beside the screenshot it was first seen on. See "Transcript" below.

KDL's `tools/make_documentation.py` reads the export and checks the chain. `appwalk.py verify` also
checks every image and tree against the hash in its row, and reports files that have no row.

The text of password fields is removed from a view tree before it is saved or hashed. A code the
examiner types at the workstation was in no file of the export in the emulator test.

## Transcript

So nobody has to type what the pictures show, each export gets a transcript made from the view trees:

- The text lines inside the scrolling list of each screen, top to bottom.
- Frames joined into one sequence by the lines two consecutive screenshots share, so a line that was
  on three overlapping screenshots appears once.
- When two consecutive frames share no text (the shared part of the screen holds a picture, for
  example), the screenshots are compared instead: the shift at which the old picture lines up with
  the new one is searched for. If the pictures clearly overlap, the lines are marked `by the
  screenshots`. Otherwise they are marked `NOT JOINED`: look at the pictures there, something may be
  missing between them. The limits were set on Signal on a Pixel 7a: 67 of 69 neighbouring pairs
  were confirmed and 0 of 129 pairs that share nothing. Other apps were not measured.
- For each line: the screenshot it was first seen on, where it sat (left, right, across) and the
  app's own description of it, when the app gives one.

It does not say who sent a message unless there is a profile for the app in `profiles/`. A profile
names the view ids of message text and time lines and how a sent message is marked. With one, each
message gets `sent` or `received` and the basis: "the app's own description" or "side of the screen
(inferred)". Profiles exist for Google Messages and Fossify Messages (checked against ALEAPP on the
emulator) and for WhatsApp, Signal and Discord (made on 12 November 2026 from one conversation each on
a Pixel 7a, from the view ids and their counts; not checked line by line against the pictures). Each
says which version it was checked with. An app can change its ids or layout at any time, so check a
profile's output against the pictures.

A profile can also be taught in the window: **Teach this app** on the Result step shows the export's
screenshots, and the examiner clicks a sent message, a received one, a time, and if there are any, a
picture and an author name (`teach.py`, `teach_gui.py`). For each click the view under it in that
screenshot's view tree gives the id, the bubble around it and its side. Profiles taught this way are
saved in `~/.appwalk/profiles/` and are used before the ones in `profiles/`. On the emulator, a profile
taught with two clicks gave the same sent and received for all 229 Google Messages texts as the
shipped profile; the 16 "Texting with" banner lines, which the shipped profile leaves out by their
wording, came out as messages with no direction, since a click cannot teach a wording. On real
exports from a Pixel 7a, with the clicks placed in code by view id and position, never by what a
message says: Discord's taught profile was the same as the shipped one in every field, and WhatsApp's
added only its bubble view; both gave the same role and direction on every line as the shipped one.
`python3 appwalk.py teach <export folder>` opens the Teach window on an earlier export.

What a profile can say, as JSON in `profiles/<package>.json`:

- `message_ids`, `time_ids`, `ignore_ids`, `ignore_text_prefixes`: which views are messages, times, or
  left out.
- `message_parent_ids`: message text that has no view id of its own, known by its parent's id
  (Discord).
- `author_ids`: who wrote the lines after it, shown in bold (Discord).
- `described_ids`: views with no text whose description goes into the transcript in square brackets,
  such as a picture (`image` in WhatsApp and Discord, `image_view` in Signal). A named view inside
  another named one is left out, so a picture is one line. Not every app's descriptions are worth
  having: taking every described view would have added 215 lines of delivery ticks and thumbnails to
  one Signal conversation, so a profile names the ones it wants.
- `direction_rules`: how a sent message is told from a received one, by the app's description or the
  side of the screen.
- `single_column`: the app shows every message in one column, so lines are placed on the left.
- `bubble_ids`: the view that is a message's bubble. The side of a line is then the side of its
  bubble. Without it, a short reply in a bubble the quote has widened to the far edge read as the
  other side (seen once in Signal, where `body_bubble` is named).

The transcript is a reading aid, made from the saved files, and can be made again with
`python3 appwalk.py transcript <export folder>`. It is not in the hash chain. The screenshots are the
record of what was on the screen; the transcript is what the app reported.

Checked on the emulator exports against ALEAPP: for Google Messages, all 16 threads (227 messages)
came out in exactly ALEAPP's order with the same direction for every message, in one session from the
side of the screen and in another from the app's description. For Fossify Messages, direction matched
for all 226 messages it shows.

## What it does not do

- It opens rows only from the list the examiner put in front, and only the rows chosen. It does not
  look for content on its own in other parts of the app.
- Rows are told apart by their title. If two rows share a title, only the first is opened and the
  log says so.
- Opening a thread marks it read, as it does for a person. On the emulator the conversations that
  were not chosen stayed unread.
- It cannot capture a window the app marks as secure. That frame comes back black and is recorded
  as `blank`. On the emulator, the view tree of one such window (the Settings confirm-PIN screen)
  was still readable.
- It does not know what a list holds on a server. Offline it documents what is on the phone.
- It does not leave the phone unchanged. An opened thread is marked read, and USB debugging stores
  the workstation's key on the phone.
- Relative labels on screen ("Now", "5 minutes") are captured as shown. The row's clock fields say
  when.
- No iOS.

## Tested

Android 15 emulator (`sdk_gphone64_arm64`, API 35), Google Messages `20240528_00_RC01`, 11 November
2026, made-up texts. One thread of 66 texts (60 incoming, 6 outgoing): 17 screenshots, both ends
confirmed, no gaps. Every one of the 66 messages ALEAPP's GoogleMessages artifact reported for that
thread from an extraction of the same emulator was found as a message bubble in at least one view
tree, and no bubble was found that ALEAPP did not report.

`walk-list`, same emulator, 16 conversations and 227 texts: with every row chosen, 16 rows were
found, opened and walked (86 screenshots, about 17 minutes), and all 227 messages ALEAPP reported
were found under the row of the same name. Again on 12 November with the 0.70 default and the
screenshot comparison: 66 screenshots in 786 s (was 86 in 1,044 s), all 227 found, no NOT JOINED
line, and every conversation's messages in the transcript the same, in the same order and direction, as in
the 11 November run. The new run also has two texts that are in neither ALEAPP's output for that
extraction nor the 11 November run. With two rows chosen by title, those two became read and
the other 13 unread conversations stayed unread. With `--skip-list-capture` and one row chosen, no
file of the export named any other conversation.

Between two sessions the same app version gave message bubbles a different view id. The walker does
not use ids, so it was not affected.

Other apps on the same emulator, each checked with `devtools/check_texts.py`, which looks for the
expected texts in the view trees without using view ids:

| App | What it stands for | Screenshots | Texts captured |
|---|---|---|---|
| Google Messages 20240528 | native app | 86, none black | 227 of 227 |
| Fossify Messages 1.10.2 | another native app, same SMS store | 106, none black | 226 of 227 |
| Silence 0.16.12 | app that blocks screenshots | 13, all black | 30 of 30, from the view trees only |
| System HTML Viewer with a made-up chat page | content drawn in a web view | 28, none black | 80 of 80 |

- The one text missing in Fossify Messages is not on its screen either: the app shows the messages
  before and after it and not that one, while ALEAPP reports it from the SMS store. Why the app
  leaves it out is not established. The pictures show what the app shows, which is not always what
  the store holds.
- Fossify Messages exposed a bug, now fixed and tested: a row cut off by the edge of the list lost
  its title line, and its preview line was taken for a seventeenth conversation.
- In the web view every tree listed the whole page, on screen or not. There a tree does not show
  that a text was in that step's picture, and the gap check has nothing to work with.
- When screenshots come back black the command says so and exits with 2.

Not tried: a real phone, WhatsApp, Signal, Telegram or any app that needs an account, a Flutter or
game-engine app, any other Android version, a thread with media, rows that share a title,
right-to-left layouts, a horizontal list.

Unit tests use a made-up phone and need no device:

```bash
python3 -m unittest discover -s tests
```

`devtools/` holds the emulator-only scripts used for the test: seeding texts, taking extractions,
and the comparison with ALEAPP.

## License

MIT. See [LICENSE](LICENSE). Third-party packages AppWalk uses when they are installed, and their
licences, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
