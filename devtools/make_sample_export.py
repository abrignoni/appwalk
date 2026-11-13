#!/usr/bin/env python3
"""Writes a small AppWalk export from the test suite's made-up phone, for checking a build where no
phone or emulator is connected (the build workflow uses it).

Usage: python3 devtools/make_sample_export.py <folder>
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
import appwalk  # noqa: E402
from test_appwalk import FakePhone, PKG  # noqa: E402


def main(out):
    os.makedirs(out, exist_ok=True)
    phone = FakePhone(count=30)
    log = appwalk.Log(out, "sample", "fake", phone)
    walker = appwalk.Walker(phone, log, PKG, sleep=lambda s: None, say=lambda *a: None)
    summary = walker.run()
    appwalk.write_export(log, phone, PKG, walker.app_version, summary, {})
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
