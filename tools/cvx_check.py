#!/usr/bin/env python3
"""Check every CVX code in the vaccine catalog against the CDC's CVX list.

  python3 tools/cvx_check.py          report codes that are unknown, not a vaccine, or whose display differs
  python3 tools/cvx_check.py --fix    also write CVX's short description as the display

The list is downloaded with a plain GET (nothing is sent). Inactive and Non-US codes are accepted: they are
valid for recording doses given in the past or outside the US. A display that differs is not a formality:
read CVX's full name and decide whether the code still means what the catalog entry says before --fix.
"""
import json
import sys
import urllib.request
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "paperarchive" / "catalog" / "vaccines.json"
CVX_URL = "https://www2a.cdc.gov/vaccines/iis/iisstandards/downloads/cvx.txt"


def parse(text: str) -> dict[str, dict]:
    """cvx.txt: code|short description|full name|notes|status|nonvaccine|last updated (pipe separated)."""
    out = {}
    for line in text.lstrip("﻿").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 6 and parts[0]:
            out[parts[0]] = {"short": parts[1], "full": parts[2], "status": parts[4],
                             "nonvaccine": parts[5].lower() == "true"}
    return out


def fetch() -> dict[str, dict]:
    request = urllib.request.Request(CVX_URL, headers={"User-Agent": "paper-archive-cvx-check"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return parse(response.read().decode("utf-8"))


def dump(data: dict) -> str:
    """One vaccine per line, like analytes.json."""
    head = json.dumps({k: v for k, v in data.items() if k not in ("vaccines", "not_coded")}, ensure_ascii=False, indent=1)[:-2]
    rows = [",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in data[k]) for k in ("vaccines", "not_coded")]
    return f'{head},\n "vaccines": [\n{rows[0]}\n ],\n "not_coded": [\n{rows[1]}\n ]\n}}\n'


def main() -> int:
    fix, bad = "--fix" in sys.argv, 0
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    cvx = fetch()
    for entry in data["vaccines"]:
        official = cvx.get(entry["cvx"])
        if official is None:
            bad += 1
            print(f"BAD   {entry['key']}: {entry['cvx']} is not a CVX code")
        elif official["nonvaccine"]:
            bad += 1
            print(f"BAD   {entry['key']}: {entry['cvx']} {official['short']!r} is not a vaccine")
        elif entry.get("display") != official["short"]:
            print(f"{'FIXED' if fix else 'DIFF '} {entry['key']}: {entry['cvx']} ({official['status']})\n"
                  f"        catalog: {entry.get('display')!r}\n        CVX:     {official['short']!r}\n"
                  f"        full:    {official['full']!r}")
            if fix:
                entry["display"] = official["short"]
            else:
                bad += 1
    if fix:
        CATALOG.write_text(dump(data), encoding="utf-8")
    print(f"{len(data['vaccines'])} codes checked against {len(cvx)} CVX codes, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
