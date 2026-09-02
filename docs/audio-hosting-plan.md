# Audio hosting plan: getting episode MP3s out of the repo

Measured 2026-09-02 against `main` @ `1c39863`.

## Status

| Stage | State |
| --- | --- |
| Tooling + validator | **Done** — `validate_public.py` accepts both hosting layouts, `migrate_audio_to_release.py` automates stages 0–2, 23 tests cover both, all wired into CI |
| S0 spike | **Blocked here** — needs credentials this session does not have |
| S1 forward fix | Needs a change in the external generator (out of this repo) |
| S2 backfill | One command, once someone runs it with a `contents: write` token |
| S3 remove from tree | After S2 plus a 1–2 week overlap |
| S4 history rewrite | Optional, deliberately deferred |

Stages 0, 2 and 3 could not be executed from the session that wrote this:
release creation returns `403 Creating, editing, or deleting releases is not
permitted for this session type`. They are automated instead, so running them
is one command rather than 24 manual uploads.

The full path was rehearsed end to end against a copy of the real tree with
stand-in audio at true byte sizes. Validation passes at every stage —
before (24 on Pages), during the overlap (24 on Releases, audio still on
disk), and after removal — with every `<guid>` unchanged.

## 1. The forcing function

`public/` is served by GitHub Pages, which has a **hard 1 GB published-site limit**.

| Measure | Value |
| --- | --- |
| `public/` total | 370.4 MB — **36.2% of the 1 GB Pages cap** |
| Episodes | 24 files, mean 15.4 MB (min 3.3, max 28.5) |
| Headroom | 654 MB ≈ **42 more episodes** at mean size, **22** at worst-case size |
| Publish cadence | 24 episodes in 39 days ≈ 0.62/day |
| **Projected cap date** | **~68 days (~2.3 months) at current cadence** |

There are two *separate* size problems. Conflating them is the main way this
migration goes wrong:

- **The working tree** (`public/`, 370 MB) is what Pages serves and what counts
  against the 1 GB cap. Fixed by stages 1–3 below.
- **Git history** (`.git`, 432 MB packed) is paid on every clone and grows
  permanently — deleting an episode from the tree does not shrink it. Fixed
  only by stage 4.

History already carries **61.7 MB of pure dead weight**: 13 episode paths that
exist nowhere in the current tree, left over from the pre-`public/` layout
(`episodes/alex-finn.mp3`) and from acceptance-test runs
(`episodes/alex-wissner-gross-standalone-acceptance.mp3`, 14.6 MB;
`episodes/youtube-miner-acceptance-handoff.mp3`, 8.0 MB). Plus one superseded
version of `balaji-srinivasan.mp3` (11.0 MB).

Secondary pressure, worth noting but not urgent: every deploy re-uploads the
whole of `public/` as a Pages artifact. Runs currently take ~2 minutes, but run
7 took **6m43s** against a 10-minute Pages deploy timeout. That headroom shrinks
as the tree grows.

## 2. Recommendation: GitHub Releases

| Option | Cost | Effort | Fixes cap | Notes |
| --- | --- | --- | --- | --- |
| **GitHub Releases** ← recommended | free | low | yes | 2 GiB/file, no total cap, no bandwidth cap, doesn't count toward repo size. No new account, no secrets, no DNS. |
| Cloudflare R2 + custom domain | free tier covers 370 MB (10 GB) | medium | yes | Technically the nicest: correct `Content-Type`, own domain, zero egress fees. Costs an account, R2 credentials in Actions secrets, and a domain. |
| Git LFS | paid past 1 GB | medium | **no** | LFS objects still render into the Pages artifact. Solves clone size only. Wrong tool here. |
| Prune old episodes from the feed | free | low | delays only | Buys months, not a fix, and breaks back-catalogue links. |

Releases wins on effort-to-benefit: it stays entirely inside GitHub, needs no
new credentials beyond the built-in `GITHUB_TOKEN`, and moves audio off *both*
the Pages cap and future repo growth in one move.

### What was verified empirically

Against a real public release asset (`git-lfs` v3.5.1), observed directly:

- `github.com/OWNER/REPO/releases/download/TAG/FILE` returns **302** to a
  short-lived signed URL on `release-assets.githubusercontent.com`. The
  `github.com` URL itself is stable and permanent — that is what goes in the
  feed. Podcast clients follow redirects.
