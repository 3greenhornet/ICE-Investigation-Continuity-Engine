#!/usr/bin/env python3
"""Extract a repomix XML pack into a real directory tree.

Handles the repomix format:
  <file path="...">content</file>
Files may appear inside <files>...</files>. Content is not XML-escaped
by repomix for the body (it writes raw text between tags), but we still
guard against basic entity escapes.
"""
import re
import sys
import os
from pathlib import Path

SRC = "/home/z/my-project/upload/repomix-output.xml"
DST = "/home/z/my-project/ice"

def main():
    raw = Path(SRC).read_text(encoding="utf-8", errors="replace")
    # Find the <file path="..."> ... </file> blocks (non-greedy, DOTALL).
    pattern = re.compile(r'<file path="([^"]+)">\n?(.*?)\n?</file>', re.DOTALL)
    count = 0
    skipped = []
    for m in pattern.finditer(raw):
        rel, content = m.group(1), m.group(2)
        # Basic unescape for stray XML entities (repomix usually keeps body raw)
        content = content.replace("&lt;file", "<file")
        target = Path(DST) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # keep first occurrence (files sorted by git change count; dupes unlikely)
            skipped.append(rel)
            continue
        target.write_text(content, encoding="utf-8")
        count += 1
    print(f"extracted {count} files into {DST}")
    if skipped:
        print(f"skipped duplicates: {skipped[:10]}")

if __name__ == "__main__":
    main()
