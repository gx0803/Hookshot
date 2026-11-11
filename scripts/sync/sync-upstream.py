#!/usr/bin/env python3
"""Orchestrate one upstream-sync cycle for the menu-i18n fork.

This is a READ-ONLY coordinator by default. It runs the safe checks and prints
the exact next commands; it never mutates git state or compiles anything unless
you pass --apply, and even then it only does the git rebase + conflict resolution
(it stops before building, because the build needs machine-specific toolchain
paths documented in docs/upstream-sync.md).

    python -I -B scripts/sync/sync-upstream.py --check          # report only (default)
    python -I -B scripts/sync/sync-upstream.py --apply          # fetch + rebase + resolve; stop before build

The i18n delta lives in ONE commit on feat/menu-i18n (on top of v2.1.0). We
rebase that commit onto the newest upstream/main: most files auto-merge, and the
few known pure-insertion conflicts are resolved by resolve-rebase-conflicts.py
against known-conflicts.json. Upstream's NEW menu strings are then found by
audit-menu-drift.py and must be translated from the game's official zh-Hans data
by a human before rebuilding.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
UPSTREAM_REMOTE = "https://github.com/NotRexed/Hookshot.git"
UPSTREAM_REF = "refs/remotes/upstream/main"
I18N_BRANCH = "feat/menu-i18n"


def run(args, capture=True):
    p = subprocess.run(args, cwd=str(ROOT), text=True, encoding="utf-8",
                       errors="replace", capture_output=capture, timeout=300)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only report (the default; no git state changed)")
    ap.add_argument("--apply", action="store_true", help="perform fetch + rebase + conflict resolution")
    ap.add_argument("--upstream", default=UPSTREAM_REMOTE)
    ap.add_argument("--ref", default=UPSTREAM_REF)
    a = ap.parse_args()

    print("== upstream sync check ==")
    print("repo:     %s" % ROOT)
    print("i18n br:  %s" % I18N_BRANCH)
    print("upstream: %s" % a.upstream)

    rc, out, _ = run(["git", "status", "--porcelain"])
    if out.strip():
        print("!! working tree is not clean; commit or stash before syncing:\n" + out, file=sys.stderr)
        return 1
    print("OK  working tree clean")

    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch.strip() != I18N_BRANCH:
        print("!! on branch '%s'; switch to %s first" % (branch.strip(), I18N_BRANCH), file=sys.stderr)
        return 1
    print("OK  on %s" % I18N_BRANCH)

    if not a.apply:
        print("\n--check mode: no git state changed.")
        print("Next steps (see docs/upstream-sync.md):")
        print("  1) python -I -B scripts/gen_menu_i18n.py --check")
        print("  2) python -I -B tests/test_menu_i18n.py")
        print("  3) python -I -B scripts/sync/sync-upstream.py --apply")
        return 0

    print("\n== fetching upstream ==")
    rc, out, err = run(["git", "fetch", "--no-tags", a.upstream, "main:" + a.ref])
    print((out + err).strip())
    if rc != 0:
        print("!! git fetch failed", file=sys.stderr)
        return rc

    rc, head, _ = run(["git", "rev-parse", "--short", a.ref])
    print("upstream/main now at %s" % head.strip())

    print("\n== rebasing %s onto upstream/main ==" % I18N_BRANCH)
    rc, out, err = run(["git", "rebase", a.ref])
    print((out + err).strip())
    if rc != 0:
        conflicted = run(["git", "diff", "--name-only", "--diff-filter=U"])[1].strip().splitlines()
        print("\nrebase paused on conflicts: %s" % ", ".join(conflicted))
        for f in conflicted:
            r2 = run([sys.executable, "-I", "-B", str(HERE / "resolve-rebase-conflicts.py"), str(ROOT / f), "--check"])
            if r2[0] != 0:
                print("!! conflict in %s is NOT safely auto-resolvable; resolve manually." % f, file=sys.stderr)
                print("   (run resolve-rebase-conflicts.py <file> --dump, register it, then rerun --check)", file=sys.stderr)
                return 1
        print("\nAll conflicts are registered. Apply resolution:")
        for f in conflicted:
            print("  python -I -B scripts/sync/resolve-rebase-conflicts.py %s" % f)
        print("  git add <files> && git rebase --continue")
        print("\nSTOPPED before build. Resolve above, then follow docs/upstream-sync.md steps 4-6.")
        return 2

    print("\nOK  rebase completed with no conflicts.")
    print("\n== post-rebase checks ==")
    for script, label in [("scripts/gen_menu_i18n.py", "generator"),
                          ("tests/test_menu_i18n.py", "i18n tests")]:
        rc, out, err = run([sys.executable, "-I", "-B", str(ROOT / script), "--check"] if "gen_menu" in script
                           else [sys.executable, "-I", "-B", str(ROOT / script)])
        print("[%s] rc=%d %s" % (label, rc, (out + err).strip().splitlines()[-1] if (out + err).strip() else ""))
    rc, out, err = run([sys.executable, "-I", "-B", str(HERE / "audit-menu-drift.py")])
    print("[drift]\n" + out.strip())

    print("\nSTOPPED before build. Next (docs/upstream-sync.md step 5): translate any drift into")
    print("gpu/vk/menu_zh.json, run gen_menu_i18n.py, then RX_VK_ONLY=1 rebuild, then switch-fork-i18n.py to install.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
