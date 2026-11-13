#!/usr/bin/env python3
"""Builds the two AppWalk programs with PyInstaller, checks that the built programs run, and packs them.

    python3 devtools/build_binaries.py [--out dist-release]

Needs PyInstaller, CustomTkinter, ReportLab and Pillow in the Python that runs it. On Linux the window
check needs a display (in CI: xvfb-run).

The programs, one folder each (PyInstaller onedir):
  AppWalk      the window (AppWalk.app on macOS)
  appwalk-cli  the command line (not "appwalk": Windows takes that for the same name as AppWalk)

A build counts only when the built programs pass these checks, run from the build itself:
  - the window opens and reports, through APPWALK_SMOKE, that it runs packaged, with Tk 8.6, the
    logo, its four steps and ReportLab;
  - the command line checks a made-up export (devtools/make_sample_export.py) and writes its report.

The pack is a .zip on macOS (made with ditto, which keeps the app's links) and Windows, and a .tar.gz
on Linux (which keeps the programs executable), named appwalk-<version>-<system>-<arch>. adb is not
in it: the Android SDK licence (clause 3.4) does not allow passing it on. The builds are not signed.
Exit status 0 only when every step passed.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import appwalk  # noqa: E402

SYSTEM = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
ARCH = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower(), platform.machine().lower())

NOTE = """AppWalk {version} for {system} ({arch})

AppWalk      the window. Open it to document an app on a connected Android phone.
appwalk-cli  the same from the command line: appwalk-cli --help

NOT SIGNED. These builds are not code-signed yet.
- macOS blocks an unsigned app the first time: open it, then allow it in System Settings, Privacy &
  Security ("Open Anyway").
- Windows SmartScreen may warn about an unknown publisher: More info, Run anyway.

adb is NOT included (the Android SDK licence does not allow passing it on). Install Android's
SDK Platform-Tools once: https://developer.android.com/tools/releases/platform-tools
AppWalk finds adb on the PATH, in the usual install places, or where APPWALK_ADB points.

The user manual: https://github.com/abrignoni/appwalk/blob/main/docs/USER_MANUAL.md
Licence: MIT (LICENSE). Third-party software inside: THIRD_PARTY_NOTICES.md.
"""


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, **kw)


def build(work):
    common = ["--noconfirm", "--clean", "--distpath", os.path.join(work, "dist"), "--workpath", os.path.join(work, "build"),
              "--specpath", work, "--paths", ROOT,
              "--add-data", f"{os.path.join(ROOT, 'assets')}{os.pathsep}assets",
              "--add-data", f"{os.path.join(ROOT, 'profiles')}{os.pathsep}profiles",
              "--collect-data", "customtkinter", "--collect-data", "reportlab"]
    for m in ("appwalk_gui_classic", "teach_gui", "report_pdf", "transcript", "teach"):
        common += ["--hidden-import", m]
    icon = os.path.join(ROOT, "assets", {"macos": "appwalk_1024.png", "windows": "appwalk.ico"}.get(SYSTEM, "appwalk_256.png"))
    ok = run([sys.executable, "-m", "PyInstaller", *common, "--windowed", "--name", "AppWalk", "--icon", icon,
              os.path.join(ROOT, "appwalk_gui.py")]).returncode == 0
    ok = ok and run([sys.executable, "-m", "PyInstaller", *common, "--console", "--name", "appwalk-cli",
                     os.path.join(ROOT, "appwalk.py")]).returncode == 0
    return ok


def programs(dist):
    exe = ".exe" if SYSTEM == "windows" else ""
    window = (os.path.join(dist, "AppWalk.app", "Contents", "MacOS", "AppWalk") if SYSTEM == "macos"
              else os.path.join(dist, "AppWalk", "AppWalk" + exe))
    return window, os.path.join(dist, "appwalk-cli", "appwalk-cli" + exe)


def check(dist, work):
    window, cli = programs(dist)
    problems = []
    smoke = os.path.join(work, "smoke.json")
    env = dict(os.environ, APPWALK_SMOKE=smoke)
    try:
        run([window], env=env, timeout=180)
    except subprocess.TimeoutExpired:
        problems.append("the window did not close by itself within 180 s")
    facts = {}
    try:
        with open(smoke, encoding="utf-8") as f:
            facts = json.load(f)
    except (OSError, ValueError):
        problems.append("the window wrote no self-check")
    print("window self-check:", facts, flush=True)
    want = {"frozen": True, "tk": "8.6", "pages": 4, "reportlab": True, "assets": True}
    problems += [f"window {k} is {facts.get(k)!r}, not {v!r}" for k, v in want.items() if facts and facts.get(k) != v]
    if facts and facts.get("logo") in (None, "NoneType"):
        problems.append("the window has no logo")
    sample = os.path.join(work, "sample")
    run([sys.executable, os.path.join(ROOT, "devtools", "make_sample_export.py"), sample], check=True)
    verify = run([cli, "verify", sample], capture_output=True, text=True)
    print(verify.stdout, flush=True)
    if verify.returncode != 0 or "hash chain: intact" not in verify.stdout:
        problems.append("appwalk-cli verify did not pass the sample export")
    os.remove(os.path.join(sample, "report.pdf"))
    report = run([cli, "report", sample], capture_output=True, text=True)
    if report.returncode != 0 or not os.path.getsize(os.path.join(sample, "report.pdf")):
        problems.append("appwalk-cli report did not write a report: " + (report.stderr or report.stdout)[-300:])
    return problems


def pack(dist, out):
    version = appwalk.VERSION
    name = f"appwalk-{version}-{SYSTEM}-{ARCH}"
    stage = os.path.join(out, name)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    for item in ("AppWalk.app" if SYSTEM == "macos" else "AppWalk", "appwalk-cli"):
        src = os.path.join(dist, item)
        if SYSTEM == "macos":
            run(["ditto", src, os.path.join(stage, item)], check=True)   # keeps the app's links
        else:
            shutil.copytree(src, os.path.join(stage, item), symlinks=True)
    for f in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy(os.path.join(ROOT, f), stage)
    with open(os.path.join(stage, "README.txt"), "w", encoding="utf-8", newline="\r\n" if SYSTEM == "windows" else "\n") as f:
        f.write(NOTE.format(version=version, system=SYSTEM, arch=ARCH))
    if SYSTEM == "macos":
        archive = os.path.join(out, name + ".zip")
        run(["ditto", "-c", "-k", "--keepParent", stage, archive], check=True)
    elif SYSTEM == "windows":
        archive = shutil.make_archive(os.path.join(out, name), "zip", out, name)
    else:
        archive = os.path.join(out, name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as t:
            t.add(stage, arcname=name)
    shutil.rmtree(stage)
    return archive


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist-release"))
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix="appwalk-build-")
    os.makedirs(a.out, exist_ok=True)
    if not build(work):
        print("BUILD FAILED", flush=True)
        return 1
    problems = check(os.path.join(work, "dist"), work)
    if problems:
        print("CHECKS FAILED:\n  " + "\n  ".join(problems), flush=True)
        return 1
    archive = pack(os.path.join(work, "dist"), a.out)
    print(f"OK {archive} ({os.path.getsize(archive) // (1024 * 1024)} MB)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
