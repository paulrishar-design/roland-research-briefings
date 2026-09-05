#!/usr/bin/env python3
"""Black-box tests for validate_public.py.

Each test builds a throwaway publication tree in a temp directory, copies the
scripts in beside it, and runs the validator as a subprocess — the same way CI
invokes it. Run with: python3 scripts/test_validate_public.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGES = "https://paulrishar-design.github.io/roland-research-briefings/episodes/"
RELEASE = "https://github.com/paulrishar-design/roland-research-briefings/releases/download/audio/"

FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Roland Research Briefings</title>
{items}
</channel></rss>
"""
ITEM = """  <item>
    <title>{slug}</title>
    <guid isPermaLink="false">roland-research-unified_deadbeef-{slug}</guid>
    <enclosure url="{url}" length="{length}" type="{mime}"/>
  </item>"""


def item(slug, url, length, mime="audio/mpeg"):
    return ITEM.format(slug=slug, url=url, length=length, mime=mime)


class ValidatorCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        (self.dir / "scripts").mkdir()
        for name in ("validate_public.py", "podcast_config.py"):
            shutil.copy(HERE / name, self.dir / "scripts" / name)
        self.episodes = self.dir / "public" / "episodes"
        self.episodes.mkdir(parents=True)

    def write_audio(self, slug: str, size: int) -> int:
        # Deliberately invalid UTF-8 so the leak scanner treats it as binary.
        (self.episodes / f"{slug}.mp3").write_bytes(b"\xff\xfe" + b"\x00" * (size - 2))
        return size

    def write_feed(self, *items: str) -> None:
        (self.dir / "public" / "roland-research-briefings.xml").write_text(
            FEED.format(items="\n".join(items)), encoding="utf-8"
        )

    def run_validator(self, *args: str):
        return subprocess.run(
            [sys.executable, str(self.dir / "scripts" / "validate_public.py"), *args],
            capture_output=True, text=True,
        )

    def assertPasses(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, f"expected pass, got: {result.stderr or result.stdout}")
        return result.stdout

    def assertFailsWith(self, needle: str):
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0, "expected failure, but validation passed")
        self.assertIn(needle, result.stderr + result.stdout)


class TestAcceptedLayouts(ValidatorCase):
    def test_all_pages_hosted_still_passes(self):
        size = self.write_audio("george-hotz", 4096)
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", size))
        self.assertIn("1 on Pages, 0 on Releases", self.assertPasses())

    def test_all_release_hosted_passes_with_no_local_audio(self):
        self.write_feed(item("george-hotz", f"{RELEASE}george-hotz.mp3", 17820333))
        self.assertIn("0 on Pages, 1 on Releases", self.assertPasses())

    def test_mixed_feed_passes_during_the_overlap_window(self):
        size = self.write_audio("george-hotz", 4096)
        self.write_feed(
            item("george-hotz", f"{PAGES}george-hotz.mp3", size),
            item("nat-friedman", f"{RELEASE}nat-friedman.mp3", 24000000),
        )
        self.assertIn("1 on Pages, 1 on Releases", self.assertPasses())

    def test_empty_episodes_dir_is_fine_once_migrated(self):
        self.write_feed(item("george-hotz", f"{RELEASE}george-hotz.mp3", 17820333))
        self.assertTrue(self.episodes.is_dir())
        self.assertPasses()


class TestRejectedFeeds(ValidatorCase):
    def test_unknown_host_is_rejected(self):
        self.write_feed(item("george-hotz", "https://cdn.example.com/george-hotz.mp3", 1234))
        self.assertFailsWith("unrecognised host")

    def test_missing_local_file_is_rejected(self):
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", 4096))
        self.assertFailsWith("no matching public artifact")

    def test_length_mismatch_is_rejected(self):
        self.write_audio("george-hotz", 4096)
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", 9999))
        self.assertFailsWith("length does not match")

    def test_zero_length_is_rejected(self):
        self.write_audio("george-hotz", 4096)
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", 0))
        self.assertFailsWith("missing or zero length")

    def test_wrong_mime_type_is_rejected(self):
        size = self.write_audio("george-hotz", 4096)
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", size, mime="audio/mp4"))
        self.assertFailsWith("not audio/mpeg")

    def test_dirty_slug_is_rejected(self):
        self.write_feed(item("x", f"{RELEASE}George Hotz_v2.mp3", 1234))
        self.assertFailsWith("not a clean slug")

    def test_empty_audio_file_is_rejected(self):
        (self.episodes / "george-hotz.mp3").write_bytes(b"")
        self.write_feed(item("george-hotz", f"{RELEASE}george-hotz.mp3", 17820333))
        self.assertFailsWith("empty")

    def test_feed_with_no_enclosures_is_rejected(self):
        self.write_feed()
        self.assertFailsWith("no episode enclosures")


class TestLeakScanningStillWorks(ValidatorCase):
    def _feed_one(self):
        size = self.write_audio("george-hotz", 4096)
        self.write_feed(item("george-hotz", f"{PAGES}george-hotz.mp3", size))

    def test_github_token_in_public_is_rejected(self):
        self._feed_one()
        (self.dir / "public" / "notes.txt").write_text("ghp_" + "a" * 32, encoding="utf-8")
        self.assertFailsWith("GitHub token")

    def test_local_path_leak_is_rejected(self):
        self._feed_one()
        (self.dir / "public" / "notes.txt").write_text("/Users/paul/secret", encoding="utf-8")
        self.assertFailsWith("absolute macOS path")

    def test_env_file_is_rejected(self):
        self._feed_one()
        (self.dir / "public" / ".env.production").write_text("A=1", encoding="utf-8")
        self.assertFailsWith("environment file")

    def test_symlink_is_rejected(self):
        self._feed_one()
        (self.dir / "public" / "link.txt").symlink_to(self.dir / "scripts" / "podcast_config.py")
        self.assertFailsWith("symlink")


if __name__ == "__main__":
    unittest.main(verbosity=2)
