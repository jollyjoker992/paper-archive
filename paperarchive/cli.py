"""Command line: intake -> (the agent writes extraction.json) -> validate -> review -> approve -> map -> load."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import DEFAULT_BASE, DOCUMENT_TYPES, EXTRACTION_FORMAT, MAPPING_VERSION, catalog, duplicates, extraction, fhir, load, pdf, store

EXIT_OK, EXIT_ISSUES, EXIT_CONFIG = 0, 1, 2


def template(dtype: str) -> dict:
    row = {"section": "", "name": "", "analyte": None, "value": "", "unit": "", "reference": "", "flag": "",
           "alt_value": "", "alt_reference": "", "page": 1}
    body = {
        "lab-report": {"results": [row]},
        "ecg": {"ecg": {"measurements": {k: "" for k in extraction.ECG["measurements"]}, "rhythm": "",
                        "machine_interpretation": "", "conclusion": "", "device": ""}},
        "visit": {"visit": {k: "" for k in extraction.VISIT_SECTIONS}},
    }.get(dtype, {"study": {"key": None, "procedure": "", "indication": "", "technique": "", "findings": "",
                            "measurements": [row], "impression": "", "recommendation": ""}})
    return {"format": EXTRACTION_FORMAT,
            "document": {"type": dtype, "language": "vi", "title": "", "document_number": "", "pages": 1},
            "patient": {"name": "", "birth_date": None, "birth_year": None, "age": None, "sex": None,
                        "patient_number": ""},
            "facility": {"name": "", "department": "", "address": ""},
            "staff": {"ordered_by": "", "performed_by": "", "signed_by": ""},
            "dates": {"collected": "", "received": "", "performed": "", "reported": ""},
            "extracted_by": {"agent": "", "model": ""},
            "vitals": {k: "" for k in extraction.VITALS},
            **body, "uncertain": [], "notes": ""}


def _checked(root: Path, doc: store.Document):
    """Every rule, including the comparison with the other documents of the archive."""
    people = store.load_people(root)
    issues, parsed = extraction.validate(doc.extraction(), people.get(doc.meta["person"]))
    return issues + duplicates.Index(root, people).issues(doc, parsed), parsed


def cmd_people(args, root):
    for person in store.load_people(root).values():
        print(f"{person['id']}\t{person['name']}\t{person.get('birth_date', '')}\t{person.get('sex', '')}")
    return EXIT_OK


def cmd_intake(args, root):
    doc = store.intake(root, args.person, [Path(f).expanduser() for f in args.files])
    out = {"id": doc.id, "folder": str(doc.path), "pdf": str(doc.path / "document.pdf"),
           "pages": doc.meta["pdf"]["pages"], "originals": [str(doc.path / o["file"]) for o in doc.meta["originals"]],
           "write_extraction_to": str(doc.extraction_path)}
    print(json.dumps(out, indent=1))
    return EXIT_OK


def cmd_template(args, root):
    print(json.dumps(template(args.type), indent=1, ensure_ascii=False))
    return EXIT_OK


def cmd_validate(args, root):
    doc = store.document(root, args.doc)
    issues, _ = _checked(root, doc)
    counts = extraction.summary(issues)
    if args.json:
        print(json.dumps({"id": doc.id, "state": doc.state(), **counts, "issues": issues}, indent=1, ensure_ascii=False))
    else:
        for i in issues:
            print(f"{i['level']:7} {i['code']:22} {i['path']}: {i['message']}")
        print(f"{doc.id}: {counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} note(s)")
    return EXIT_ISSUES if counts["error"] else EXIT_OK


def _rows_table(rows: list[dict]) -> list[str]:
    out = ["", "| # | Printed name | Value | Unit | Reference (printed) | Range used | Flag | Coded as |",
           "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        coded = f"{row['code']['loinc']} {row['code']['display']}" if row["code"] else "(printed name only)"
        ref = row["reference"] or {}
        used = " to ".join(str(ref[k]) if k in ref else "" for k in ("low", "high")) if ("low" in ref or "high" in ref) else ""
        value = row["value_text"] + (f" [also {row['alt_value']} {row['alt_reference'] or ''}]" if row["alt_value"] else "")
        out.append(f"| {row['index']} | {row['name']} | {value} | {row['unit_text'] or ''} | "
                   f"{row['reference_text'] or ''} | {used} | {row['flag'] or ''} | {coded} |")
    return out


def cmd_review(args, root):
    """The sheet the owner checks against the paper before approving."""
    doc = store.document(root, args.doc)
    issues, parsed = _checked(root, doc)
    x = parsed["doc"]
    out = [f"# Review {doc.id} ({doc.state()})", "",
           f"Scan: {doc.path / 'document.pdf'}", f"Person: {doc.meta['person']}", ""]
    for section in ("document", "patient", "facility", "staff", "dates"):
        for key, value in (x.get(section) or {}).items():
            out.append(f"- {section}.{key}: {value}")
    for key, value in (x.get("vitals") or {}).items():
        out.append(f"- vitals.{key}: {value}" + ("" if key in parsed["vitals"] else "  (NOT loaded)"))
    if parsed["type"] == "lab-report":
        out += _rows_table(parsed["results"])
    elif parsed["type"] == "ecg" and "ecg" in x:
        spec = catalog.codes()["ecg"]
        out += [""] + [f"- {k}: {v['text']} {spec[k]['unit']}" for k, v in parsed["ecg"].items()]
        out += [f"- {k}: {x['ecg'][k]}" for k in ("rhythm", "machine_interpretation", "conclusion", "device") if k in x["ecg"]]
    elif parsed["type"] == "visit" and "visit" in x:
        out += [""] + [f"- {k}: {v}" for k, v in x["visit"].items()]
    elif "study" in x:
        study = parsed["study"]
        out += ["", f"- coded as: {study['loinc'] + ' ' + study['display'] if study else '(generic study, printed name only)'}"]
        out += [f"- {k}: {v}" for k, v in x["study"].items() if k != "measurements"]
        if parsed["measurements"]:
            out += _rows_table(parsed["measurements"])
    if "notes" in x:
        out += ["", f"Notes: {x['notes']}"]
    out += ["", "## To settle before approval", ""]
    out += [f"- {i['level'].upper()} {i['code']} `{i['path']}`: {i['message']}" for i in issues] or ["- nothing"]
    counts = extraction.summary(issues)
    out += ["", f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} note(s)"]
    print("\n".join(out))
    return EXIT_ISSUES if counts["error"] else EXIT_OK


def cmd_approve(args, root):
    doc = store.document(root, args.doc)
    issues, _ = _checked(root, doc)
    review = store.approve(doc, args.by, issues, args.accept_warnings)
    print(f"{doc.id}: approved by {review['approved_by']} ({len(review['accepted_warnings'])} warning(s) accepted)")
    return EXIT_OK


def cmd_remove(args, root):
    doc = store.document(root, args.doc)
    if args.doc != doc.id:
        raise store.ArchiveError(f"give the full id to remove a document ({doc.id})")
    state = doc.state()
    store.remove(doc)
    print(f"{doc.id}: removed ({doc.meta['person']}, was {state})")
    if state == "mapped":
        print("run map and load to take it off the FHIR server")
    return EXIT_OK


def cmd_map(args, root):
    only = store.document(root, args.doc).id if args.doc else None
    result = fhir.map_all(root, store.env("PAPER_FHIR_BASE", DEFAULT_BASE), only, args.force)
    print(json.dumps(result, indent=1))
    return EXIT_ISSUES if result["failed"] else EXIT_OK


def cmd_load(args, root):
    url, project = store.env("PAPER_FHIR_URL"), store.env("PAPER_FHIR_PROJECT_ID")
    cid, secret = store.env("PAPER_FHIR_CLIENT_ID"), store.env("PAPER_FHIR_CLIENT_SECRET")
    if not (url and project and cid and secret):
        raise store.ArchiveError("PAPER_FHIR_URL, PAPER_FHIR_PROJECT_ID, PAPER_FHIR_CLIENT_ID and "
                                 "PAPER_FHIR_CLIENT_SECRET must be set (.env)")
    result = load.load_all(root, load.FhirClient(url, cid, secret), project, args.force)
    print(json.dumps(result, indent=1))
    return EXIT_ISSUES if result["failed"] else EXIT_OK


def cmd_status(args, root):
    manifest_path = root / "fhir" / "load-manifest.json"
    loaded = json.loads(manifest_path.read_text())["documents"] if manifest_path.is_file() else {}
    rows = []
    for doc in store.documents(root):
        state = doc.state()
        fhir_file = doc.path / "fhir.ndjson"
        if state == "mapped" and loaded.get(doc.id, {}).get("status") == "ok" \
                and loaded[doc.id]["sha256"] == store.sha256(fhir_file):
            state = "loaded"
        dtype = None
        if doc.extraction_path.is_file():
            try:
                dtype = doc.extraction().get("document", {}).get("type")
            except store.ArchiveError:
                dtype = "invalid-json"
        rows.append({"id": doc.id, "person": doc.meta["person"], "type": dtype, "state": state,
                     "intake_at": doc.meta["intake_at"], "pages": doc.meta["pdf"]["pages"]})
    if args.json:
        print(json.dumps({"data_dir": str(root), "mapping_version": MAPPING_VERSION,
                          "catalog_version": catalog.catalog_version(), "documents": rows}, indent=1))
    else:
        for r in rows:
            print(f"{r['id']}  {r['person']:10} {r['type'] or '-':12} {r['state']}")
        print(f"{len(rows)} document(s) in {root}")
    return EXIT_OK


def cmd_catalog(args, root):
    hits = catalog.find(args.text, "study" if args.study else "analyte")
    for entry in hits[:12]:
        if args.study:
            print(f"{entry['key']:20} {entry['loinc']:9} {entry['display']}  [{entry['type']}]")
        else:
            for code in entry["codes"]:
                units = ", ".join(code["units"]) or "negative/positive"
                print(f"{entry['key']:20} {code['loinc']:9} {code['display']}  [{units}]")
        if entry.get("use"):
            print(f"{'':20} use: {entry['use']}")
    if not hits:
        print("no match: transcribe with analyte/study null (see the paper-catalog skill to add it)")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper_archive.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("people", help="list the people in people.toml").set_defaults(fn=cmd_people)
    p = sub.add_parser("intake", help="take scans (page images in order, or one PDF) into the archive")
    p.add_argument("--person", required=True)
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_intake)
    p = sub.add_parser("template", help="print an empty extraction for a document type")
    p.add_argument("type", choices=DOCUMENT_TYPES)
    p.set_defaults(fn=cmd_template)
    p = sub.add_parser("validate", help="check a document's extraction.json against every rule")
    p.add_argument("doc")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("review", help="print the sheet the owner checks against the paper")
    p.add_argument("doc")
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("approve", help="record the owner's approval (only on the owner's word)")
    p.add_argument("doc")
    p.add_argument("--by", required=True, help="the person who compared the sheet with the paper")
    p.add_argument("--accept-warnings", action="store_true")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("remove", help="delete a document, e.g. a duplicate (only on the owner's word)")
    p.add_argument("doc", help="the full document id")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("map", help="approved documents -> FHIR NDJSON")
    p.add_argument("doc", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_map)
    p = sub.add_parser("load", help="load the mapped documents into Medplum")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_load)
    p = sub.add_parser("status", help="every document and its state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("catalog", help="find a catalog key by a printed name")
    p.add_argument("action", choices=["find"])
    p.add_argument("text")
    p.add_argument("--study", action="store_true", help="search imaging studies instead of lab analytes")
    p.set_defaults(fn=cmd_catalog)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        return args.fn(args, store.data_dir())
    except (store.ArchiveError, load.LoadError, pdf.PdfError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
