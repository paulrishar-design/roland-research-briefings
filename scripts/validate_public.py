#!/usr/bin/env python3
"""Validate the dedicated podcast publication tree."""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
RSS = PUBLIC / "roland-research-briefings.xml"
FORBIDDEN = {
    "absolute macOS path": re.compile(r"/Users/"),
    "absolute Linux home path": re.compile(r"/home/"),
    "private IPv4 address": re.compile(r"\b(?:10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}(?:\.\d{1,3}){2}\b"),
    "private key block": re.compile(r"BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "credential assignment": re.compile(r"(?:password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;]{8,}", re.I),
}


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> int:
    if not PUBLIC.is_dir():
        fail("public/ is missing")
    for path in PUBLIC.rglob("*"):
        if path.is_symlink():
            fail(f"symlink is not allowed in public/: {path.relative_to(ROOT)}")
        if path.is_file() and (path.name == ".env" or path.name.startswith(".env.")):
            fail(f"environment file is not allowed: {path.relative_to(ROOT)}")
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN.items():
            if pattern.search(body):
                fail(f"{label} found in {path.relative_to(ROOT)}")

    try:
        root = ET.parse(RSS).getroot()
    except (OSError, ET.ParseError) as error:
        fail(f"RSS is invalid XML: {error}")
    enclosures = root.findall(".//enclosure")
    if not enclosures:
        fail("RSS has no episode enclosures")
    public_files = {path.name for path in PUBLIC.rglob("*") if path.is_file()}
    for enclosure in enclosures:
        url = enclosure.get("url", "")
        filename = Path(urllib.parse.urlparse(url).path).name
        if not filename or filename not in public_files:
            fail(f"RSS enclosure has no matching public artifact: {filename or '[missing URL]'}")
    audio_files = list((PUBLIC / "episodes").glob("*.mp3"))
    if not audio_files or any(path.stat().st_size == 0 for path in audio_files):
        fail("episode audio is missing or empty")
    print(f"public podcast validation passed: {len(enclosures)} enclosures, {len(audio_files)} MP3 files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
