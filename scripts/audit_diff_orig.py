#!/usr/bin/env python3
"""Diff the ORIGINAL repomix tree against the current ice/ tree.

Reports:
  - files present in original but MISSING from current (possible deletions)
  - files added in current (the improvements pass)
  - files modified (content differs)

Exit prints a compact summary plus full lists to audit/diff-report.txt.
"""
import hashlib
from pathlib import Path

ORIG = Path("/home/z/my-project/audit/orig-ice")
CUR = Path("/home/z/my-project/ice")
OUT = Path("/home/z/my-project/audit/diff-report.txt")

EXCLUDE_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules",
                 ".venv", "verification"}


def rel_files(root: Path):
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in EXCLUDE_PARTS for part in rel.parts):
            continue
        out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main():
    orig = rel_files(ORIG)
    cur = rel_files(CUR)

    missing = sorted(set(orig) - set(cur))
    added = sorted(set(cur) - set(orig))
    common = set(orig) & set(cur)
    modified = sorted(
        f for f in common
        if orig[f] != cur[f] and not f.endswith(".json")
    )

    lines = []
    lines.append(f"ORIGINAL files: {len(orig)}   CURRENT files: {len(cur)}")
    lines.append(f"MISSING from current (deleted or renamed): {len(missing)}")
    for f in missing:
        lines.append(f"  - {f}")
    lines.append(f"ADDED in current: {len(added)}")
    for f in added[:80]:
        lines.append(f"  + {f}")
    if len(added) > 80:
        lines.append(f"  ... and {len(added) - 80} more")
    lines.append(f"MODIFIED (content differs): {len(modified)}")
    for f in modified:
        lines.append(f"  ~ {f}")

    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    print(report[:3000])
    print(f"\nfull report written to {OUT}")
    print(f"counts: missing={len(missing)} added={len(added)} modified={len(modified)}")


if __name__ == "__main__":
    main()
