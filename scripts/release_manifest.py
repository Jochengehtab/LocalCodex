#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: release_manifest.py VERSION DIST", file=sys.stderr)
        return 2
    version = sys.argv[1].lstrip("v")
    directory = Path(sys.argv[2])
    assets = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name in {"release-manifest.json", "SHA256SUMS"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append({"name": path.name, "sha256": digest, "size": path.stat().st_size})
    manifest = {
        "schema_version": 1,
        "version": version,
        "repository": "Jochengehtab/LocalCodex",
        "platforms": ["windows-x64-wsl2", "linux-x64"],
        "assets": assets,
    }
    (directory / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    with (directory / "SHA256SUMS").open("w", encoding="utf-8", newline="\n") as output:
        for asset in assets:
            output.write(f"{asset['sha256']}  {asset['name']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
