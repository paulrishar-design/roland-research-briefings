#!/usr/bin/env python3
"""Move episode audio from public/episodes/ to a GitHub Release.

Runs in three phases, in the order the migration plan requires:

  1. upload   every local MP3 becomes an asset on the release, sent with an
              explicit audio/mpeg content type
  2. verify   every asset is re-fetched and its byte count checked against the
              local file, so nothing is trusted on the strength of a 201
  3. rewrite  only once all of the above passed, the feed's enclosure URLs are
              repointed at the release (requires --rewrite-feed)

Safe to re-run: assets that already exist with the right size are skipped, and
the feed rewrite is a no-op once applied. Nothing is deleted from the working
tree — removing public/episodes/ is a deliberate later step, after subscribers
have had time to refresh their cached copy of the feed.

Needs a token in GITHUB_TOKEN with `contents: write` on the repository.

    python3 scripts/migrate_audio_to_release.py                  # dry run
    python3 scripts/migrate_audio_to_release.py --execute
    python3 scripts/migrate_audio_to_release.py --execute --rewrite-feed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from podcast_config import (
    AUDIO_MIME,
    EPISODES,
    OWNER,
    PAGES_EPISODE_PREFIX,
    RELEASE_PREFIX,
    RELEASE_TAG,
    REPO,
    RSS,
    SLUG_FILENAME,
)

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


class Abort(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"ERROR: {message}")


def request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    data = body
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        # Sent explicitly: some proxies reject a POST that does not declare it.
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise Abort(f"{method} {url} failed: {error.reason}") from error


def human(size: int) -> str:
    return f"{size / 1048576:.1f} MB"


def get_release(token: str, tag: str) -> dict | None:
    status, body = request("GET", f"{API}/repos/{OWNER}/{REPO}/releases/tags/{tag}", token)
    if status == 404:
        return None
    if status != 200:
        raise Abort(f"could not read release {tag!r}: HTTP {status} {body[:300].decode(errors='replace')}")
    return json.loads(body)


def create_release(token: str, tag: str) -> dict:
    status, body = request(
        "POST",
        f"{API}/repos/{OWNER}/{REPO}/releases",
        token,
        payload={
            "tag_name": tag,
            "name": "Episode audio",
            "body": (
                "Audio assets for the Roland Research Briefings feed.\n\n"
                "Hosted here rather than in `public/` so episode audio does not "
                "count against the 1 GB GitHub Pages site limit. Managed by "
                "`scripts/migrate_audio_to_release.py`."
            ),
            "make_latest": "false",
        },
    )
    if status != 201:
        raise Abort(f"could not create release {tag!r}: HTTP {status} {body[:300].decode(errors='replace')}")
    return json.loads(body)


def upload_asset(token: str, release_id: int, path: Path) -> None:
    url = f"{UPLOADS}/repos/{OWNER}/{REPO}/releases/{release_id}/assets?name={urllib.parse.quote(path.name)}"
    status, body = request(
        "POST", url, token, body=path.read_bytes(), content_type=AUDIO_MIME
    )
    if status != 201:
        raise Abort(f"upload failed for {path.name}: HTTP {status} {body[:300].decode(errors='replace')}")


def delete_asset(token: str, asset_id: int) -> None:
    status, body = request("DELETE", f"{API}/repos/{OWNER}/{REPO}/releases/assets/{asset_id}", token)
    if status != 204:
        raise Abort(f"could not delete asset {asset_id}: HTTP {status} {body[:300].decode(errors='replace')}")


def probe(url: str) -> tuple[int, int | None, str | None]:
    """Range-GET one byte. Returns (status, total size, served content type)."""
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            content_range = response.headers.get("Content-Range", "")
            total = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else None
            return response.status, total, response.headers.get("Content-Type")
    except urllib.error.HTTPError as error:
        return error.code, None, None
    except urllib.error.URLError as error:
        raise Abort(f"could not reach {url}: {error.reason}") from error


def rewrite_feed(slugs: list[str], execute: bool) -> int:
    """Repoint enclosure URLs at the release, leaving every guid untouched.

    Done as a targeted string swap rather than an XML round-trip so the
    generator's own formatting survives and the diff stays readable.
    """
    text = RSS.read_text(encoding="utf-8")
    changed = 0
    for slug in slugs:
        old = f'"{PAGES_EPISODE_PREFIX}{slug}"'
        new = f'"{RELEASE_PREFIX}{slug}"'
        if old in text:
            text = text.replace(old, new)
            changed += 1
    if changed and execute:
        RSS.write_text(text, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="actually make changes (default: dry run)")
    parser.add_argument("--rewrite-feed", action="store_true", help="repoint enclosure URLs after verifying")
    parser.add_argument("--tag", default=RELEASE_TAG, help=f"release tag to use (default: {RELEASE_TAG})")
    parser.add_argument("--replace", action="store_true", help="re-upload assets whose size does not match")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise Abort("set GITHUB_TOKEN (needs contents: write on the repository)")

    local = sorted(EPISODES.glob("*.mp3")) if EPISODES.is_dir() else []
    if not local:
        raise Abort(f"no MP3s found in {EPISODES}")
    for path in local:
        if not SLUG_FILENAME.match(path.name):
            raise Abort(f"{path.name} is not a clean slug; rename it before uploading")

    total = sum(p.stat().st_size for p in local)
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"[{mode}] {len(local)} episodes, {human(total)} -> release {args.tag!r}\n")

    release = get_release(token, args.tag)
    if release is None and args.execute:
        release = create_release(token, args.tag)
        print(f"  created release {args.tag!r} (id {release['id']})")

    if release is None:
        print(f"  would create release {args.tag!r}")
        release_id: int | None = None
        existing: dict[str, dict] = {}
    else:
        release_id = release["id"]
        existing = {a["name"]: a for a in release.get("assets", [])}
        print(f"  release {args.tag!r} in place (id {release_id}), {len(existing)} assets attached")

    # ---- phase 1: upload -------------------------------------------------
    print("\nupload")
    uploaded = skipped = 0
    for path in local:
        size = path.stat().st_size
        asset = existing.get(path.name)
        if asset and asset.get("size") == size:
            skipped += 1
            continue
        if asset and not args.replace:
            print(f"  ! {path.name}: attached but {human(asset.get('size', 0))} != local {human(size)} — pass --replace")
            continue
        if not args.execute:
            print(f"  would upload {path.name} ({human(size)})")
            uploaded += 1
            continue
        if asset:
            delete_asset(token, asset["id"])
        upload_asset(token, release_id, path)
        print(f"  uploaded {path.name} ({human(size)})")
        uploaded += 1
    print(f"  {uploaded} uploaded, {skipped} already present")

    # ---- phase 2: verify -------------------------------------------------
    if not args.execute:
        print("\nverify\n  skipped in dry run")
        print("\nnext: re-run with --execute, then add --rewrite-feed once verification passes")
        return 0

    print("\nverify")
    problems = []
    served_types = set()
    for path in local:
        url = f"{RELEASE_PREFIX}{path.name}"
        status, remote_size, content_type = probe(url)
        local_size = path.stat().st_size
        if status not in (200, 206):
            problems.append(f"{path.name}: HTTP {status}")
        elif remote_size is not None and remote_size != local_size:
            problems.append(f"{path.name}: remote {remote_size} != local {local_size}")
        if content_type:
            served_types.add(content_type)
    if problems:
        for problem in problems:
            print(f"  FAIL {problem}")
        raise Abort(f"{len(problems)} asset(s) failed verification; feed not touched")
    print(f"  all {len(local)} assets resolve with matching byte counts")
    print(f"  served Content-Type: {', '.join(sorted(served_types)) or 'unknown'}")
    if served_types and served_types != {AUDIO_MIME}:
        print(
            f"  note: not {AUDIO_MIME}. Most clients key off the enclosure type attribute\n"
            "        and the .mp3 extension, but test one episode in a real client before\n"
            "        relying on it."
        )

    # ---- phase 3: rewrite ------------------------------------------------
    print("\nfeed")
    if not args.rewrite_feed:
        print("  left alone (pass --rewrite-feed to repoint enclosure URLs)")
        return 0
    changed = rewrite_feed([p.name for p in local], execute=True)
    print(f"  repointed {changed} enclosure URL(s) at the release; guids untouched")
    print("\nnow: python3 scripts/validate_public.py --check-remote, then commit the feed alone.")
    print("Leave public/episodes/ in place for 1-2 weeks so cached feeds keep resolving.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
