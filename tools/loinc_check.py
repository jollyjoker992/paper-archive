#!/usr/bin/env python3
"""Check every LOINC code in the catalog against the NLM LOINC search API (clinicaltables.nlm.nih.gov).

  python3 tools/loinc_check.py          report codes that are unknown, deprecated or whose display differs
  python3 tools/loinc_check.py --fix    also write the API's long common name as the display

Only LOINC numbers are sent. A display that differs is not a formality: read the API's name and decide
whether the code still means what the catalog entry says before running --fix.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "paperarchive" / "catalog"
API = "https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search"


def lookup(code: str) -> str | None:
    query = urllib.parse.urlencode({"terms": code, "sf": "LOINC_NUM", "df": "LOINC_NUM,LONG_COMMON_NAME"})
    with urllib.request.urlopen(f"{API}?{query}", timeout=30) as response:
        rows = json.load(response)[3]
    return next((name for number, name in rows if number == code), None)


def coded(node, path=""):
    """Every dict carrying a 'loinc' key, with where it is."""
    if isinstance(node, dict):
        if "loinc" in node:
            yield path, node
        for key, value in node.items():
            yield from coded(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from coded(value, f"{path}[{value.get('key', i) if isinstance(value, dict) else i}]")


def dump(name: str, data: dict) -> str:
    """analytes.json keeps one analyte per line; codes.json is plainly indented."""
    if name != "analytes.json":
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    head = json.dumps({k: v for k, v in data.items() if k not in ("analytes", "not_coded")}, ensure_ascii=False, indent=1)[:-2]
    rows = [",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in data[k]) for k in ("analytes", "not_coded")]
    return f'{head},\n "analytes": [\n{rows[0]}\n ],\n "not_coded": [\n{rows[1]}\n ]\n}}\n'


def main() -> int:
    fix, bad, seen = "--fix" in sys.argv, 0, {}
    for name in ("analytes.json", "codes.json"):
        path = CATALOG / name
        data = json.loads(path.read_text(encoding="utf-8"))
        for where, entry in coded(data):
            code = entry["loinc"]
            if code not in seen:
                seen[code] = lookup(code)
                time.sleep(0.05)
            official = seen[code]
            if official is None or official.startswith("Deprecated"):
                bad += 1
                print(f"BAD   {name} {where}: {code} {official or 'is not a LOINC code'}")
            elif entry.get("display") != official:
                print(f"{'FIXED' if fix else 'DIFF '} {name} {where}: {code}\n        catalog: {entry.get('display')!r}\n        LOINC:   {official!r}")
                if fix:
                    entry["display"] = official
                else:
                    bad += 1
        if fix:
            path.write_text(dump(name, data), encoding="utf-8")
    print(f"{len(seen)} codes checked, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
