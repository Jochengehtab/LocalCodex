#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: set_version.py X.Y.Z[-prerelease]", file=sys.stderr)
        return 2
    version = sys.argv[1].removeprefix("v").strip()
    if not SEMVER.fullmatch(version):
        print(f"invalid semantic version: {version}", file=sys.stderr)
        return 2
    Path("VERSION").write_text(version + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
