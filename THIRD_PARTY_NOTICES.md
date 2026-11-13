# Third-party software

AppWalk bundles no third-party code. It uses these packages when they are installed; each is optional.

| Package, version tested | Used for | Licence, as read from the wheel | Without it |
|---|---|---|---|
| CustomTkinter 6.0.0 (Tom Schimansky) | the window's look | MIT (the LICENSE file in the wheel). The wheel's `License:` metadata line says "Creative Commons Zero v1.0 Universal" while its classifier says MIT; the LICENSE file is MIT. | the plain Tk window (`appwalk_gui_classic.py`) opens |
| darkdetect 0.8.0 | installed with CustomTkinter | BSD-3-Clause | |
| packaging 26.3 | installed with CustomTkinter | Apache-2.0 OR BSD-2-Clause | |
| ReportLab 4.4.6 (ReportLab Inc.) | `report.pdf` | BSD (the LICENSE file in the wheel) | no `report.pdf`; the rest of the export is written, and the result says why the report is missing |
| Pillow 12.3.0 | telling black screenshots apart, comparing two screenshots to see that the screen had settled, the picture on the Run step, and the pictures in `report.pdf` | MIT-CMU | black frames are not flagged, the view tree is read twice per step instead, the CustomTkinter window's Run step shows no picture (the plain window still shows a smaller one), and there is no `report.pdf` |

On Ubuntu, the system Pillow (python3-pil) comes without its Tk part, PIL.ImageTk, which is in the
separate python3-pil.imagetk package. Without it the CustomTkinter window shows the logo and the latest
screenshot through Tk's own PNG support instead, at a fixed size.

Other versions were not tried, except CustomTkinter 5.2.2, which also passed the window checks on
macOS. On Ubuntu and Windows the window was tested with CustomTkinter 6.0.0 and ReportLab 4.4.6;
Pillow there was 12.1.1 (Ubuntu) and 13.0.0 (Windows).

Fonts: the PDF sets its text in a system font when it finds one (Arial Unicode on macOS, Arial on
Windows, DejaVu Sans on Linux) and embeds the glyphs it uses. Otherwise it uses Helvetica, which is not
embedded. The PDF also lists Helvetica, ReportLab's starting font, which is not embedded. A character
the font cannot draw, most emoji among them, is written as its code (for example [U+1FAC2]).
