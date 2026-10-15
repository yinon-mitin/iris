#!/usr/bin/env python3
"""Validate local documentation links and English/Russian document parity."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    "README.md": "README.ru.md",
    "CONTRIBUTING.md": "CONTRIBUTING.ru.md",
    "SECURITY.md": "SECURITY.ru.md",
    "CONTRIBUTORS.md": "CONTRIBUTORS.ru.md",
    "docs/INSTALL.md": "docs/ru/INSTALL.md",
    "docs/ARCHITECTURE.md": "docs/ru/ARCHITECTURE.md",
    "docs/OPERATIONS.md": "docs/ru/OPERATIONS.md",
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
HTML_SOURCE = re.compile(r"(?:src|href)=\"([^\"]+)\"")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def anchor(heading: str) -> str:
    # GitHub: lowercase, drop punctuation except - and _, spaces to dashes; keeps Cyrillic
    text = re.sub(r"[`*_]", "", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(document: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", document.read_text(encoding="utf-8"), flags=re.DOTALL)
    found = {anchor(h) for h in HEADING.findall(text)}
    found |= set(re.findall(r'<a id="([^"]+)"', text))
    return found


def repository_markdown() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return sorted(ROOT / rel for rel in set(result.stdout.splitlines()) if rel and (ROOT / rel).is_file())


def main() -> int:
    errors: list[str] = []
    documents = repository_markdown()

    for english, russian in PAIRS.items():
        for relative in (english, russian):
            if not (ROOT / relative).is_file():
                errors.append(f"missing required document: {relative}")

    for document in documents:
        location = document.relative_to(ROOT)
        text = document.read_text(encoding="utf-8")
        for raw in MARKDOWN_LINK.findall(text) + HTML_SOURCE.findall(text):
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, fragment = unquote(raw).partition("#")
            target = (document.parent / path_part).resolve() if path_part else document
            if not target.exists():
                errors.append(f"{location}: broken local link: {raw}")
            elif fragment and target.suffix == ".md" and fragment not in anchors(target):
                errors.append(f"{location}: missing anchor: {raw}")

    if errors:
        print("Documentation validation FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Documentation validation passed ({len(documents)} Markdown files, {len(PAIRS)} bilingual pairs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
