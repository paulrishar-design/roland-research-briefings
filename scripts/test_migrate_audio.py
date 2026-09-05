#!/usr/bin/env python3
"""Tests for the feed rewrite in migrate_audio_to_release.py.

The rewrite edits the live feed in place, so it gets checked against a real
copy of the published XML: enclosure URLs must move, and nothing else — guids
above all — may change. Run with: python3 scripts/test_migrate_audio.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import migrate_audio_to_release as mig  # noqa: E402
from podcast_config import PAGES_EPISODE_PREFIX, RELEASE_PREFIX, RSS  # noqa: E402


class TestFeedRewrite(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.feed = self.dir / "feed.xml"
        shutil.copy(RSS, self.feed)
        self.original = self.feed.read_text(encoding="utf-8")
        patched = mig.RSS
        mig.RSS = self.feed
        self.addCleanup(setattr, mig, "RSS", patched)
        self.slugs = [
            Path(e.get("url")).name
            for e in ET.parse(RSS).getroot().findall(".//enclosure")
        ]

    def enclosure_urls(self):
        return [e.get("url") for e in ET.parse(self.feed).getroot().findall(".//enclosure")]

    def guids(self):
        return [g.text for g in ET.parse(self.feed).getroot().findall(".//guid")]

    def test_dry_run_reports_but_writes_nothing(self):
        changed = mig.rewrite_feed(self.slugs, execute=False)
        self.assertEqual(changed, len(self.slugs))
        self.assertEqual(self.feed.read_text(encoding="utf-8"), self.original)

    def test_every_enclosure_moves_to_the_release(self):
        before = self.enclosure_urls()
        self.assertTrue(all(u.startswith(PAGES_EPISODE_PREFIX) for u in before))
        mig.rewrite_feed(self.slugs, execute=True)
        after = self.enclosure_urls()
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            self.assertTrue(new.startswith(RELEASE_PREFIX), new)
            self.assertEqual(Path(new).name, Path(old).name)

    def test_guids_are_untouched(self):
        before = self.guids()
        mig.rewrite_feed(self.slugs, execute=True)
        self.assertEqual(before, self.guids())
        self.assertTrue(before, "fixture feed had no guids to protect")

    def test_only_enclosure_urls_change(self):
        mig.rewrite_feed(self.slugs, execute=True)
        after = self.feed.read_text(encoding="utf-8")
        # Reverse the swap; the file must come back byte-identical, which means
        # the rewrite touched enclosure URLs and nothing else.
        restored = after.replace(
            f'"{RELEASE_PREFIX}', f'"{PAGES_EPISODE_PREFIX}'
        )
        self.assertEqual(restored, self.original)

    def test_lengths_and_types_survive(self):
        root_before = ET.parse(self.feed).getroot()
        before = [(e.get("length"), e.get("type")) for e in root_before.findall(".//enclosure")]
        mig.rewrite_feed(self.slugs, execute=True)
        root_after = ET.parse(self.feed).getroot()
        after = [(e.get("length"), e.get("type")) for e in root_after.findall(".//enclosure")]
        self.assertEqual(before, after)

    def test_rerunning_is_a_no_op(self):
        mig.rewrite_feed(self.slugs, execute=True)
        once = self.feed.read_text(encoding="utf-8")
        changed = mig.rewrite_feed(self.slugs, execute=True)
        self.assertEqual(changed, 0)
        self.assertEqual(self.feed.read_text(encoding="utf-8"), once)

    def test_channel_self_link_is_not_rewritten(self):
        # The atom:self link and cover art live under the same Pages base but
        # outside episodes/, so they must survive untouched.
        mig.rewrite_feed(self.slugs, execute=True)
        after = self.feed.read_text(encoding="utf-8")
        self.assertIn("roland-research-briefings.xml", after)
        self.assertIn("cover.jpg", after)
        self.assertNotIn(f"{RELEASE_PREFIX}cover.jpg", after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
