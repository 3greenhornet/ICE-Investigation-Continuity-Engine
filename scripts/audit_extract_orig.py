#!/usr/bin/env python3
"""Audit variant: extract the ORIGINAL repomix pack into audit/orig-ice.

Unlike extract_repomix.py (which materialises into the working tree), this
writes to a separate directory so the original can be diffed against the
current ice/ tree without touching it.
"""
import re
from pathlib import Path

SRC = Path("/home/z/my-project/upload/repomix-output.xml")
DST = Path("/home/z/my-project/audit/orig-ice")


def main():
    raw = SRC.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r'<file path="([^"]+)">\n?(.*?)\n?</file>', re.DOTALL)
    count, skipped = 0, []
    for m in pattern.finditer(raw):
        rel, content = m.group(1), m.group(2)
        content = content.replace("&lt;file", "<file")
        target = DST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped.append(rel)
            continue
        target.write_text(content, encoding="utf-8")
        count += 1
    print(f"extracted {count} files into {DST}")
    if skipped:
        print(f"skipped duplicates: {skipped[:10]}")


if __name__ == "__main__":
    main()
