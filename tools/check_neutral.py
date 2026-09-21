#!/usr/bin/env python3
"""Fail if a concrete deployment value was committed.

This integration must work for any school on the platform, and a hardcoded
school code or tenant does double damage: it breaks every other deployment,
and it identifies whoever committed it. Examples must stay placeholders.

    python3 tools/check_neutral.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Placeholders that are fine to appear where a real value would identify one
# school. Keep this list small and obviously generic.
ALLOWED = {
    "example", "school", "school_code", "mycode", "subdomain",
    "your-school", "tenant", "id",
}

# "<something>.together-school.com" with a concrete subdomain label.
HOST = re.compile(r"\b([a-z0-9][a-z0-9-]*)\.together-school\.com", re.I)
# A tenant value such as "tenant7" or "tenant_somewhere" (but not the field
# names tenant_id / TENANT_ID, and not an obvious placeholder).
TENANT = re.compile(r"\btenant[_-]?([a-z0-9][a-z0-9-]*)\b", re.I)

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
SKIP_SUFFIX = {".png", ".jpg", ".ico", ".bundle", ".gz", ".zip"}


def offending(text: str) -> list[str]:
    hits: list[str] = []
    for label in HOST.findall(text):
        bare = label.lower().strip("<>{}")
        if bare not in ALLOWED:
            hits.append(f"hostname label '{label}'")
    for label in TENANT.findall(text):
        bare = label.lower().strip("<>{}")
        if bare in ALLOWED:
            continue
        # tenant_id / TENANT_ID are field names, not values.
        if bare == "id":
            continue
        hits.append(f"tenant value 'tenant…{label}'")
    return hits


def main() -> int:
    problems = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX or path == pathlib.Path(__file__):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for hit in offending(line):
                problems.append(
                    f"{path.relative_to(ROOT)}:{line_no}: {hit}"
                )

    if problems:
        print("Deployment-specific values committed - use a placeholder:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("No deployment-specific values found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
