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

For a step-by-step guide to the window, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

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
   open the transcript and the folder, check the export, and copy the latest row hash.

Light and dark themes: it starts in the system's setting on macOS and Windows, and the Light or Dark
button in the header switches. Examiner, phone label, folder and theme are remembered in
`~/.appwalk/settings.json`; the case reference and note are not.

Every run from the window or the command line now also logs the phone's state before and after it,
and a pause as its own row.

It needs a Python whose Tk is 8.6; on macOS the python.org 3.14 build has Tk 9, and the window
refuses to start there.

Driven through its own controls against an Android 15 emulator (`devtools/drive_gui.py`): 29 checks on
macOS, and on Ubuntu 26.04 and Windows 11 (both ARM64) 25 checks before the last addition and the 6
checks of the short run after it. The checks cover each step, Pause, Stop, the lock dialog with a typed
code, the question about a different app, Check this export, Copy, and the theme switch.

Needs Python 3 and adb. Pillow is optional: with it, a black frame is recorded as `blank`.

What a walk does:

1. Scrolls to the far end of the list without taking screenshots, because an app can reopen a list
   wherever it was left. `--no-rewind` skips this.
2. At each step: reads the view tree, takes a screenshot, reads the view tree again. The screenshot
   is kept as settled only when the tree was the same before and after it.
3. Scrolls a part of the list height (`--fraction`, default 0.30), so consecutive frames overlap.
   If a frame shares nothing with the one before, it logs a gap, goes back and comes forward in
   smaller moves.
4. Stops when two scrolls in a row change nothing, at `--max-screens`, or when blocked.
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
  on three overlapping screenshots appears once. A frame that shares nothing with the one before is
  marked `NOT JOINED`.
- For each line: the screenshot it was first seen on, where it sat (left, right, across) and the
  app's own description of it, when the app gives one.

It does not say who sent a message unless there is a profile for the app in `profiles/`. A profile
names the view ids of message text and time lines and how a sent message is marked. With one, each
message gets `sent` or `received` and the basis: "the app's own description" or "side of the screen
(inferred)". Profiles exist for Google Messages and Fossify Messages; each says which version it was
checked with. An app can change its ids or layout at any time, so check a profile's output against
the pictures.

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
were found under the row of the same name. With two rows chosen by title, those two became read and
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

Not chosen yet.