- **Range requests work**: `206 Partial Content`, `Accept-Ranges: bytes`,
  correct `Content-Range`. Seeking and resume are fine.
- The asset served as **`Content-Type: application/octet-stream`** with
  `Content-Disposition: attachment`.

That last point is the one open risk — see stage 0.

## 3. Plan

### Stage 0 — spike: confirm the served Content-Type (do this first)

The signed redirect URL carries explicit `rsct=` (response content type) and
`rscd=` parameters, which are populated from the asset's stored `content_type`.
That field is set from the `Content-Type` header sent at upload. So uploading
with `Content-Type: audio/mpeg` *should* make GitHub serve the MP3 as
`audio/mpeg`. This was inferred from the redirect URL's shape, **not** proven —
the public API for other repos is unreachable from this environment, so prove
it with one throwaway upload before committing to the design.

The migration tool reports the served content type during its verify phase, so
the spike is now just a dry run plus a single-episode execute against a
throwaway tag:

```bash
export GITHUB_TOKEN=...   # needs contents: write

# see exactly what would happen, touching nothing
python3 scripts/migrate_audio_to_release.py

# real upload of the whole set to a scratch tag, then read the reported type
python3 scripts/migrate_audio_to_release.py --execute --tag audio-spike
```

The verify phase prints `served Content-Type:` and flags it explicitly if it is
not `audio/mpeg`. Delete the scratch release afterwards.

**If it serves `audio/mpeg`:** proceed as planned.
**If it serves `application/octet-stream` regardless:** proceed anyway, but
test one episode end-to-end in Apple Podcasts before backfilling all 24. Most
clients key off the enclosure `type="audio/mpeg"` attribute and the `.mp3`
extension rather than the response header, so this is very likely tolerable —
but it is worth knowing before, not after. If a client does balk, that is the
signal to fall back to Cloudflare R2, which controls response headers fully.

### Stage 1 — stop the bleeding (forward fix)

Change the publish path so **new** episodes never enter git. Nothing about the
existing 24 changes yet, so this is entirely non-breaking and independently
useful even if the backfill is deferred.

Create one long-lived, non-draft release, tag `audio`, and treat it as a rolling
bucket of assets:

```
https://github.com/paulrishar-design/roland-research-briefings/releases/download/audio/<slug>.mp3
```

A single tag keeps URLs trivially predictable. Episode slugs are already
lowercase alphanumeric-plus-hyphen, so they survive GitHub's asset-name
sanitisation unchanged. If the asset list ever gets unwieldy, partition by year
(`audio-2026`) — old URLs keep working because old assets stay on the old tag.

**This is the stage that depends on code outside this repo.** This repo holds
only the published tree; the `Publish research briefings <timestamp>` commits
come from an external generator. That generator must change to:

1. Upload the MP3 to the `audio` release (the stage-0 `curl`, with `TOKEN`
   scoped to `contents: write`) instead of writing it into `public/episodes/`.
2. Emit the release URL as the `<enclosure url=...>`, keeping `length` (the
   real byte size) and `type="audio/mpeg"` as they are today.
3. Continue to commit `public/roland-research-briefings.xml` and
   `public/index.html` exactly as it does now.

### Stage 2 — backfill the existing 24

Order matters. **Upload and verify before touching the feed**, so there is never
a moment where the feed advertises a URL that 404s.

`scripts/migrate_audio_to_release.py` does all of it, in that order, and
refuses to touch the feed if any asset fails verification:

```bash
export GITHUB_TOKEN=...

python3 scripts/migrate_audio_to_release.py                       # dry run
python3 scripts/migrate_audio_to_release.py --execute             # upload + verify
python3 scripts/migrate_audio_to_release.py --execute --rewrite-feed
python3 scripts/validate_public.py --check-remote
```

It is idempotent — assets already attached at the right size are skipped, and
the feed rewrite is a no-op once applied — so an interrupted run is resumed by
running it again. `<guid>` values are never touched. Commit the feed change on
its own; `public/episodes/` stays in place for now.

### Stage 3 — remove the audio from the tree (after an overlap period)

