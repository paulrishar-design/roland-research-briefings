"""Shared constants describing where podcast audio is published.

Both the validator and the migration tool read from here so the two can never
disagree about what a valid enclosure URL looks like.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
EPISODES = PUBLIC / "episodes"
RSS = PUBLIC / "roland-research-briefings.xml"

OWNER = "paulrishar-design"
REPO = "roland-research-briefings"

# Audio served straight out of public/ by GitHub Pages. Counts against the
# 1 GB Pages site limit, which is what the migration exists to escape.
PAGES_BASE = f"https://{OWNER}.github.io/{REPO}/"
PAGES_EPISODE_PREFIX = f"{PAGES_BASE}episodes/"

# Audio served as GitHub Release assets. Does not count against the Pages
# limit or repository size. One long-lived tag acts as a rolling bucket.
RELEASE_TAG = "audio"
RELEASE_PREFIX = f"https://github.com/{OWNER}/{REPO}/releases/download/{RELEASE_TAG}/"

AUDIO_MIME = "audio/mpeg"

# Episode slugs are lowercase alphanumeric words joined by single hyphens.
# Anything else risks being altered by GitHub's release-asset name sanitising.
SLUG_FILENAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.mp3$")


def classify(url: str) -> str:
    """Return 'pages', 'release', or 'unknown' for an enclosure URL."""
    if url.startswith(PAGES_EPISODE_PREFIX):
        return "pages"
    if url.startswith(RELEASE_PREFIX):
        return "release"
    return "unknown"
