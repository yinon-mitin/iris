#!/usr/bin/env python3
"""Regenerate requirements.txt from pyproject.toml so Dependabot sees the same pins."""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lines = [item.split()[-1] if " " in item else item for item in config["project"]["dependencies"]]
    (ROOT / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"requirements.txt updated ({len(lines)} packages)")


if __name__ == "__main__":
    main()