Wait **1–2 weeks** after stage 2 before deleting `public/episodes/`. Clients
that cached the *old* feed will keep requesting the old Pages URLs until they
refresh; the overlap means those requests still succeed. Deleting immediately is
the version of this migration that generates 404s.

Then:

```bash
git rm -r public/episodes
printf 'public/episodes/\n' >> .gitignore   # belt and braces against re-adding
```

`public/` drops from 370 MB to ~1 MB. The Pages cap problem is now solved
permanently, and deploys go from ~2 minutes to seconds.

### Stage 4 — optional: reclaim the 432 MB of history

Independent of everything above, and the only genuinely risky step. Skip it
unless clone size actually bothers you — it buys no Pages headroom.

```bash
git filter-repo --path public/episodes --path episodes --invert-paths
git push --force --all && git push --force --tags
```

Preconditions that make this comparatively safe here: 0 forks, 0 open PRs, 0
open issues, a single contributor. Costs: every commit SHA changes, every
existing clone must be re-cloned, and the four `Publish research briefings`
SHAs referenced in old Actions runs stop resolving. GitHub does not reclaim the
storage immediately; unreachable objects linger until GC, and support may need
to be asked to run it.

Do this *after* stage 3 has been stable for a while, never as part of the same
change.

## 4. Why this does not break subscribers

Podcast clients de-duplicate episodes on **`<guid>`**, not on enclosure URL.
The feed's GUIDs (`roland-research-unified_<hash>-<slug>`) are stable and are
not being touched, so:

- Already-downloaded episodes stay on-device, untouched.
- Not-yet-downloaded episodes are fetched from the new URL on next refresh.
- No episode reappears as "new", and no subscription is lost.

The only failure mode is a URL that resolves to nothing, which the stage-2/3
ordering and the overlap window are specifically designed to prevent.

## 5. Code in this repo that must change

**Done.** `scripts/validate_public.py` used to have two checks that assumed
audio lives in the tree, and would have failed CI the moment stage 2 landed:

```python
# breaks: release URLs have no matching file under public/
if not filename or filename not in public_files:
    fail(f"RSS enclosure has no matching public artifact: ...")

# breaks: public/episodes/ will be empty or gone after stage 3
audio_files = list((PUBLIC / "episodes").glob("*.mp3"))
if not audio_files or any(path.stat().st_size == 0 for path in audio_files):
    fail("episode audio is missing or empty")
```

These are now replaced by logic that classifies each enclosure by URL:

- **Pages-hosted** (under the Pages base URL) → must exist in `public/`, as today.
  Keeps the validator correct during the stage-2/3 overlap, when the feed is
  mixed.
- **Release-hosted** → assert the URL matches the expected
  `github.com/<owner>/<repo>/releases/download/<tag>/<name>.mp3` shape, and that
  `<name>` is a clean slug.
- Replace the "episodes dir is non-empty" check with "the feed has at least one
  enclosure and every enclosure carries a non-zero `length` and a
  `type="audio/mpeg"`".
- An opt-in `--check-remote` flag range-GETs each release URL, for use on a
  schedule or by hand rather than on every push, since it costs network in CI.

Shared constants live in `scripts/podcast_config.py` so the validator and the
migration tool cannot drift on what a valid enclosure URL looks like.
`scripts/test_validate_public.py` (16 tests) and
`scripts/test_migrate_audio.py` (7 tests) cover both, including the mixed-feed
overlap state and the leak scanning, and both run in CI ahead of validation.

The secret/path-leak scanning at the top of the file is unaffected and should be
kept exactly as-is.

**`.github/workflows/pages.yml`** needs no change to keep working, but after
stage 3 the `upload-pages-artifact` step goes from 370 MB to ~1 MB, which
removes the 10-minute-timeout concern entirely.

## 6. Suggested order of work

1. ~~Update `validate_public.py` to accept both URL shapes~~ — **done**.
2. Stage 0 spike — answers the one open question, fully reversible.
3. Stage 1 forward fix in the external generator — stops new growth.
4. Stage 2 backfill + feed rewrite — one command.
5. Wait 1–2 weeks.
6. Stage 3 `git rm`.
7. Stage 4 history rewrite, only if desired.

Stages 1 and 2 are independent; doing stage 1 alone already caps the problem at
today's 370 MB and buys back the full 654 MB of headroom indefinitely.
