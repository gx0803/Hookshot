#!/usr/bin/env python3
"""Resolve ONLY the rebase conflicts registered in known-conflicts.json.

When feat/menu-i18n is rebased onto a newer upstream/main, most files merge
cleanly (menu_i18n.h / menu_zh.json are new files; vk_menu.h and the two font
files are almost never touched by upstream). Occasionally upstream inserts code
at the exact spot where an i18n hook was inserted. Those are often two
INDEPENDENT insertions at the same location -- not two edits of the same line --
so the correct resolution is to keep both sides.

This tool never guesses. It reads known-conflicts.json, and for every conflict
block in the target file it demands a byte-exact match of BOTH sides against one
registered entry before keeping them. Any conflict that is missing from the
registry, or whose text differs even by one byte, aborts the whole run without
writing -- so a new upstream edit can never be silently dropped.

    python -I -B scripts/sync/resolve-rebase-conflicts.py <conflicted-file> --dump
    python -I -B scripts/sync/resolve-rebase-conflicts.py <conflicted-file> --check
    python -I -B scripts/sync/resolve-rebase-conflicts.py <conflicted-file>

--dump   print each conflict's two sides as JSON strings (to register a new one)
--check  verify all conflicts are known/resolvable but do NOT write
default  verify and write the resolved file (LF preserved, no BOM)
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "known-conflicts.json"
PAT = re.compile(r"^<<<<<<<[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n", re.M | re.S)


def dump(conflicts):
    out = []
    for i, (ours, theirs) in enumerate(conflicts, 1):
        out.append("--- conflict %d ---" % i)
        out.append("ours:   " + json.dumps(ours, ensure_ascii=True))
        out.append("theirs: " + json.dumps(theirs, ensure_ascii=True))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="conflicted source file (e.g. gpu/vk/vk_menu.cpp)")
    ap.add_argument("--dump", action="store_true", help="print conflict sides as JSON and exit")
    ap.add_argument("--check", action="store_true", help="verify without writing")
    a = ap.parse_args()

    target = Path(a.target)
    if not target.is_file():
        print("!! no such file: %s" % target, file=sys.stderr)
        return 2
    text = target.read_text(encoding="utf-8")
    conflicts = [(m.group(1), m.group(2)) for m in PAT.finditer(text)]
    if not conflicts:
        print("OK  no conflict markers in %s" % target.name)
        return 0
    if a.dump:
        print(dump(conflicts))
        return 0

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_pair = {}
    for c in reg["conflicts"]:
        key = (c["ours"].strip(), c["theirs"].strip())
        if key in by_pair:
            print("!! duplicate (ours,theirs) pair in registry: ids '%s' and '%s'" % (by_pair[key]["id"], c["id"]), file=sys.stderr)
            return 1
        by_pair[key] = c

    errors = []
    for i, (ours, theirs) in enumerate(conflicts, 1):
        key = (ours.strip(), theirs.strip())
        hit = by_pair.get(key)
        if hit is None:
            errors.append("conflict %d is NOT registered (or text changed). Run with --dump and add it to %s." % (i, REGISTRY.name))
            errors.append(dump([(ours, theirs)]))
            continue
        expected_name = Path(hit["file"]).name
        if target.name != expected_name:
            errors.append("conflict %d matched id '%s' but that entry is for file '%s', target basename is '%s'." % (i, hit["id"], expected_name, target.name))
            continue
        print("OK  conflict %d -> id '%s' (%s)" % (i, hit["id"], hit["reason"]))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        print("\nABORTED: nothing was written. Resolve the unregistered conflict manually, then register it.", file=sys.stderr)
        return 1

    if a.check:
        print("OK  --check: all %d conflicts are registered and byte-exact; file left untouched" % len(conflicts))
        return 0

    out = PAT.sub(lambda m: m.group(1) + m.group(2), text)
    if "<<<<<<<" in out or ">>>>>>>" in out or "\n=======\n" in out:
        print("!! markers remain after substitution; aborting", file=sys.stderr)
        return 1
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print("OK  resolved %d registered pure-insertion conflicts in %s (both sides kept, LF)" % (len(conflicts), target.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
