#!/usr/bin/env python3
"""Writes report.pdf for an AppWalk export: a cover page, the conversation in reading order, the
screenshots six to a page, and the checks.

The conversation pages read like the chat: lines on the left of the screen on the left, lines on the
right on the right. Each line carries a tag such as S12, the screenshot it was first seen on, and the tag
links to that screenshot in the picture pages. The screenshots go in as they were saved (lossless).

The report is made from the saved files and can be made again at any time. It is not in the hash chain
it describes; its own SHA-256 is written beside it, in report.pdf.sha256.

Needs ReportLab. Without it, nothing is written and the export is unaffected.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fonts tried in order, so text in other scripts shows where the system has a font for it. A character the
# font lacks prints as an empty box; the transcript files keep every character as reported.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("AWBody", path))
                return "AWBody", path
            except Exception:  # an unreadable font file: try the next one
                continue
    return "Helvetica", None


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def when(row):
    utc = datetime.fromtimestamp(row["utc_ms"] / 1000, tz=timezone.utc)
    local = utc.astimezone(timezone(timedelta(minutes=row["tz_offset_min"])))
    m = abs(row["tz_offset_min"])
    return f"{local:%Y-%m-%d %H:%M:%S} UTC{'+' if row['tz_offset_min'] >= 0 else '-'}{m // 60:02d}:{m % 60:02d}"


def summary_lines(manifest):
    """The result in sentences, from the manifest alone."""
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
        out.append(f"{s['screens']} screenshots of the open screen" + ("." if clean(s, s.get("rewind_end_confirmed") is not None) else f". Look at this: {s.get('stop_reason')}"))
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
    facts += [("Started", when(rows[0])), ("Ended", when(rows[-1])), ("Export folder", folder),
              ("Latest row hash", last_hash), ("Made by", f"AppWalk {appwalk.VERSION}")]
    t = Table([[Paragraph(esc(k), st["small"]), Paragraph(esc(str(v)), st["p"])] for k, v in facts if v],
              colWidths=[1.6 * inch, 5.2 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [t, Paragraph("Result", st["h"])]
    story += [Paragraph(esc(x), st["p"]) for x in summary_lines(manifest)]
    story.append(Paragraph("How to read this report", st["h"]))
    for x in ("The screenshots are the record of what was on the screen. The conversation pages are the text the app "
              "reported for those screens, in reading order, each line once. That is usually the same text and is not "
              "guaranteed to be.",
              "Lines on the left sat on the left of the screen, lines on the right on the right. Grey centred lines span "
              "the screen, such as dates. Sent and received appear only for apps AppWalk has a profile for, with the "
              "basis for it.",
              "Each line's tag, such as S12, names the screenshot it was first seen on. Click it to go to that screenshot.",
              "This report is made from the export's files and is not in its hash chain. Its own SHA-256 is in "
              "report.pdf.sha256 beside it. The export's latest row hash, on every page, covers every row and every file "
              "of the export."):
        story.append(Paragraph(esc(x), st["p"]))
        story.append(Spacer(1, 4))
    for n in notes:
        story.append(Paragraph(esc(n), st["small"]))

    # Conversation pages
    story.append(PageBreak())
    item = object()
    for r in lines:
        if r["item"] != item:
            item = r["item"]
            story.append(Paragraph(esc(item or "The screen that was open"), st["h"]))
        tag = f'<a href="#S{r["first_seen_step"]}" color="#5F6B7A">S{r["first_seen_step"]}</a>'
        label = " · ".join(x for x in (r["direction"], r["direction_basis"]) if x)
        if r["role"] in ("time", "other") or r["side"] == "across":
            story.append(Paragraph(f"{esc(r['text'])} &nbsp; {tag}", st["across"]))
        else:
            extra = f' <font size="7" color="#5F6B7A">· {esc(label)}</font>' if label else ""
            small_tag = f'<font size="7">{tag}</font>'
            if r["side"] == "right":
                story.append(Paragraph(f"{esc(r['text'])}{extra} &nbsp;{small_tag}", st["right"]))
            else:
                story.append(Paragraph(f"{small_tag}&nbsp; {esc(r['text'])}{extra}", st["left"]))

    # Picture pages: six to a page
    story.append(PageBreak())
    story.append(Paragraph("Screenshots", st["h"]))
    by_entry = {r["entry_id"]: r for r in rows if r["action"] == "screenshot"}
    cells, last = [], None
    for s in sorted(manifest["screenshots"], key=lambda x: x["row_seq"]):
        r = by_entry.get(s["entry_id"])
        d = json.loads(r["detail"]) if r else {}
        step = d.get("step", "?")
        path = os.path.join(export, "screens", s["file"])
        # The same picture again (same SHA-256, as when the end of a short conversation is checked) is
        # printed once: its S-number goes on the caption of the one before, and links to it land there.
        if last and last["sha"] == s["sha256_at_capture"] and last["item"] == d.get("item"):
            last["also"].append(step)
            continue
        # Two rows of three must fit a letter page with its margins, captions and heading: 4 in tall at most.
        img = Image(path, width=2.1 * inch, height=4.0 * inch, kind="proportional")
        last = {"sha": s["sha256_at_capture"], "item": d.get("item"), "also": [], "img": img,
                "head": f'S{step} · {esc(when(r)[11:19]) if r else ""}{" · " + esc(d.get("item") or "") if d.get("item") else ""}',
                "tail": f'{s["sha256_at_capture"][:16]}{" · " + s["status"] if s["status"] != "ok" else ""}', "step": step}
        cells.append(last)
    for c in cells:
        names = "".join(f'<a name="S{x}"/>' for x in [c["step"]] + c["also"])
        same = f'<br/>same picture as {", ".join("S" + str(x) for x in c["also"])}' if c["also"] else ""
        c["cell"] = [c["img"], Paragraph(f'{names}{c["head"]}<br/>{c["tail"]}{same}', st["cap"])]
    cells = [c["cell"] for c in cells]
    for i in range(0, len(cells), 6):
        group = cells[i:i + 6]
        grid = []
        for row_start in range(0, len(group), 3):
            three = group[row_start:row_start + 3] + [["", ""]] * (3 - len(group[row_start:row_start + 3]))
            grid.append([c[0] for c in three])
            grid.append([c[1] for c in three])
        g = Table(grid, colWidths=[2.25 * inch] * 3)
        g.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(KeepTogether([g]))
        if i + 6 < len(cells):
            story.append(PageBreak())

    # Checks
    story.append(PageBreak())
    story.append(Paragraph("Checks", st["h"]))
    for name, result, ok in appwalk.verify_export(export):
        story.append(Paragraph(f"{'ok' if ok else 'PROBLEM'} · {esc(name)}: {esc(result)}", st["p"]))
    story.append(Paragraph("What this report does not show", st["h"]))
    for x in ("A screen the app protects from screenshots comes back black. Its text may still be in the conversation pages.",
              "Only what the phone had. Older messages that load from the network or a backup are documented as far as the phone showed them.",
              "Only the conversations that were opened. Archived or hidden conversations need their own run.",
              f"Characters the report's font lacks print as an empty box{' (font: ' + os.path.basename(font_path) + ')' if font_path else ''}. "
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
