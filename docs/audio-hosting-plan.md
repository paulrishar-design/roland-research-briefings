# Audio hosting plan: getting episode MP3s out of the repo

Status: proposal, not yet executed. Measured 2026-09-02 against `main` @ `1c39863`.

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

### Stage 0 — spike: confirm the served Content-Type (30 min, do this first)

The signed redirect URL carries explicit `rsct=` (response content type) and
`rscd=` parameters, which are populated from the asset's stored `content_type`.
That field is set from the `Content-Type` header sent at upload. So uploading
with `Content-Type: audio/mpeg` *should* make GitHub serve the MP3 as
`audio/mpeg`. This was inferred from the redirect URL's shape, **not** proven —
the public API for other repos is unreachable from this environment, so prove
it with one throwaway upload before committing to the design.

```bash
OWNER=paulrishar-design; REPO=roland-research-briefings
# 1. create a draft release so nothing is user-visible while testing
REL=$(curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/$OWNER/$REPO/releases \
  -d '{"tag_name":"audio-spike","name":"spike","draft":true}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2. upload one MP3 with an explicit audio content type
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: audio/mpeg" \
  --data-binary @public/episodes/alex-finn.mp3 \
  "https://uploads.github.com/repos/$OWNER/$REPO/releases/$REL/assets?name=alex-finn.mp3"

# 3. observe what is actually served (expect: 206, audio/mpeg)
curl -sSL -r 0-99 -o /dev/null -D - \
  "https://github.com/$OWNER/$REPO/releases/download/audio-spike/alex-finn.mp3" \
  | grep -iE '^(HTTP/|content-type|content-range|accept-ranges)'

# 4. delete the draft release
curl -sS -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/releases/$REL
```

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

1. Upload all 24 MP3s to the `audio` release.
2. Verify all 24 resolve (`curl -sSLo /dev/null -w '%{http_code}'`) and that
   each `Content-Length` matches the `length` attribute already in the feed.
3. Rewrite the 24 `<enclosure url>` values to the release URLs. Leave
   **`<guid>` untouched** — see §4.
4. Commit the feed change alone. `public/episodes/` stays in place for now.

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

**`scripts/validate_public.py` will fail CI the moment stage 2 lands.** Two
checks assume audio lives in the tree:

```python
# breaks: release URLs have no matching file under public/
if not filename or filename not in public_files:
    fail(f"RSS enclosure has no matching public artifact: ...")

# breaks: public/episodes/ will be empty or gone after stage 3
audio_files = list((PUBLIC / "episodes").glob("*.mp3"))
if not audio_files or any(path.stat().st_size == 0 for path in audio_files):
    fail("episode audio is missing or empty")
```

Replace with logic that classifies each enclosure by URL:

- **Pages-hosted** (under the Pages base URL) → must exist in `public/`, as today.
  Keeps the validator correct during the stage-2/3 overlap, when the feed is
  mixed.
- **Release-hosted** → assert the URL matches the expected
  `github.com/<owner>/<repo>/releases/download/<tag>/<name>.mp3` shape, and that
  `<name>` is a clean slug.
- Replace the "episodes dir is non-empty" check with "the feed has at least one
  enclosure and every enclosure carries a non-zero `length` and a
  `type="audio/mpeg"`".
- Optionally add an opt-in `--check-remote` flag that HEADs each release URL,
  for use on a schedule rather than on every push (it costs network in CI).

The secret/path-leak scanning at the top of the file is unaffected and should be
kept exactly as-is.

**`.github/workflows/pages.yml`** needs no change to keep working, but after
stage 3 the `upload-pages-artifact` step goes from 370 MB to ~1 MB, which
removes the 10-minute-timeout concern entirely.

## 6. Suggested order of work

1. Stage 0 spike — answers the one open question, ~30 minutes, fully reversible.
2. Update `validate_public.py` to accept both URL shapes — must land *before*
   stage 2 or CI goes red.
3. Stage 1 forward fix in the external generator — stops new growth.
4. Stage 2 backfill + feed rewrite.
5. Wait 1–2 weeks.
6. Stage 3 `git rm`.
7. Stage 4 history rewrite, only if desired.

Stages 1 and 2 are independent; doing stage 1 alone already caps the problem at
today's 370 MB and buys back the full 654 MB of headroom indefinitely.
