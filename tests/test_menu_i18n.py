"""The menu's optional display language: the generated translation header stays in sync with its JSON table,
the table is well-formed, and tr() has the exact semantics the menu relies on.

Why it matters: the menu's English strings double as ini keys and strcmp subjects (vk_menu.cpp's ini_key(),
is_motion_row(), cheat_value() == "On"). Translation is therefore done at draw time only -- tr() returns the
original pointer in English or when a string has no translation, so no comparison or key ever sees Chinese.
This test guards that contract without compiling the renderer (the header is self-contained C++).

Checks:
  * scripts/gen_menu_i18n.py --check: menu_i18n.h is byte-for-byte what the JSON generates.
  * the JSON table: unique non-empty keys, no identity entries, all-ASCII keys, no brand-name key ("Switch").
  * tr() compiled and run from the real header: language=0 returns the input pointer; language=1 returns the
    Chinese text for a known key, the original pointer for an unknown key, and is safe on nullptr / "".
  * the main menu's key strings (tabs, prompts, the Language row) are all present in the table.

Game-free, standard library only, and needs no C++ toolchain in the common case (the tr() runtime check is
skipped when no C++ compiler is available; run_all.py --strict will require one).

python -I -B tests/test_menu_i18n.py [--cxx g++]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts" / "gen_menu_i18n.py"
DATA = ROOT / "gpu" / "vk" / "menu_zh.json"
HEADER = ROOT / "gpu" / "vk" / "menu_i18n.h"
MENU = ROOT / "gpu" / "vk" / "vk_menu.cpp"
ITEMS_H = ROOT / "gpu" / "vk" / "oot_cheat_items.h"
PYTHON = sys.executable

# The Inventory/Teleport subpages draw every item / destination / section name through tr(). Each English
# name must therefore be a table key, or that cell stays English. Read them from the source that owns them.
FMT = re.compile(r"%(?:[-+ 0#]*)(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L|I64)?[diouxXeEfgGaAcspn%]")


def placeholders(s):
    return [t for t in FMT.findall(s) if t != "%"]


def subpage_keys():
    """(required subpage keys, format-string keys) parsed from the source, no hard-coded list to rot."""
    h = ITEMS_H.read_text(encoding="utf-8")
    items = re.findall(r'OOT_ITEM\("([^"]+)"', h)
    sections = re.findall(r'OOT_SECTION\("([^"]+)"', h)
    vk = MENU.read_text(encoding="utf-8")
    tp = vk[vk.index("teleport_names[] = {"):vk.index("};", vk.index("teleport_names[] = {"))]
    destinations = re.findall(r'"([^"]+)"', tp)
    # the snprintf format strings the two subpages call tr() on (placeholders must survive translation)
    formats = ["Quantity %d", "Page %d of %d", "%d-%d of %d", "%d of %d", "%d items"]
    other = ["All", "Overworld", "Dungeons", "adult only", "Travel", "Move", "Amount",
             "Section", "Back", "Relock", "Unlock", "Edit amount", "Done", "Unavailable", "Unlocked", "Locked"]
    required = set(items + sections + destinations + formats + other)
    return required, set(formats), len(items), len(sections), len(destinations)

# Strings the main menu must translate. "Switch" (a controller-layout preset / brand name) must NOT be a key:
# tr() matches by full string, so translating it would also change tx_prompts' "Switch" preset.
MUST_HAVE = ["Graphics", "Audio", "Gameplay", "Controls", "Debug", "Language",
             "Menu language", "Choose", "Change", "Confirm", "Back", "Close", "Open",
             "On", "Off", "Restart Needed", "Later", "Restart Now", "Section",
             "The language the menu is drawn in. The game's own language (its dialogue and signs) is unchanged. Applies at once."]
MUST_NOT_HAVE = ["Switch"]

TR_PROBE = r"""
#include "menu_i18n.h"
#include <cstdio>
using namespace mkwvk::menu_i18n;
int main() {
    const char* known = "Language";
    int ok = 1;
    language = 0;
    if (tr(known) != known) { std::puts("english: not identity"); ok = 0; }
    language = 1;
    if (tr(known) == known) { std::puts("chinese: known key not translated"); ok = 0; }
    const char* unknown = "ZZ not in the table ZZ";
    if (tr(unknown) != unknown) { std::puts("chinese: unknown key not identity"); ok = 0; }
    if (tr(nullptr) != nullptr) { std::puts("chinese: nullptr not safe"); ok = 0; }
    if (tr("")[0] != '\0') { std::puts("chinese: empty not safe"); ok = 0; }
    if (ok) std::puts("OK");
    return ok ? 0 : 1;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    a = ap.parse_args()

    # 1) generated header is in sync with the JSON (the same check CI runs)
    r = subprocess.run([PYTHON, "-I", "-B", str(GEN), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("FAIL: menu_i18n.h is out of date:", (r.stdout + r.stderr).strip())
        return 1
    print("OK  generator --check:", r.stdout.strip())

    # 2) table shape
    data = json.loads(DATA.read_text(encoding="utf-8"))
    entries = data["entries"]
    keys = [e["en"] for e in entries]
    seen = set()
    for e in entries:
        if set(e) != {"en", "zh"}:
            print("FAIL: entry without exactly en/zh:", e)
            return 1
        en, zh = e["en"], e["zh"]
        if not en or not zh:
            print("FAIL: empty en or zh:", e)
            return 1
        if en == zh:
            print("FAIL: identity entry (language names should be left untranslated instead):", e)
            return 1
        if any(ord(c) >= 128 for c in en):
            print("FAIL: non-ASCII key:", repr(en))
            return 1
        if en in seen:
            print("FAIL: duplicate key:", repr(en))
            return 1
        seen.add(en)
    print(f"OK  table shape: {len(entries)} unique, non-empty, ASCII-keyed entries")

    # 3) key coverage / exclusions
    missing = [k for k in MUST_HAVE if k not in seen]
    if missing:
        print("FAIL: menu strings missing from the table:", missing)
        return 1
    forbidden = [k for k in MUST_NOT_HAVE if k in seen]
    if forbidden:
        print("FAIL: brand-name / preset keys must not be translated:", forbidden)
        return 1
    print(f"OK  key coverage: {len(MUST_HAVE)} required present, {len(MUST_NOT_HAVE)} excluded")

    # 3b) the Inventory/Teleport subpages: every item/destination/section name must be a key, and every
    # printf format string must keep its placeholders in the same order after translation (a mismatch would
    # make snprintf read the wrong varargs -- undefined behaviour).
    required, formats, n_items, n_sections, n_dests = subpage_keys()
    sub_missing = sorted(required - seen)
    if sub_missing:
        print(f"FAIL: subpage strings missing from the table ({len(sub_missing)}):", sub_missing[:12])
        return 1
    for e in entries:
        if placeholders(e["en"]):
            if placeholders(e["en"]) != placeholders(e["zh"]):
                print("FAIL: printf placeholder mismatch:", repr(e["en"]), "->", repr(e["zh"]),
                      placeholders(e["en"]), "vs", placeholders(e["zh"]))
                return 1
    print(f"OK  subpage coverage: {n_items} items + {n_dests} destinations + {n_sections} sections + "
          f"{len(formats)} formats, all {len(required)} keys present")
    print(f"OK  printf placeholders: {sum(1 for e in entries if placeholders(e['en']))} format strings en/zh consistent")

    # 4) the draw-time contract: compile the real header and exercise tr()
    cxx = shutil.which(a.cxx)
    if not cxx:
        print("SKIP tr() runtime check: no C++ compiler found (--strict makes this a failure)")
        return 0
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "probe.cpp"
        exe = Path(td) / "probe"
        src.write_text(TR_PROBE, encoding="utf-8")
        cmd = [cxx, "-std=c++17", "-I", str(HEADER.parent), str(src), "-o", str(exe)]
        b = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if b.returncode != 0:
            print("FAIL: tr() probe did not compile:", (b.stdout + b.stderr).strip())
            return 1
        run = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8")
        if run.returncode != 0:
            print("FAIL: tr() probe:", (run.stdout + run.stderr).strip())
            return 1
    print("OK  tr() runtime: English identity / Chinese hit / unknown identity / nullptr / empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
