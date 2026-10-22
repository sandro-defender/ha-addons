# Why Home Assistant didn't get new add-on versions — and how it stays fixed

## TL;DR

Nothing was broken in Home Assistant. This repository includes every add-on
as a **git submodule**, and a submodule is not a live link — it is a *pin* to
one exact upstream commit. Home Assistant Supervisor only ever reads the
pinned commits, so a new upstream release stayed invisible until somebody
committed a new pin here. Nobody (or nothing) did.

**Fix installed:** [`.github/workflows/sync-addons.yml`](../.github/workflows/sync-addons.yml)
runs hourly, fast-forwards every pin whose add-on `version:` changed upstream,
and commits the result. From then on the normal Home Assistant machinery
(store refresh every 3 hours, or **⋮ → Check for updates**) picks it up with
no manual work.

The rest of this file explains the mechanism, lists the alternative
solutions (with trade-offs, in case you prefer one of them), and documents
the one rule no automation here can work around: **upstream must bump
`version:` in its add-on config for every release.**

---

## 1. Evidence (verified 2026-10-22)

| Submodule in this repo | Add-on config (what Supervisor reads) | Version pinned here | Version at upstream `main` | Result |
| --- | --- | --- | --- | --- |
| `ha-mcp` | `agent_gateway/config.yaml` | **0.1.0** (`2ca33a9`) | **0.3.0** (`205345f`) | ❌ store offered 0.1.0 forever |
| `Github-Sync` | `github_sync/config.yaml` | 0.9.5 (`8efc448`) | 0.9.5 | ✅ (by luck — nothing moved yet) |
| `HomeAssistantEditor` | `homeassistant-editor/config.json` | 1.2.1 (`fcd729c`) | 1.2.1 | ✅ (same) |
| `HomeAssistantVersionControl` | `homeassistant-version-control/config.json` | 1.3.0 (`5bab6b5`) | 1.3.0 | ✅ (same) |
| `HomeAssistantTimeMachine` | `homeassistant-time-machine/config.yaml` | 2.3.1 (`2370de1`) | 2.3.1 — although upstream tagged/released **v2.3.2** | ⚠️ see §6 |

Every “working” row only worked because the pin happened to be created at
the latest release. The first future upstream release would have broken each
of them in exactly the same way as `ha-mcp`.

## 2. How Supervisor actually decides “update available”

Verified against the Supervisor source (`home-assistant/supervisor`):

1. **Clone/refresh** (`supervisor/store/git.py`): repositories are cloned
   with `--recursive --depth=1 --shallow-submodules`. On refresh it fetches
   *this* repository, hard-resets if its HEAD moved, then runs
   `git submodule update --init --recursive --depth 1` — **without
   `--remote`**. So submodules are always reset to the *pinned* commit.
   `branch = main` in `.gitmodules` only matters when `--remote` is used;
   Supervisor never uses it. It does **not** mean “always latest”.
2. **No HEAD move → no re-read** (`supervisor/store/__init__.py`): if this
   repo's HEAD didn't change, `reload()` skips rebuilding the catalogue
   entirely. An upstream-only change is doubly invisible.
3. **Discovery** (`supervisor/store/data.py`): the catalogue is built by
   globbing `**/config.{yaml,yml,json}` (excluding dot-folders and
   `rootfs`). That's why the nested layout
   (`ha-mcp/agent_gateway/config.yaml`, …) works at all — and why the
   version Supervisor sees is whatever that file contains *at the pinned
   commit*.
4. **Update offer** (`supervisor/apps/app.py`): `need_update` is simply
   `installed_version != latest_version`, where both come from `version:`
   in the add-on config. No version bump ⇒ no update, ever, no matter how
   much code changed. For add-ons with `image:` (only `Github-Sync` here)
   the image tag pulled is also the `version:` string; the other four are
   built locally from the Dockerfile in the checked-out (pinned) tree.
5. **Timing** (`supervisor/misc/tasks.py`): the store is reloaded every
   `RUN_RELOAD_APPS = 10800`s (**3 hours**), plus immediately when you press
   **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**. This side
   was always working — it just kept fetching a repository that never
   changed.

Failure chain:

```
upstream releases 0.3.0 ──✗ (no pin bump committed here)──► ha-addons HEAD unchanged
        ► Supervisor fetch: changed=False ► catalogue not re-read
        ► latest_version still 0.1.0 == installed ► "no update" (correctly!)
```

Note: a template for this exact workflow already existed in
`sandro-defender/ha-mcp` (`tools/ha-addons-sync/sync-addons.yml`, plus a
daily watchdog that can only warn — a workflow's `GITHUB_TOKEN` cannot push
to a different repository). It was never copied into this repo, and as
written it only covered the two owned add-ons and only read `config.yaml`
(it would have silently skipped the two `config.json` add-ons forever).
The installed version covers all five submodules and both config formats.

## 3. Solutions at a glance

