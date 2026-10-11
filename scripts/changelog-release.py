#!/usr/bin/env python3
"""Collect changelog.d/*.md into a dated CHANGELOG section (stdlib only)."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE_HEADING = re.compile(r"^## (\d{4}\.\d{2}\.\d{2})\s*$", re.M)


def render_fragment(text: str) -> str:
    """Move ordinary inline/reference links from changelog.d to the root."""
    text = re.sub(r"(\]\()\.\./", r"\1", text)
    return re.sub(r"(^ {0,3}\[[^]\n]+\]:\s*<?)\.\./", r"\1", text, flags=re.M)


def release(root: Path, version: str) -> int:
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", version):
        raise ValueError("version must be YYYY.MM.DD")
    datetime.strptime(version, "%Y.%m.%d")
    fragments = sorted((root / "changelog.d").glob("*.md"))
    if not fragments:
        return 0

    changelog = root / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    headings = list(RELEASE_HEADING.finditer(original))
    if re.search(r"^##\s+Unreleased\b", original, re.M | re.I):
        raise ValueError("move Unreleased items to changelog.d first")
    if any(match.group(1) == version for match in headings):
        raise ValueError("version already released; review remaining fragments")
    if headings and version <= headings[0].group(1):
        raise ValueError("version must be newer than the latest release")

    contents = []
    for path in fragments:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("### ") or re.search(r"^#{1,2}\s", text, re.M):
            raise ValueError(f"{path.name}: start with ###; no level 1/2 headings")
        contents.append(render_fragment(text))
    # Keep each fragment's prose/spacing; add a separator only if needed.
    body = ""
    for text in contents:
        if body and not body.endswith("\n\n"):
            body += "\n" if body.endswith("\n") else "\n\n"
        body += text
    body = body.rstrip("\n") + "\n\n"
    position = headings[0].start() if headings else len(original)
    prefix = original[:position]
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n" if prefix.endswith("\n") else "\n\n"
    updated = prefix + f"## {version}\n\n" + body + original[position:]

    # Save notes atomically before deleting any source fragment. A failed
    # cleanup leaves a complete released section and never discards notes.
    descriptor, temporary = tempfile.mkstemp(prefix=".changelog-", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, changelog.stat().st_mode & 0o777)
        os.replace(temporary, changelog)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    for path in fragments:
        path.unlink()
    return len(fragments)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new release date: YYYY.MM.DD")
    args = parser.parse_args()
    try:
        count = release(ROOT, args.version)
    except (OSError, ValueError) as error:
        parser.exit(1, f"changelog-release: {error}\n")
    print(f"Collected {count} changelog fragments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
