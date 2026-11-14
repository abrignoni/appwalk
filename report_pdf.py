#!/usr/bin/env python3
"""Writes report.pdf for an AppWalk export: a cover page, the conversation in reading order with each
screenshot beside the lines first seen on it, and the checks.

The text reads like the chat: lines on the left of the screen on the left, lines on the right on the
right. Each line carries a tag such as S12, the screenshot it was first seen on, which is the one beside
it, so a picture or other media is on the same page as the line that names it. The screenshots go in as
they were saved (lossless).

The report is made from the saved files and can be made again at any time. It is not in the hash chain
it describes; its own SHA-256 is written beside it, in report.pdf.sha256.

Needs ReportLab. Without it, nothing is written and the export is unaffected.
"""
import hashlib
import json
import os
import unicodedata
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fonts tried in order, so text in other scripts shows where the system has a font for it. A character the
# font lacks (most emoji: no system font here draws colour emoji) is written as its code, such as [U+1FAF6],
# instead of an empty box; the transcript files keep every character as reported.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
BOLD_COVERED = set()


def bold_font():
    """A bold face to set author names in. The body font (Arial Unicode on macOS) has none, so <b> did nothing."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in BOLD_CANDIDATES:
        if os.path.exists(path):
            try:
                f = TTFont("AWBold", path)
                pdfmetrics.registerFont(f)
                BOLD_COVERED.clear()
                BOLD_COVERED.update(f.face.charToGlyph)
                return "AWBold"
            except Exception:  # an unreadable font file: try the next one
                continue
    return None


def strong(text, bold):
    """text (already escaped) in bold where the bold face can draw every character of it; otherwise in the
    body font, underlined, so a name in a script the bold face lacks still stands out and still shows."""
    plain = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if bold and all(ord(ch) in BOLD_COVERED or ch == " " for ch in plain):
        return f'<font name="{bold}">{text}</font>'
    return f"<u>{text}</u>"


def font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                f = TTFont("AWBody", path)
                pdfmetrics.registerFont(f)
                COVERED.clear()
                COVERED.update(f.face.charToGlyph)
                return "AWBody", path
            except Exception:  # an unreadable font file: try the next one
                continue
    COVERED.clear()
    COVERED.update(range(32, 256))
    return "Helvetica", None


COVERED = set()
# Invisible marks that only steer how an emoji is drawn (style selectors, the joiner): dropped, not boxed.
INVISIBLE = {0xFE0E, 0xFE0F, 0x200D}


def drawable(s):
    """s with each character the PDF font cannot draw written as its code point."""
    if not COVERED:
        return s
    out = []
    for ch in s:
        c = ord(ch)
        # Format characters (Unicode category Cf: the direction marks U+2066 to U+2069 that Android puts
        # around names and numbers, zero-width joiners) are invisible on the phone too: dropped, not coded.
        if c in INVISIBLE or unicodedata.category(ch) == "Cf":
            continue
        if unicodedata.category(ch) == "Zs":   # any kind of space, such as the narrow one Android puts before PM
            ch, c = " ", 32
        out.append(ch if c in COVERED or ch in "\n\t" else f"[U+{c:04X}]")
    return "".join(out)


def by_height(paras, width, limits):
    """paras cut into runs, the n-th no taller than limits[n] when set width wide. A single paragraph
    taller than its limit still gets a run of its own (ReportLab splits a long paragraph itself only
    outside a table, so such a run is left for the layout to report)."""
    chunks, cur, used, n = [], [], 0.0, 0
    for p in paras:
        h = p.wrap(width, 10_000)[1] + p.getSpaceBefore() + p.getSpaceAfter()
        if cur and used + h > limits[min(n, len(limits) - 1)]:
            chunks.append(cur)
            cur, used, n = [], 0.0, n + 1
        cur.append(p)
        used += h
    chunks.append(cur)
    return chunks


def esc(s):
    return drawable(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def when(row):
    utc = datetime.fromtimestamp(row["utc_ms"] / 1000, tz=timezone.utc)
    local = utc.astimezone(timezone(timedelta(minutes=row["tz_offset_min"])))
    m = abs(row["tz_offset_min"])
    return f"{local:%Y-%m-%d %H:%M:%S} UTC{'+' if row['tz_offset_min'] >= 0 else '-'}{m // 60:02d}:{m % 60:02d}"


def summary_lines(manifest, rows=None):
    """The result in sentences, from the manifest and the log rows."""
    s = manifest["walk"]["summary"]
    shots = manifest["screenshots"]
    out = []

    def clean(w, need_rewind=True):
        return bool(w.get("end_confirmed") and (not need_rewind or w.get("rewind_end_confirmed")) and not w.get("gaps") and not w.get("unsettled"))
    if s.get("mode") == "list":
        out.append(f"{s['items_found']} rows were in the list. {len(s['items_selected'])} were chosen and {s['items_walked']} were opened and documented.")
        for w in s.get("walks", []):
            out.append(f"{w['item']}: {w['screens']} screenshots" + ("" if clean(w) else f". Look at this one: {w.get('stop_reason')}"))
        for t in s.get("items_not_opened", []):
            out.append(f"{t}: could not be opened")
        if s.get("stop_reason"):
            out.append("The run ended early: " + s["stop_reason"])
    else:
        out.append(f"{s['screens']} screenshots of the open screen.")
        if not s.get("end_confirmed") or (s.get("rewind_end_confirmed") is False):
            why = s.get("stop_reason") or ""
            if why.startswith("stopped where the examiner asked"):
                out.append(why[0].upper() + why[1:] + ".")
            else:
                out.append(f"Look at this: the end was not confirmed ({why}).")
        else:
            out.append("Both ends of the list were reached.")
        if s.get("unsettled"):
            out.append(f"Look at this: {s['unsettled']} screenshot(s) taken while the screen was still changing.")
    # Gaps from the log's own events, not the summary's count: before 0.1.1 the count included gaps the walk
    # then went back over.
    events = [json.loads(r["detail"]) for r in (rows or []) if r["action"] == "continuity_gap" and r.get("detail")]
    retried = sum(1 for d in events if d.get("going_back_to_retry"))
    left = len(events) - retried
    if retried:
        out.append(f"{retried} time(s) a screenshot shared nothing with the one before, so the walk went back and took "
                   "that part again in smaller moves. The conversation pages say where.")
    if left:
        out.append(f"Look at this: {left} place(s) where two screenshots shared nothing and could not be taken again. "
                   "Lines may be missing there; the conversation pages mark the place.")
    blank = sum(1 for x in shots if x["status"] == "blank")
    if blank:
        out.append(f"{blank} screenshots are black: the app blocks screenshots there. The text for those screens is in the conversation pages only.")
    return out


def build(export, out_name="report.pdf"):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    import appwalk
    import transcript as T

    body, font_path = font()
    bold = bold_font()
    manifest = json.load(open(os.path.join(export, "manifest.json"), encoding="utf-8"))
    rows = [json.loads(x) for x in open(os.path.join(export, "actions.jsonl"), encoding="utf-8") if x.strip()]
    lines, notes, _ = T.build(export)
    case = next((json.loads(r["detail"]) for r in rows if r["action"] == "case_information"), {})
    states = [json.loads(r["detail"]) for r in rows if r["action"] == "device_state"]
    app = manifest["apps_under_test"][0]
    last_hash = manifest["hash_chain"]["last_row_hash"]
    folder = os.path.basename(os.path.normpath(export))

    ink, muted, accent = colors.HexColor("#1B1F24"), colors.HexColor("#5F6B7A"), colors.HexColor("#1B45AD")
    st = {
        "title": ParagraphStyle("t", fontName=body, fontSize=20, leading=24, textColor=ink, spaceAfter=10),
        "h": ParagraphStyle("h", fontName=body, fontSize=13, leading=17, textColor=ink, spaceBefore=10, spaceAfter=6),
        "p": ParagraphStyle("p", fontName=body, fontSize=10, leading=14, textColor=ink),
        "small": ParagraphStyle("s", fontName=body, fontSize=8.5, leading=11, textColor=muted),
        "left": ParagraphStyle("l", fontName=body, fontSize=9.5, leading=12, textColor=ink, alignment=TA_LEFT,
                               rightIndent=1.5 * inch, spaceAfter=3),
        "right": ParagraphStyle("r", fontName=body, fontSize=9.5, leading=12, textColor=accent, alignment=TA_RIGHT,
                                leftIndent=1.5 * inch, spaceAfter=3),
        "across": ParagraphStyle("a", fontName=body, fontSize=8, leading=10, textColor=muted, alignment=TA_CENTER, spaceAfter=3),
        "note": ParagraphStyle("n", fontName=body, fontSize=9.5, leading=13, textColor=ink, backColor=colors.HexColor("#FFF3CD"),
                               borderPadding=5, spaceBefore=4, spaceAfter=6),
        "cap": ParagraphStyle("c", fontName=body, fontSize=7.5, leading=9.5, textColor=muted, alignment=TA_CENTER),
    }

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(body, 7.5)
        canvas.setFillColor(muted)
        ref = case.get("reference") or folder
        canvas.drawString(0.7 * inch, 0.52 * inch, f"AppWalk report · {ref}")
        canvas.drawRightString(letter[0] - 0.7 * inch, 0.52 * inch, f"page {doc.page}")
        canvas.drawString(0.7 * inch, 0.38 * inch, f"Latest row hash {last_hash}")
        canvas.restoreState()

    story = []
    # Cover
    story.append(Paragraph("AppWalk report", st["title"]))
    facts = [("Examiner", case.get("examiner")), ("Case or reference", case.get("reference")), ("Note", case.get("note")),
             ("App", f"{app['package']} {app.get('version_name_at_add') or ''}".strip())]
    if states:
        s0 = states[0]
        facts.append(("Phone", f"{s0.get('manufacturer') or ''} {s0.get('model') or ''}, Android {s0.get('android') or '?'}".strip()))
        on = lambda v: "not reported" if v is None else ("on" if v else "off")
        facts.append(("Airplane mode", " · ".join(f"{s.get('when', '')}: {on(s.get('airplane_mode_on'))}" for s in states)))
    walk_rows = [r for r in rows if r["action"] != "file_attached"]   # a file added later is not the walk
    facts += [("Started", when(walk_rows[0])), ("Ended", when(walk_rows[-1])), ("Export folder", folder),
              ("Latest row hash", last_hash), ("Made by", f"AppWalk {appwalk.VERSION}")]
    t = Table([[Paragraph(esc(k), st["small"]), Paragraph(esc(str(v)), st["p"])] for k, v in facts if v],
              colWidths=[1.6 * inch, 5.2 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [t, Paragraph("Result", st["h"])]
    story += [Paragraph(esc(x), st["p"]) for x in summary_lines(manifest, rows)]
    for n in notes:   # the same notes the transcript page shows in yellow at its top, given the same weight here
        story.append(Spacer(1, 4))
        story.append(Paragraph(esc(n), st["note"]))
    atts = manifest.get("attachments") or []
    if atts:
        # Files the examiner added after the walk, such as an app's own chat export: their hashes are in the
        # chain, and for a zip each member is listed in the row (name, size, CRC-32).
        story.append(Paragraph("Files added after the walk", st["h"]))
        for d in atts:
            src = ("pulled from the phone, " + d.get("phone_path", "") + ", and hashed there too" if d.get("source") == "phone"
                   else "copied from this computer")
            story.append(Paragraph(esc(f"attachments/{d['file']}: {d['bytes']:,} bytes, {src}. SHA-256 {d['sha256']}"), st["p"]))
            if d.get("note"):
                story.append(Paragraph(esc("Note: " + d["note"]), st["small"]))
            members = d.get("zip_members")
            if members:
                kinds = {}
                for m in members:
                    ext = os.path.splitext(m["name"])[1].lower() or "(no extension)"
                    kinds[ext] = kinds.get(ext, 0) + 1
                story.append(Paragraph(esc(f"A zip of {len(members)} files: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))), st["small"]))
                for m in members[:60]:
                    story.append(Paragraph(esc(f"{m['name']} · {m['bytes']:,} bytes · CRC-32 {m['crc32']}"), st["small"]))
                if len(members) > 60:
                    story.append(Paragraph(esc(f"and {len(members) - 60} more, listed in the file's row in actions.jsonl"), st["small"]))
            story.append(Spacer(1, 4))
    story.append(Paragraph("How to read this report", st["h"]))
    for x in ("The screenshots are the record of what was on the screen. The text beside each is what the app "
              "reported for those screens, in reading order, each line once. That is usually the same text and is not "
              "guaranteed to be.",
              "Lines on the left sat on the left of the screen, lines on the right on the right, as on the transcript "
              "page. Grey lines are times and other text the app showed; centred ones spanned the screen, such as dates. "
              "Sent and received appear only for apps AppWalk has a profile for, with the basis for it, in small grey "
              "type under the line, with the app's own description where it says more than the line.",
              "A line in square brackets is the description the app gave an item with no text, such as a picture. "
              "A bold line is the name of who wrote the lines after it, where the app shows one.",
              "A code such as [U+1FAC2] is a character this report's font cannot draw, most often an emoji; look it up "
              "by that code, or see the transcript page or the screenshot, which show it as reported. A code from "
              "U+E000 to U+F8FF is in Unicode's private use area, which has no meaning of its own: only the app's own "
              "font draws it, so the screenshot shows what it looked like. Invisible marks, such as the direction marks "
              "around names and numbers, are left out here and kept in transcript.csv.",
              "Each screenshot is printed beside the lines first seen on it, and each line's tag, such as S12, names that "
              "screenshot. A screenshot the same as the one before it is printed once, with both numbers.",
              "This report is made from the export's files and is not in its hash chain. Its own SHA-256 is in "
              "report.pdf.sha256 beside it. The export's latest row hash, on every page, covers every row and every file "
              "of the export."):
        story.append(Paragraph(esc(x), st["p"]))
        story.append(Spacer(1, 4))

    # Conversation pages: each screenshot beside the lines first seen on it, so a picture or other media
    # is on the same page as the line that names it.
    st["cl"] = ParagraphStyle("cl", parent=st["left"], rightIndent=0.5 * inch, leftIndent=0)
    st["cr"] = ParagraphStyle("cr", parent=st["right"], leftIndent=0.5 * inch, rightIndent=0)
    by_step = {}
    for r in lines:
        by_step.setdefault(str(r["first_seen_step"]), []).append(r)

    def line_para(r):
        """One line as the transcript page shows it: on the side it sat, times and other lines in grey on
        that same side, and under it the direction, the app's description where it adds something, and
        whether it joined the screenshot before."""
        tag = f'<font size="7" color="#5F6B7A">S{r["first_seen_step"]}</font>'
        shown = f"[{r['text']}]" if r["role"] == "described" else r["text"]
        text = esc(shown)
        if r["role"] == "described":
            text = f"<i>{text}</i>"
        elif r["role"] == "author":
            text = strong(text, bold)
        elif r["role"] in ("time", "other"):
            text = f'<font size="8" color="#5F6B7A">{text}</font>'
        meta = [x for x in (r["direction"], r["direction_basis"]) if x]
        desc = r.get("app_description") or ""
        if desc and desc.strip() not in (r["text"].strip(), shown.strip()):
            meta.append(desc)
        if r["joined_to_previous_frame"] == "NO":
            meta.append("NOT JOINED to the screenshot before: lines may be missing or repeated here")
        elif r["joined_to_previous_frame"] == "by the screenshots":
            meta.append("joined to the screenshot before by the pictures (no shared text)")
        extra = f'<br/><font size="7" color="#5F6B7A">{esc(" · ".join(meta))}</font>' if meta else ""
        if r["side"] == "across":
            return Paragraph(f"{text} &nbsp; {tag}{extra}", st["across"])
        if r["side"] == "right":
            return Paragraph(f"{text} &nbsp;{tag}{extra}", st["cr"])
        return Paragraph(f"{tag}&nbsp; {text}{extra}", st["cl"])

    by_entry = {r["entry_id"]: r for r in rows if r["action"] == "screenshot"}
    shots, last = [], None
    for s in sorted(manifest["screenshots"], key=lambda x: x["row_seq"]):
        r = by_entry.get(s["entry_id"])
        d = json.loads(r["detail"]) if r else {}
        step = d.get("step", "?")
        # The same picture again (same SHA-256, as when the end of a short conversation is checked) is
        # printed once: its S-number and any lines first seen on it go with the one before.
        if last and last["sha"] == s["sha256_at_capture"] and last["item"] == d.get("item"):
            last["also"].append(step)
            continue
        last = {"sha": s["sha256_at_capture"], "item": d.get("item"), "also": [], "step": step, "row": r,
                "path": os.path.join(export, "screens", s["file"]), "status": s["status"]}
        shots.append(last)

    # A walk that reveals older content above takes its screenshots newest first. Its rows are put back in
    # reading order, so the text runs top to bottom as in the chat; the S-numbers keep the capture order.
    reveal = {}
    for r in rows:
        if r["action"] == "walk_start":
            d = json.loads(r["detail"]) if r.get("detail") else {}
            reveal[d.get("item")] = d.get("reveal", "above")
    groups = []
    for c in shots:
        if not groups or groups[-1][0] != c["item"]:
            groups.append((c["item"], []))
        groups[-1][1].append(c)
    groups = [(it, list(reversed(g)) if reveal.get(it, "above") == "above" else g) for it, g in groups]

    def caption(c):
        steps = [c["step"]] + c["also"]
        names = "".join(f'<a name="S{x}"/>' for x in steps)
        same = f'<br/>same picture as {", ".join("S" + str(x) for x in c["also"])}' if c["also"] else ""
        return Paragraph(f'{names}S{c["step"]} · {esc(when(c["row"])[11:19]) if c["row"] else ""}<br/>'
                         f'{c["sha"][:16]}{" · " + c["status"] if c["status"] != "ok" else ""}{same}', st["cap"])

    story.append(PageBreak())
    used = set()
    for it, group in groups:
        heading = Paragraph(esc(it or "The screen that was open"), st["h"])
        quiet = []   # screenshots on which no line was seen first, such as the checks at either end
        for c in group:
            steps = [c["step"]] + c["also"]
            used.update(str(x) for x in steps)
            mine = [r for x in steps for r in by_step.get(str(x), [])]
            if not mine:
                quiet.append(c)
                continue
            # A screenshot is at most 3.9 in tall, so two rows and a heading fit a page; a long block of text continues below.
            img = Image(c["path"], width=2.3 * inch, height=3.9 * inch, kind="proportional")
            paras = [line_para(r) for r in mine]
            # A table row cannot break across pages, so the text beside a screenshot is cut by its measured
            # height, not by a count of lines: a line with its grey note under it, in a wider font (DejaVu
            # on Linux), is taller, and a fixed count made a row taller than the page (LayoutError).
            chunks = by_height(paras, 4.4 * inch - 12, [8.3 * inch] + [9.0 * inch] * len(paras))
            t = Table([[chunks[0], [img, caption(c)]]], colWidths=[4.4 * inch, 2.5 * inch])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "CENTER"),
                                   ("LINEABOVE", (0, 0), (-1, 0), 0.4, colors.HexColor("#D9DEE5")),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            story.append(KeepTogether([heading, t]) if heading else t)   # a heading never ends a page alone
            heading = None
            for part in chunks[1:]:
                more = Table([[part, Paragraph(f"S{c['step']}, continued", st["cap"])]], colWidths=[4.4 * inch, 2.5 * inch])
                more.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story.append(more)
        if quiet:
            # Kept with the conversation, three to a row, after its text: part of the record, nothing new to read.
            note = Paragraph(f"{'Screenshots' if len(quiet) > 1 else 'Screenshot'} of this "
                             f"{'conversation' if it else 'screen'} on which no line was first seen, such as the "
                             "checks that its ends were reached:", st["small"])
            cells = [[Image(c["path"], width=2.0 * inch, height=3.4 * inch, kind="proportional"), caption(c)] for c in quiet]
            for i in range(0, len(cells), 3):
                three = cells[i:i + 3] + [["", ""]] * (3 - len(cells[i:i + 3]))
                g = Table([[x[0] for x in three], [x[1] for x in three]], colWidths=[2.3 * inch] * 3)
                g.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
                story.append(KeepTogether(([heading] if heading else []) + ([note] if i == 0 else []) + [g]))
                heading = None
    left = [r for r in lines if str(r["first_seen_step"]) not in used]
    if left:
        story.append(Paragraph("Lines whose screenshot is not in the export", st["h"]))
        story += [line_para(r) for r in left]

    # Checks
    story.append(PageBreak())
    story.append(Paragraph("Checks", st["h"]))
    for name, result, ok in appwalk.verify_export(export):
        story.append(Paragraph(f"{'ok' if ok else 'PROBLEM'} · {esc(name)}: {esc(result)}", st["p"]))
    story.append(Paragraph("What this report does not show", st["h"]))
    for x in ("A screen the app protects from screenshots comes back black. Its text may still be in the conversation pages.",
              "Only what the phone had. Older messages that load from the network or a backup are documented as far as the phone showed them.",
              "Only the conversations that were opened. Archived or hidden conversations need their own run.",
              f"Characters the report's font cannot draw are written as their code{' (font: ' + os.path.basename(font_path) + ')' if font_path else ''}. "
              "transcript.csv and transcript.html keep every character as the app reported it."):
        story.append(Paragraph(esc(x), st["p"]))
        story.append(Spacer(1, 4))

    out = os.path.join(export, out_name)
    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch,
                            bottomMargin=0.8 * inch, title="AppWalk report", author=case.get("examiner") or "",
                            subject=folder, creator=f"AppWalk {appwalk.VERSION}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(out + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{digest}  {out_name}\n")
    return out, digest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: report_pdf.py <export folder>")
    path, digest = build(sys.argv[1])
    print(f"wrote {path}\nsha256 {digest}")
