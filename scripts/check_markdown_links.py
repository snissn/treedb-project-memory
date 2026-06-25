from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = [
    ROOT / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "examples").glob("**/*.md")),
]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def main() -> int:
    failures: list[str] = []
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                target = match.group(1).strip()
                if _skip_target(target):
                    continue
                local = target.split("#", 1)[0]
                if not local:
                    continue
                resolved = (path.parent / local).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    failures.append(f"{path}:{line_no}: link leaves repo: {target}")
                    continue
                if not resolved.exists():
                    failures.append(f"{path}:{line_no}: missing link target: {target}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"checked {len(DOC_PATHS)} markdown files")
    return 0


def _skip_target(target: str) -> bool:
    return (
        target.startswith("http://")
        or target.startswith("https://")
        or target.startswith("mailto:")
    )


if __name__ == "__main__":
    raise SystemExit(main())
