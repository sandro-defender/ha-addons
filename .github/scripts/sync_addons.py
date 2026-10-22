#!/usr/bin/env python3
"""Fast-forward add-on submodule pins to their upstream branch heads.

Why this exists
---------------
Home Assistant Supervisor never follows a submodule's branch. When it clones
or refreshes this repository it runs (supervisor/store/git.py):

    git clone --recursive --depth=1 --shallow-submodules ...
    git fetch --depth=1            # of THIS repository only
    git submodule update --init --recursive --depth 1     # NOTE: no --remote

`git submodule update` without `--remote` checks out the commit recorded in
this repository, and `branch = main` in .gitmodules is ignored unless
`--remote` is passed. Supervisor then builds its catalogue by scanning for
`**/config.{yaml,yml,json}` and only offers an update when the `version:`
in that file differs from the installed one. It also skips re-reading the
catalogue entirely when this repository's own HEAD did not move.

Consequence: a new release in an upstream add-on repo is invisible in Home
Assistant until someone commits a new submodule pin HERE. This script is
that someone. It is run hourly by .github/workflows/sync-addons.yml (and on
demand / on repository_dispatch from upstream repos we own).

Policy
------
* Every submodule in .gitmodules is checked (use --only to limit).
* A pin is only moved when the add-on `version:` actually changed, because
  that is the only change Home Assistant can ever act on. Moves without a
  version bump are reverted and reported (upstream forgot to bump, or the
  commits are e.g. docs/CI only). Pass --force to move anyway.
* Versions containing "-" (1.4.2-beta.1, 2.0.0-rc1, ...) are treated as
  pre-releases and skipped: this is the stable repository. Pass
  --allow-prerelease (or --force) to publish them anyway.
* One broken upstream never blocks the others: errors are collected, good
  bumps are kept, and the script exits 1 at the end so the failure is
  visible in the Actions tab.

Usage
-----
    python3 .github/scripts/sync_addons.py [--dry-run] [--force]
                                           [--allow-prerelease]
                                           [--only ha-mcp] ...

With --dry-run nothing is kept: every submodule is restored to its pin and
the script only reports what would change. Without it, bumped submodules are
left checked out at their new commits so the caller can `git add` + commit
(locally) or the workflow can commit + push (CI).

Outputs (CI): writes `changed=true|false` to $GITHUB_OUTPUT, a markdown
table to $GITHUB_STEP_SUMMARY, and $RUNNER_TEMP/bumped-paths.txt +
$RUNNER_TEMP/commit-message.txt for the commit step.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Where Supervisor (path.glob("**/config.*")) finds each add-on's config.
# Kept explicit so a stray config.* appearing in an upstream tree can never
# silently change which file we read the version from. Submodules not listed
# here fall back to Supervisor-style discovery (shallowest match wins).
CONFIG_PATHS = {
    "Github-Sync": "github_sync/config.yaml",
    "HomeAssistantEditor": "homeassistant-editor/config.json",
    "HomeAssistantTimeMachine": "homeassistant-time-machine/config.yaml",
    "HomeAssistantVersionControl": "homeassistant-version-control/config.json",
    "ha-mcp": "agent_gateway/config.yaml",
}

CONFIG_SUFFIXES = {".yaml", ".yml", ".json"}
VERSION_RE = re.compile(r"^version:\s*[\"']?([^\"'\s#]+)", re.MULTILINE)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def submodule_paths() -> list[str]:
    out = git(
        "config", "-f", ".gitmodules", "--get-regexp", r"submodule\..*\.path$"
    ).stdout
    return [line.split(None, 1)[1].strip() for line in out.splitlines() if line.strip()]


def pinned_sha(path: str) -> str:
    out = git("ls-tree", "HEAD", "--", path).stdout.strip()
    # "160000 commit <sha>\t<path>"
    return out.split()[2] if out else ""


def checkout(path: str, remote: bool) -> None:
    args = ["submodule", "update", "--init", "--depth", "1"]
    if remote:
        args.append("--remote")
    args += ["--", path]
    result = git(*args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def current_sha(path: str) -> str:
    return git("-C", path, "rev-parse", "HEAD").stdout.strip()


def find_config(submodule: str) -> Path | None:
    base = REPO_ROOT / submodule
    known = CONFIG_PATHS.get(submodule)
    if known:
        candidate = base / known
        return candidate if candidate.is_file() else None
    matches = [
        p
        for p in base.glob("**/config.*")
        if p.suffix in CONFIG_SUFFIXES
        and not [part for part in p.parts if part.startswith(".") or part == "rootfs"]
    ]
    return min(matches, key=lambda p: len(p.parts)) if matches else None


def read_version(submodule: str) -> str | None:
    config = find_config(submodule)
    if config is None:
        return None
    text = config.read_text(encoding="utf-8", errors="replace")
    if config.suffix == ".json":
        try:
            return str(json.loads(text).get("version") or "") or None
        except json.JSONDecodeError:
            return None
    match = VERSION_RE.search(text)
    return match.group(1) if match else None


def is_prerelease(version: str | None) -> bool:
    return bool(version) and "-" in version


def short(sha: str) -> str:
    return sha[:7] if sha else "?"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report only; keep nothing")
    parser.add_argument("--force", action="store_true", help="move pins even if version is unchanged/pre-release")
    parser.add_argument("--allow-prerelease", action="store_true", help="publish versions containing '-'")
    parser.add_argument("--only", action="append", default=[], metavar="PATH", help="limit to this submodule (repeatable)")
    args = parser.parse_args()

    paths = submodule_paths()
    if args.only:
        unknown = [p for p in args.only if p not in paths]
        if unknown:
            print(f"::warning::--only names not in .gitmodules: {', '.join(unknown)}")
        paths = [p for p in paths if p in args.only]

    rows: list[dict[str, str]] = []
    bumped: list[str] = []
    errors: list[str] = []

    for path in paths:
        row = {"path": path, "status": "", "old": "?", "new": "?", "detail": ""}
        try:
            pinned = pinned_sha(path)
            if not pinned:
                raise RuntimeError("not a submodule pin in HEAD")

            checkout(path, remote=False)  # what Home Assistant currently sees
            old_version = read_version(path)
            row["old"] = f"{old_version or 'unknown'} ({short(pinned)})"

            checkout(path, remote=True)  # tip of the branch in .gitmodules
            new = current_sha(path)
            new_version = read_version(path)
            row["new"] = f"{new_version or 'unknown'} ({short(new)})"

            if new == pinned:
                row["status"] = "up-to-date"
            elif new_version is None:
                checkout(path, remote=False)
                row["status"] = "error"
                row["detail"] = "could not read version from add-on config at upstream head"
                errors.append(f"{path}: {row['detail']}")
            elif is_prerelease(new_version) and not (args.force or args.allow_prerelease):
                checkout(path, remote=False)
                row["status"] = "skipped-prerelease"
                row["detail"] = "stable repository: pre-release versions are not published"
            elif new_version == old_version and not args.force:
                checkout(path, remote=False)
                row["status"] = "moved-no-version-bump"
                row["detail"] = (
                    "upstream moved but version is unchanged; Home Assistant "
                    "would not offer an update, so the pin is not moved"
                )
                print(
                    f"::warning::{path}: upstream is at {short(new)} but version "
                    f"is still {new_version}. If this was meant to be a release, "
                    f"upstream must bump `version:` in its add-on config."
                )
            else:
                row["status"] = "would-bump" if args.dry_run else "bumped"
                if args.dry_run:
                    checkout(path, remote=False)
                else:
                    bumped.append(path)
        except Exception as err:  # noqa: BLE001 - one upstream must not block the rest
            row["status"] = "error"
            row["detail"] = str(err)
            errors.append(f"{path}: {err}")
            print(f"::error::{path}: {err}")
            git("submodule", "update", "--init", "--depth", "1", "--", path, check=False)
        rows.append(row)

    # ---- human-readable table (stdout + job summary) ---------------------
    header = "| Add-on (submodule) | Pinned now | Upstream head | Result |"
    lines = [header, "| --- | --- | --- | --- |"]
    for r in rows:
        result = r["status"] + (f" — {r['detail']}" if r["detail"] else "")
        lines.append(f"| `{r['path']}` | {r['old']} | {r['new']} | {result} |")
    table = "\n".join(lines)
    print("\n" + table + "\n")
    if args.dry_run:
        print("Dry run: nothing was kept.")
    elif bumped:
        print("Bumped: " + ", ".join(bumped))
    else:
        print("Nothing to sync.")

    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write("### Add-on submodule sync\n\n" + table + "\n")

    # ---- machine-readable outputs for the workflow commit step -----------
    changed = bool(bumped) and not args.dry_run
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with open(output_file, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")

    temp_dir = Path(os.environ.get("RUNNER_TEMP") or "/tmp")
    if changed:
        message_lines = [
            "chore(sync): publish new add-on versions",
            "",
            "Submodule pins fast-forwarded by .github/workflows/sync-addons.yml",
            "so Home Assistant can see the new upstream releases:",
            "",
        ]
        for r in rows:
            if r["status"] == "bumped":
                message_lines.append(f"- {r['path']}: {r['old']} -> {r['new']}")
        (temp_dir / "bumped-paths.txt").write_text("\n".join(bumped) + "\n", encoding="utf-8")
        (temp_dir / "commit-message.txt").write_text("\n".join(message_lines) + "\n", encoding="utf-8")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
