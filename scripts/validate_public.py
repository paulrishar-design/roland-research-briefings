#!/usr/bin/env python3
"""Validate the dedicated podcast publication tree.

Episode audio may be served either out of public/ by GitHub Pages or as a
GitHub Release asset. Both are accepted, including a feed that mixes the two
mid-migration, so this stays correct during the overlap window when the feed
already points at Releases but public/episodes/ has not been removed yet.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from podcast_config import (
    AUDIO_MIME,
    EPISODES,
    PUBLIC,
    RELEASE_PREFIX,
    ROOT,
    RSS,
    SLUG_FILENAME,
    classify,
)

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


def scan_for_leaks() -> None:
    """Refuse to publish anything that looks like a credential or local path."""
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


def check_remote(url: str) -> None:
    """Confirm a release asset actually resolves, without downloading it."""
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 206):
                fail(f"release asset returned HTTP {response.status}: {url}")
    except urllib.error.HTTPError as error:
        fail(f"release asset returned HTTP {error.code}: {url}")
    except urllib.error.URLError as error:
        fail(f"release asset unreachable ({error.reason}): {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-remote",
        action="store_true",
        help="also confirm every release-hosted enclosure resolves (costs network)",
    )
    args = parser.parse_args()

    scan_for_leaks()

    try:
        root = ET.parse(RSS).getroot()
    except (OSError, ET.ParseError) as error:
        fail(f"RSS is invalid XML: {error}")

    enclosures = root.findall(".//enclosure")
    if not enclosures:
        fail("RSS has no episode enclosures")

    counts = {"pages": 0, "release": 0}

    for enclosure in enclosures:
        url = enclosure.get("url", "")
        filename = Path(urllib.parse.urlparse(url).path).name
        if not filename:
            fail("RSS enclosure has no URL")

        kind = classify(url)
        if kind == "unknown":
            fail(
                f"RSS enclosure points at an unrecognised host: {url}\n"
                f"       expected {RELEASE_PREFIX}<slug>.mp3 or a public/episodes/ URL"
            )

        if not SLUG_FILENAME.match(filename):
            fail(
                f"enclosure filename is not a clean slug: {filename}\n"
                "       release asset names must be lowercase alphanumeric words joined by hyphens"
            )

        declared = enclosure.get("length", "")
        if not declared.isdigit() or int(declared) == 0:
            fail(f"enclosure has a missing or zero length: {filename}")

        if enclosure.get("type") != AUDIO_MIME:
            fail(f"enclosure is not {AUDIO_MIME}: {filename}")

        if kind == "pages":
            # A Pages enclosure URL always resolves to public/episodes/, so
            # that is the only place the file is allowed to satisfy it from.
            local = EPISODES / filename
            if not local.is_file():
                fail(f"RSS enclosure has no matching public artifact: {filename}")
            actual = local.stat().st_size
            if actual != int(declared):
                fail(
                    f"enclosure length does not match the file: {filename} "
                    f"declared={declared} actual={actual}"
                )
        elif args.check_remote:
            check_remote(url)

        counts[kind] += 1

    # Any audio still sitting in the tree must be intact, but an empty
    # episodes/ directory is expected once the migration finishes.
    for path in EPISODES.glob("*.mp3") if EPISODES.is_dir() else []:
        if path.stat().st_size == 0:
            fail(f"episode audio is empty: {path.relative_to(ROOT)}")

    where = f"{counts['pages']} on Pages, {counts['release']} on Releases"
    checked = " (remote-checked)" if args.check_remote else ""
    print(f"public podcast validation passed: {len(enclosures)} enclosures ({where}){checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
