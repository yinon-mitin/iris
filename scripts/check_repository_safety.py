#!/usr/bin/env python3
"""Fail if Git would publish secrets, bot state, cookies, downloads or home-network addresses."""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_PREFIXES = ("data/",)
PROHIBITED_NAMES = {".env", "youtube-cookies.txt", "cookies.txt"}
PROHIBITED_SUFFIXES = {
    ".session", ".session-journal", ".db", ".sqlite", ".sqlite3", ".pem", ".p12", ".pfx", ".key",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".mp3", ".m4a", ".flac", ".part",
}
TEXT_SECRET_RULES = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b"),
    "Telegram API hash": re.compile(r"(?i)\bAPP_HASH\s*[:=]\s*[\"']?[0-9a-f]{32}\b"),
    "hard-coded credential": re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|secret(?:_key)?|provider_token)\b"
        r"\s*[:=]\s*[\"']?(?!\$\{|<|example|none|false|true|os\.environ|os\.getenv|get_env)[^\s#\"']{8,}"
    ),
}
# Example values that are allowed to look like secrets in templates and docs
PLACEHOLDERS = ("0123456789abcdef0123456789abcdef", "123456789:AA...")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])(?!/\d)")  # CIDR ranges are rules, not hosts
DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
CGNAT = ipaddress.ip_network("100.64.0.0/10")
# tests use internal addresses on purpose to check that they are refused
INTERNAL_ADDRESS_ALLOWED = ("tests/",)


def contains_internal_address(line: str) -> bool:
    for candidate in IPV4.findall(line):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        if any(address in network for network in DOCUMENTATION_NETWORKS):
            continue
        if address.is_private or address in CGNAT:
            return True
    return False


def git_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return sorted(set(filter(None, result.stdout.splitlines())))


def main() -> int:
    findings: list[tuple[str, str]] = []
    files = git_files()
    for rel in files:
        normalized = rel.replace("\\", "/")
        path = ROOT / rel
        if not path.is_file():
            continue  # deleted in the working tree
        if normalized.startswith(PROHIBITED_PREFIXES):
            findings.append((rel, "prohibited runtime path"))
            continue
        if path.name.lower() in PROHIBITED_NAMES or path.suffix.lower() in PROHIBITED_SUFFIXES:
            findings.append((rel, "prohibited secret/state/media filename"))
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="ignore")
        check_addresses = not normalized.startswith(INTERNAL_ADDRESS_ALLOWED)
        for line_no, line in enumerate(text.splitlines(), 1):
            if check_addresses and contains_internal_address(line):
                findings.append((f"{rel}:{line_no}", "hard-coded private/internal IPv4 address"))
            if any(p in line for p in PLACEHOLDERS):
                continue
            for label, pattern in TEXT_SECRET_RULES.items():
                if pattern.search(line):
                    findings.append((f"{rel}:{line_no}", label))
    if findings:
        print("Repository safety check FAILED; values are intentionally not printed:")
        for location, reason in findings:
            print(f"- {location}: {reason}")
        return 1
    print(f"Repository safety check passed ({len(files)} publishable files scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
