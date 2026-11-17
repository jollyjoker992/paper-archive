#!/usr/bin/env python3
"""Map the made-up documents in tests/fixtures to FHIR NDJSON, for the Medplum validator.

  python3 tools/fhir_fixtures.py <empty output dir>
  cd tools/fhir-validate && npm ci && node validate.mjs <output dir>

Exit code 1 if a fixture has validation errors or fails to map.
"""
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from paperarchive import DEFAULT_BASE, extraction, fhir, store  # noqa: E402

# The smallest file the PDF builder accepts as a JPEG page: the scan of a made-up document is a stand-in.
STAND_IN_JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x40\x00\x30\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    fixtures = REPO / "tests" / "fixtures"
    shutil.copy(fixtures / "people.toml", root / "people.toml")
    person = store.load_people(root)["example"]
    bad = 0
    for path in sorted(fixtures.glob("*.json")):
        scan = root / f"{path.stem}.jpg"
        scan.write_bytes(STAND_IN_JPEG + path.stem.encode())
        doc = store.intake(root, person["id"], [scan])
        shutil.copy(path, doc.extraction_path)
        issues, _ = extraction.validate(doc.extraction(), person)
        errors = [i for i in issues if i["level"] == "error"]
        for i in errors:
            print(f"{path.name}: {i['code']} {i['path']}: {i['message']}")
        if errors:
            bad += 1
            continue
        store.approve(doc, "fixture", issues, accept_warnings=True)
    result = fhir.map_all(root, DEFAULT_BASE)
    print(f"{len(result['mapped'])} document(s) mapped, {len(result['failed']) + bad} failed")
    return 1 if result["failed"] or bad else 0


if __name__ == "__main__":
    sys.exit(main())
