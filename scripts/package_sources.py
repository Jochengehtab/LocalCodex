#!/usr/bin/env python3
"""Package the exact tracked project and fetched native dependency sources for a release."""

from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import tarfile


DEPENDENCIES = ("sdl", "imgui", "implot", "httplib", "json")


def package(destination: Path, deps: Path, root: Path) -> None:
    sources = [deps / f"{name}-src" for name in DEPENDENCIES]
    for source in sources:
        if not source.is_dir():
            raise ValueError(f"Missing dependency source directory: {source}")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        for name in tracked:
            if not name:
                continue
            relative = Path(name)
            if relative.parts[0] in {"build", "out", "dist", ".codex-local", ".venv"}:
                raise ValueError(f"Generated path is tracked: {name}")
            if relative.suffix in {".wav", ".sqlite3", ".db", ".gguf", ".exe"}:
                continue
            if (root / relative).is_symlink():
                raise ValueError(f"Review tracked symlink before packaging: {name}")
            archive.add(root / relative, arcname=f"localcodex/{relative.as_posix()}", recursive=False)
        for source in sources:
            archive.add(
                source,
                arcname=f"third_party/{source.name}",
                filter=lambda item: None if ".git" in Path(item.name).parts else item,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--deps", type=Path, required=True)
    args = parser.parse_args()
    package(args.destination, args.deps, Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    main()
