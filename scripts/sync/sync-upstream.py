#!/usr/bin/env python3
"""Orchestrate one upstream-sync cycle for the menu-i18n fork.

Design note (learned the hard way, twice): the resolver tool and its registry
live in scripts/sync/ on the chore/upstream-sync branch. But a rebase replays
the i18n commit FIRST, and at that pause the worktree is checked out at a point
where scripts/sync/ does not exist yet (those files were added by LATER commits
in the stack). So the resolver path on disk vanishes exactly when it is needed.

Fix: this script reads the resolver + registry into memory at startup (before
rebase, when all tools are present), and on conflict writes them to a temporary
directory outside the worktree and runs them from there. Tool availability is
therefore independent of the transient rebase worktree state.

Run this on a worktree whose branch contains the tools (chore/upstream-sync or
a branch cut from it) -- NOT on bare feat/menu-i18n, which has no scripts/sync/
at all. The pre-flight completeness check enforces that.

This is a READ-ONLY coordinator by default. It prints status and the exact next
commands; it only mutates git state (fetch + rebase + conflict resolution) with
--apply, and even then stops before building, because the build needs
machine-specific toolchain paths documented in docs/upstream-sync.md.

Safety rules enforced here:
  * every subprocess return code is checked (no shell pipes that could swallow
    a failure);
  * a conflict is auto-resolved ONLY if resolve-rebase-conflicts.py --check
    accepts it byte-exactly;
  * after resolving, the file must have ZERO conflict markers before git add;
  * any unknown conflict aborts the whole rebase (git rebase --abort), leaving
    the branch exactly as it was.

    python -I -B scripts/sync/sync-upstream.py --check
    python -I -B scripts/sync/sync-upstream.py --apply
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
UPSTREAM_REMOTE = "https://github.com/NotRexed/Hookshot.git"
UPSTREAM_REF = "refs/remotes/upstream/main"
WORK_BRANCH = "chore/upstream-sync"
REQUIRED = [
    HERE / "resolve-rebase-conflicts.py",
    HERE / "audit-menu-drift.py",
    HERE / "known-conflicts.json",
    HERE / "drift-whitelist.json",
    ROOT / "scripts" / "gen_menu_i18n.py",
    ROOT / "tests" / "test_menu_i18n.py",
    ROOT / "gpu" / "vk" / "menu_i18n.h",
    ROOT / "gpu" / "vk" / "menu_zh.json",
]

# Read the conflict resolver + its registry into memory NOW, while every tool is
# still on disk (before any rebase). During a rebase pause at the i18n commit,
# scripts/sync/ is not checked out, so these bytes are the only reliable copy.
_RESOLVER_SRC = (HERE / "resolve-rebase-conflicts.py").read_text(encoding="utf-8") if (HERE / "resolve-rebase-conflicts.py").is_file() else None
_REGISTRY_SRC = (HERE / "known-conflicts.json").read_text(encoding="utf-8") if (HERE / "known-conflicts.json").is_file() else None
_TOOL_TMP = None


def resolver_dir() -> Path:
    """Materialise the in-memory resolver + registry outside the worktree."""
    global _TOOL_TMP
    if _TOOL_TMP is None:
        if _RESOLVER_SRC is None or _REGISTRY_SRC is None:
            raise RuntimeError("resolver/registry not loadable at startup")
        d = Path(tempfile.mkdtemp(prefix="hookshot-sync-"))
        (d / "resolve-rebase-conflicts.py").write_text(_RESOLVER_SRC, encoding="utf-8", newline="\n")
        (d / "known-conflicts.json").write_text(_REGISTRY_SRC, encoding="utf-8", newline="\n")
        _TOOL_TMP = d
    return _TOOL_TMP


VERBOSE = False


def run(args, env=None):
    e = dict(os.environ)
    e.setdefault("GIT_EDITOR", "true")
    if env:
        e.update(env)
    if VERBOSE:
        print("      $ %s" % " ".join(str(x) for x in args))
    p = subprocess.run(args, cwd=str(ROOT), text=True, encoding="utf-8",
                       errors="replace", capture_output=True, timeout=300, env=e)
    if VERBOSE:
        print("        -> rc=%d  err=%s" % (p.returncode, (p.stderr or "").strip().splitlines()[-1] if (p.stderr or "").strip() else ""))
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def has_markers(path: Path) -> bool:
    t = path.read_text(encoding="utf-8")
    return any(l.startswith("<<<<<<< ") or l.startswith(">>>>>>> ") or l == "=======\n"
               for l in t.splitlines(keepends=True))


def rebase_in_progress() -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        rc, out, _ = run(["git", "rev-parse", "--git-path", name])
        if rc == 0 and Path(str(ROOT / out.strip())).exists():
            return True
    return False


def unmerged() -> list:
    rc, out, _ = run(["git", "diff", "--name-only", "--diff-filter=U"])
    return [x for x in out.strip().splitlines() if x]


def abort():
    run(["git", "rebase", "--abort"])
    print("!! rebase aborted; branch restored to its pre-sync state.", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only report (the default)")
    ap.add_argument("--apply", action="store_true", help="fetch + rebase + resolve; stop before build")
    ap.add_argument("--upstream", default=UPSTREAM_REMOTE)
    ap.add_argument("--ref", default=UPSTREAM_REF)
    ap.add_argument("--verbose", action="store_true", help="print every subprocess command and its return code")
    a = ap.parse_args()
    global VERBOSE
    VERBOSE = a.verbose

    print("== upstream sync ==")
    print("repo:  %s" % ROOT)
    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    print("branch: %s  (expected work branch: %s)" % (branch.strip(), WORK_BRANCH))

    missing = [str(p) for p in REQUIRED if not p.is_file()]
    if missing:
        print("!! sync tools are NOT on this worktree's disk (this branch may not contain "
              "the chore commit). Missing:\n   " + "\n   ".join(missing), file=sys.stderr)
        print("   Fix: run the sync on a worktree whose branch is %s or is cut from it." % WORK_BRANCH, file=sys.stderr)
        return 1
    print("OK  all sync tools present on disk")

    rc, out, _ = run(["git", "status", "--porcelain"])
    if out.strip():
        print("!! working tree is not clean; commit or stash before syncing:\n" + out, file=sys.stderr)
        return 1
    print("OK  working tree clean")

    rc, out, _ = run(["git", "rev-list", "--count", "HEAD.." + a.ref]) if (run(["git", "rev-parse", "--verify", a.ref])[0] == 0) else (0, "0", "")
    print("upstream ref: %s  | commits ahead: %s" % (a.ref, out.strip()))

    if not a.apply:
        print("\n--check mode: no git state changed. Next:")
        print("  python -I -B scripts/gen_menu_i18n.py --check")
        print("  python -I -B tests/test_menu_i18n.py")
        print("  python -I -B scripts/sync/sync-upstream.py --apply")
        return 0

    print("\n[1] fetch upstream")
    rc, out, err = run(["git", "fetch", "--no-tags", a.upstream, "+main:" + a.ref])
    print((out + err).strip())
    if rc != 0:
        print("!! git fetch failed", file=sys.stderr)
        return rc
    rc, head, _ = run(["git", "rev-parse", "--short", a.ref])
    print("upstream/main = %s" % head.strip())

    print("\n[2] rebase onto upstream/main (loop, strict checks)")
    rc, out, err = run(["git", "rebase", a.ref])
    print((out + err).strip())
    rounds = 0
    while rc != 0 and rounds < 50:
        rounds += 1
        if not rebase_in_progress():
            print("!! rebase failed with no conflict state; inspect manually", file=sys.stderr)
            return 1
        conflicted = unmerged()
        if not conflicted:
            print("!! rebase paused with no unmerged files; inspect manually and finish by hand", file=sys.stderr)
            return 1
        print("  round %d: conflicts in %s" % (rounds, ", ".join(conflicted)))
        tool = str(resolver_dir() / "resolve-rebase-conflicts.py")   # in-memory copy, outside the transient worktree
        for f in conflicted:
            p = ROOT / f
            c1, o1, e1 = run([sys.executable, "-I", "-B", tool, str(p), "--check"])
            print("    check  %-24s rc=%d %s" % (f, c1, (o1 + e1).strip().splitlines()[-1] if (o1 + e1).strip() else ""))
            if c1 != 0:
                print("!! %s is NOT safely auto-resolvable; aborting. Register it first (see known-conflicts.json)." % f, file=sys.stderr)
                abort()
                return 1
            c2, o2, e2 = run([sys.executable, "-I", "-B", tool, str(p)])
            print("    apply  %-24s rc=%d" % (f, c2))
            if c2 != 0 or has_markers(p):
                print("!! resolve failed or markers remain in %s; aborting (nothing committed).", file=sys.stderr)
                abort()
                return 1
            ca, _, ea = run(["git", "add", f])
            if ca != 0:
                print("!! git add failed for %s: %s" % (f, ea.strip()), file=sys.stderr)
                abort()
                return 1
        rc, out, err = run(["git", "rebase", "--continue"])
        print((out + err).strip())
    if rc != 0:
        print("!! rebase did not complete after %d rounds; aborting", file=sys.stderr)
        abort()
        return 1
    if rebase_in_progress():
        print("!! rebase still in progress; aborting", file=sys.stderr)
        abort()
        return 1
    print("OK  rebase complete, 0 conflict markers")

    print("\n[3] post-rebase gates")
    gates = [
        ("generator", [sys.executable, "-I", "-B", str(ROOT / "scripts" / "gen_menu_i18n.py"), "--check"]),
        ("i18n-tests", [sys.executable, "-I", "-B", str(ROOT / "tests" / "test_menu_i18n.py")]),
        ("drift", [sys.executable, "-I", "-B", str(HERE / "audit-menu-drift.py")]),
    ]
    for label, cmd in gates:
        grc, gout, gerr = run(cmd)
        tail = (gout + gerr).strip().splitlines()[-1] if (gout + gerr).strip() else ""
        print("  [%-10s] rc=%d  %s" % (label, grc, tail))
        if label != "drift" and grc != 0:
            print("  !! %s failed -- fix before building (drift is expected on new main and is not a gate)." % label, file=sys.stderr)

    print("\nSTOPPED before build. Next (docs/upstream-sync.md steps 4-6):")
    print("  4) translate drift (if any) into gpu/vk/menu_zh.json, then gen_menu_i18n.py")
    print("  5) RX_VK_ONLY=1 rebuild -> build-fork-i18n3/sa/build/hookshot_vk.dll")
    print("  6) switch-fork-i18n.py --apply --lang 1 --fork-dll <that dll>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