| # | Solution | Latency after upstream release | Effort | Keeps single store URL? | Review gate? |
| --- | --- | --- | --- | --- | --- |
| **1** | **Hourly sync workflow in this repo (installed)** | ≤ 1 h here + ≤ 3 h in HA (instant with “Check for updates”) | done | ✅ | ❌ (auto-trusts upstream `main`) |
| 1b | 1 + `repository_dispatch` from owned upstreams | ~1 min + HA side | small, per owned repo | ✅ | ❌ |
| 1c | Dependabot `gitsubmodule` PRs instead of direct pushes | until PR merged (+ auto-merge possible) | small | ✅ | ✅ |
| 2 | Add each upstream repo directly in the HA store | HA side only (≤ 3 h) | per HA instance | ❌ 5 URLs | ❌ |
| 3 | Drop submodules; vendor add-on folders via CI | same as 1 | medium migration | ✅ | either |
| 4 | HA-side: auto-update toggle / `update.install` automation | — (applies updates, doesn't discover them faster) | per HA instance | n/a | n/a |

Solutions 1–3 make updates *visible*; solution 4 only makes them *apply*
automatically once visible. You almost certainly want 1 (done) + 4.

## 4. Solution 1 — hourly pin sync (installed)

[`.github/scripts/sync_addons.py`](../.github/scripts/sync_addons.py) +
[`.github/workflows/sync-addons.yml`](../.github/workflows/sync-addons.yml):

- Triggers: hourly cron (`17 * * * *`), manual **Actions → Sync add-ons from
  upstream → Run workflow** (with `force` / `only` inputs), and
  `repository_dispatch` type `addon-updated` (see §4.1).
- For every submodule in `.gitmodules` it reads the pinned add-on version,
  fast-forwards to the branch head (`git submodule update --remote`), and
  **only keeps the move if `version:` changed** — the only change Home
  Assistant can act on. Moves without a version bump are reverted and
  surface as a warning in the job summary (that row reads
  `moved-no-version-bump`).
- Versions containing `-` (`1.4.2-beta.1`, …) are skipped: this is the
  stable repository. Use the manual `force` input to override.
- One broken upstream doesn't block the rest; the job still commits the good
  bumps and then fails visibly.
- Commits are pushed with the repo's own `GITHUB_TOKEN`
  (`contents: write`) — no PAT to create, rotate or leak.

Run it locally any time (also the fastest way to check “is a pin stale?”):

```bash
python3 .github/scripts/sync_addons.py --dry-run   # report only
python3 .github/scripts/sync_addons.py             # bump, then git add/commit/push yourself
python3 .github/scripts/sync_addons.py --only ha-mcp --force
```

**Caveats**

- Scheduled workflows only run from the **default branch** — this fix starts
  working once it is on `main`.
- If `main` becomes protected (required PRs/reviews), the bot push will be
  rejected. Either allow GitHub Actions to bypass, or switch to §4.2.
- GitHub disables scheduled workflows after 60 days without repository
  activity (it e-mails first). Any successful sync commit counts as
  activity; if all upstreams go quiet for 60 days, re-enable it from the
  Actions tab.
- It trusts upstream `main`: if an upstream bumps `version:` mid-development
  on `main`, this repo will publish it within the hour. If that ever
  becomes a problem, switch that submodule to §4.2's review gate — or talk
  to the upstream about only bumping versions on release commits (the
  current five all do this).

### 4.1 Make it instant for the repos you own (`repository_dispatch`)

Hourly polling is required for the three `saihgupr/*` repos (you can't add
workflows to someone else's repo), but `ha-mcp` and `Github-Sync` can *push*
instead of being polled. The workflow above already listens for it. In each
owned upstream:

1. Create a fine-grained PAT with **Contents: write** on
   `sandro-defender/ha-addons` only; store it in the upstream repo as secret
   `HA_ADDONS_DISPATCH_TOKEN`. (A cross-repo push is the one thing
   `GITHUB_TOKEN` cannot do, hence the PAT.)
2. Add `.github/workflows/notify-ha-addons.yml`:

```yaml
name: Notify ha-addons
on:
  push:
    branches: [main]
    paths: ["agent_gateway/config.yaml"]   # ← that repo's add-on config
  release:
    types: [published]
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - env:
          GH_TOKEN: ${{ secrets.HA_ADDONS_DISPATCH_TOKEN }}
        run: |
          gh api repos/sandro-defender/ha-addons/dispatches \
            -f event_type=addon-updated \
            -f "client_payload[addon]=ha-mcp"
```

For `Github-Sync`, trigger on `release: [published]` (and use
`client_payload[addon]=Github-Sync`) — its `image:` is pre-built, so the pin
must only move *after* the `ghcr.io` image for that version exists.

### 4.2 Review-gated variant (Dependabot)

Prefer a human (or CI) in the loop before a new upstream version reaches
your Home Assistant? Replace the direct-push workflow with
`.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "gitsubmodule"
    directory: "/"
    schedule:
      interval: "daily"
```

Dependabot opens one PR per moved submodule; merging the PR moves the pin.
(You can auto-merge Dependabot PRs with a small workflow once you trust it.)
Don't run this *and* the hourly direct-push workflow for the same
submodules — pick one owner per pin.

## 5. Solution 2 — skip the aggregator, add upstreams directly in HA

Every upstream here is itself a valid add-on repository (`repository.json` /
`repository.yaml` at its root), so the simplest possible setup needs no pins
at all. In **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add:

```
https://github.com/sandro-defender/ha-mcp
https://github.com/sandro-defender/Github-Sync
https://github.com/saihgupr/HomeAssistantEditor
https://github.com/saihgupr/HomeAssistantVersionControl
https://github.com/saihgupr/HomeAssistantTimeMachine
```

Updates then flow as soon as Supervisor refreshes (≤ 3 h). Trade-offs:
five store URLs to manage on every HA instance instead of one; no curation
(every version bump each maintainer pushes reaches you directly); and —
important — **the same add-on from a different repository is a different
add-on to Supervisor** (its ID is `<repo-hash>_<slug>`). Switching source
means: back up the add-on, uninstall the old entry, install from the new
repository, restore/re-enter its options. Installed add-ons don't migrate
or cross-update.

## 6. The rule nothing here can fix: upstream must bump `version:`

Home Assistant compares version strings, nothing else. Proof from the table
in §1: `HomeAssistantTimeMachine` has a GitHub release/tag **v2.3.2**, but
`homeassistant-time-machine/config.yaml` still says `version: 2.3.1` both at
that tag and at current `main`. No pin bump, store trick or HA setting will
ever offer “2.3.2” — as far as Supervisor is concerned it doesn't exist.
The sync workflow will report this repo as `moved-no-version-bump` with a
warning until upstream fixes its config.

Release checklist for every upstream (owned ones especially):

1. Bump `version:` in the add-on config (`config.yaml`/`config.json`) — in
   the same commit as the release, and keep tag = version (e.g. `v0.9.5`).
2. For `image:` add-ons: build & push `ghcr.io/...:<new-version>` **before**
   the pin can move, or users get an update that fails to pull.
3. Don't bump `version:` on `main` until the release is actually ready —
   the hourly sync will publish it.

## 7. Solution 4 — Home Assistant side (do this too)

None of this discovers updates faster than the 3-hour store refresh on its
own, but once a version is visible it can apply itself:

- **Manual refresh:** Add-on Store → **⋮ → Check for updates** (forces the
  same fetch/reset/re-read described in §2, immediately).
- **Per add-on auto-update:** open the add-on → enable **Auto update**.
  Supervisor then installs new versions as they appear (it still respects
  `breaking_versions` declared in the add-on config).
- **Fully hands-off:** the `update.<addon>_update` entities can drive an
  automation calling `update.install` (optionally excluding add-ons you
  want to babysit). Prefer the built-in Auto update toggle unless you need
  schedules/conditions (e.g. only at 04:00, never while away).

## 8. Manual one-off bump & troubleshooting

Bump one pin by hand (what the workflow automates):

```bash
git clone https://github.com/sandro-defender/ha-addons.git && cd ha-addons
git submodule update --init --remote --depth 1 -- ha-mcp
git add ha-mcp && git commit -m "Update ha-mcp to <version>" && git push
# then in HA: Add-on Store → ⋮ → Check for updates
```

Is a pin stale right now?

```bash
git submodule status
git ls-remote https://github.com/sandro-defender/ha-mcp.git HEAD
python3 .github/scripts/sync_addons.py --dry-run
```

If HA still shows an old version after the pin moved:

1. Add-on Store → ⋮ → **Check for updates**; wait a few seconds, hard-refresh
   the page.
2. Check **Settings → System → Logs → Supervisor** for
   `supervisor.store.git` errors (`Can't update ... repo`) — a corrupt local
   clone needs the repository removed/re-added (or the suggested
   **Repair**/reset action) before it can fetch again.
3. Confirm the version really changed at the pinned commit (`--dry-run`
   table). Code-only changes with the same `version:` are a no-op *by
   design* (§6); to force a rebuild of the same version locally, use the
   add-on's **⋮ → Rebuild** in HA — that rebuilds from the currently pinned
   tree.
4. Make sure you installed the add-on from *this* repository. The same slug
   installed from another repository (§5) will never update from here.

## 9. Long-term alternative (Solution 3): vendor instead of submodules

The layout used by `home-assistant/addons` and `hassio-addons/repository` —
plain add-on folders in one repo, no submodules — avoids this whole class of
problem: a CI job would copy each upstream's add-on folder
(`agent_gateway/`, `github_sync/`, …) into a top-level folder here on the
same hourly/dispatch triggers, producing a normal, reviewable diff instead
of a pin move, and insulating the store from upstream tree noise (Supervisor
globs `**/config.*`, so any stray `config.json` upstream currently becomes a
phantom add-on here). It costs a one-time migration and gives up the
“exact upstream commit” guarantee submodules provide. Not needed while the
sync above works — recorded here in case submodule friction ever outweighs
it.
