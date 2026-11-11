#!/usr/bin/env python3
"""Audit menu translation drift against the checked-in Chinese table.

After every `git fetch upstream main` + rebase, run this to see exactly which
menu strings NEWLY appeared in vk_menu.cpp and would fall back to English when
the menu language is Chinese. It extracts three groups from the source:

  * rows[] row names
  * rows[] help texts (adjacent C++ string literals are concatenated, matching
    how the compiler builds them -- a half-literal key would never match at runtime)
  * tx_* option-value helpers (string arrays and inline strings)

Anything not already in gpu/vk/menu_zh.json and not in drift-whitelist.json is
reported as drift. This tool ONLY reports -- it never guesses or machine-
translates. Translations must come from the game's official zh-Hans resources
(romfs CNzh MSBT) or an explicit human decision, per the project convention.

    python -I -B scripts/sync/audit-menu-drift.py            # human-readable report
    python -I -B scripts/sync/audit-menu-drift.py --check    # CI gate: exit 1 on any drift
    python -I -B scripts/sync/audit-menu-drift.py --json     # machine-readable list
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CPP = ROOT / "gpu" / "vk" / "vk_menu.cpp"
TABLE = ROOT / "gpu" / "vk" / "menu_zh.json"
WHITELIST = HERE / "drift-whitelist.json"

NUMERIC = re.compile(r"^\d+(?:\.\d+)?\s*(?:x|%|nits|GB|\+)??$")
FRAME_RATE = re.compile(r"^\d+(?:\s*\(.*\))?$")
PRINTF = re.compile(r"%(?:[-+ #0-9.]*)?(?:d|i|u|x|X|f|e|g|s|c|p|%)")


_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "?": "?"}


def unescape(s: str) -> str:
    """Undo C++ string escapes so a source literal equals its runtime value (and
    therefore the key in menu_zh.json). Without this, a help like
    Shows \\"Playing Hookshot\\" would never match its translated key."""
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESC:
                out.append(_ESC[nxt]); i += 2; continue
            if nxt == "x":  # \xNN
                m = re.match(r"[0-9a-fA-F]{1,2}", s[i + 2:])
                if m:
                    out.append(chr(int(m.group(0), 16))); i += 2 + len(m.group(0)); continue
        out.append(ch); i += 1
    return "".join(out)


def merged_literals(text):
    """String literals with adjacent (whitespace-only-separated) ones joined,
    mirroring C++ compile-time concatenation, then unescaped to runtime bytes."""
    tok = list(re.finditer(r'"((?:[^"\\]|\\.)*)"', text))
    out, i = [], 0
    while i < len(tok):
        val, end, j = tok[i].group(1), tok[i].end(), i + 1
        while j < len(tok) and text[end:tok[j].start()].strip() == "":
            val += tok[j].group(1)
            end = tok[j].end()
            j += 1
        out.append(unescape(val))
        i = j
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpp", type=Path, default=CPP)
    ap.add_argument("--table", type=Path, default=TABLE)
    ap.add_argument("--check", action="store_true", help="exit 1 if any non-whitelisted drift")
    ap.add_argument("--json", action="store_true", help="print drift as a JSON array")
    a = ap.parse_args()

    cpp = a.cpp.read_text(encoding="utf-8")
    data = json.loads(a.table.read_text(encoding="utf-8"))
    keys = {e["en"] for e in data["entries"]}
    wl = json.loads(WHITELIST.read_text(encoding="utf-8"))

    def skipped(s):
        if not s:
            return True
        if s in wl["exact"]:
            return True
        if any(s.startswith(p) for p in wl["prefix"]):
            return True
        if wl.get("numeric_only") and (NUMERIC.match(s) or FRAME_RATE.match(s)):
            return True
        if wl.get("printf_format_strings") and PRINTF.search(s):
            return True
        return False

    block = re.search(r"Row rows\[\] = \{(.*?)\n\};", cpp, re.S)
    block = block.group(1) if block else ""
    rows = re.findall(r'\{TAB_\w+,\s*"((?:[^"\\]|\\.)*)"', block)

    helps = []
    for seg in re.split(r"(?=\n\s*\{TAB_\w+,)", block):
        if "{TAB_" in seg:
            helps.extend(x for x in merged_literals(seg)[1:] if x)

    txvals = []
    for fn in re.finditer(r"const char\*\s+tx_\w+\([^)]*\)\s*\{(.*?)\n\}", cpp, re.S):
        txvals.extend(x for x in merged_literals(fn.group(1)) if x)

    groups = {
        "rows[] names": rows,
        "rows[] help": helps,
        "tx_* values": txvals,
    }
    drift = []
    report = []
    for title, values in groups.items():
        uniq = sorted(set(values))
        missing = [v for v in uniq if v not in keys and not skipped(v)]
        drift.extend(missing)
        report.append((title, len(uniq), sum(1 for v in uniq if v in keys), missing))

    if a.json:
        print(json.dumps(sorted(set(drift)), ensure_ascii=True, indent=1))
    else:
        for title, total, known, missing in report:
            print("%s: %d unique | translated %d | DRIFT %d" % (title, total, known, len(missing)))
            for v in missing:
                print("   + " + v)
            print()
        print("TOTAL new strings needing translation: %d" % len(set(drift)))
        print("(source of truth for translations: game romfs CNzh MSBT; never machine-translate)")

    if a.check and drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
