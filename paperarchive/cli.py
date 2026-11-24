"""Command line: intake -> (the agent writes extraction.json) -> validate -> review -> approve -> map -> load."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import (DEFAULT_BASE, DOCUMENT_TYPES, EXTRACTION_FORMAT, MAPPING_VERSION, backup, catalog, duplicates, extraction,
               fhir, load, pdf, store)

EXIT_OK, EXIT_ISSUES, EXIT_CONFIG = 0, 1, 2


def template(dtype: str) -> dict:
    row = {"section": "", "name": "", "analyte": None, "value": "", "unit": "", "reference": "", "flag": "",
           "alt_value": "", "alt_reference": "", "page": 1}
    body = {
        "lab-report": {"results": [row]},
        "ecg": {"ecg": {"measurements": {k: "" for k in extraction.ECG["measurements"]}, "leads": None, "rhythm": "",
                        "machine_interpretation": "", "conclusion": "", "device": ""}},
        "visit": {"visit": {k: "" for k in extraction.VISIT_SECTIONS}},
        "procedure": {"procedure": {k: "" for k in extraction.PROCEDURE}},
        "vaccination": {"vaccination": {"screening": "", "dose_unit": "", "doses": [
            {**{k: "" for k in extraction.DOSE}, "key": None, "date": None, "expiry": None, "next_due": None,
             "given": None, "page": 1}],
            "due": [{**{k: "" for k in extraction.DUE}, "key": None, "date": None, "page": 1}]}},
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


def _compact_rows(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        value = " ".join(filter(None, [row["value_text"], row["unit_text"]]))
        extra = [f"({row['reference_text']})" if row["reference_text"] else "", row["flag"] or ""]
        if row["alt_value"]:
            extra.append(f"[also {row['alt_value']}]")
        coded = row["code"]["loinc"] if row["code"] else "name only"
        out.append(f"#{row['index']} {row['name']}: {value} {' '.join(e for e in extra if e)} -> {coded}".replace("  ", " "))
    return out


def _dose_lines(doses: list[dict], due: list[dict] = ()) -> list[str]:
    """One line per dose and per appointment: what is printed, and what it becomes."""
    out = []
    for d in doses:
        printed = ", ".join(f"{k} {d[k]}" for k in ("disease", "dose_number", "lot", "expiry", "manufacturer", "country",
                                                     "route", "site", "dose_quantity", "given_by", "facility",
                                                     "next_due", "note")
                            if d[k])
        coded = f"CVX {d['code']['cvx']} {d['code']['display']}" if d["code"] else "printed name only"
        becomes = {"yes": "Immunization completed", "no": "Immunization not-done",
                   "unknown": "order only (MedicationRequest)"}.get(d["given"], "given? NOT ANSWERED")
        out.append(f"#{d['index']} {d['date'] or '(no date)'} {d['vaccine']}" + (f" ({printed})" if printed else "")
                   + f" -> {coded}; {becomes}" + ("; next dose due" if d["next_due"] else ""))
    for d in due:
        printed = ", ".join(f"{k} {d[k]}" for k in ("disease", "dose_number", "note") if d[k])
        coded = f"CVX {d['code']['cvx']} {d['code']['display']}" if d["code"] else "printed name only"
        out.append(f"due#{d['index']} {d['date']} {d['vaccine']}" + (f" ({printed})" if printed else "")
                   + f" -> {coded}; appointment (ImmunizationRecommendation)")
    return out


def review_compact(doc: store.Document, issues: list[dict], parsed: dict) -> str:
    """The review sheet for a chat: one line per value, no tables."""
    x = parsed["doc"]
    head = [x.get("document", {}).get("type"), (x.get("facility") or {}).get("name"),
            " ".join(f"{k} {v}" for k, v in (x.get("dates") or {}).items())]
    out = [f"Review {doc.id} ({doc.state()}) - {doc.meta['person']}", " | ".join(h for h in head if h)]
    patient = x.get("patient") or {}
    if patient:
        out.append("Patient: " + ", ".join(f"{k} {v}" for k, v in patient.items()))
    staff = x.get("staff") or {}
    if staff:
        out.append("Staff: " + ", ".join(f"{k} {v}" for k, v in staff.items()))
    if x.get("vitals"):
        out.append("Vitals: " + ", ".join(f"{k} {v}" + ("" if k in parsed["vitals"] else " (not loaded)")
                                          for k, v in x["vitals"].items()))
    out.append("")
    if parsed["type"] == "lab-report":
        out += _compact_rows(parsed["results"])
    elif parsed["type"] == "ecg" and "ecg" in x:
        spec = catalog.codes()["ecg"]
        out += [f"{k}: {v['text']} {spec[k]['unit']}" for k, v in parsed["ecg"].items()]
        out += [f"{k}: {x['ecg'][k]}" for k in ("leads", "rhythm", "machine_interpretation", "conclusion") if k in x["ecg"]]
    elif parsed["type"] in ("visit", "procedure") and parsed["type"] in x:
        out += [f"{k}: {v}" for k, v in x[parsed["type"]].items()]
    elif parsed["type"] == "vaccination" and "vaccination" in x:
        if "screening" in x["vaccination"]:
            out.append(f"screening: {x['vaccination']['screening']}")
        out += _dose_lines(parsed["doses"], parsed["due"])
    elif "study" in x:
        study = parsed["study"]
        out.append(f"Study: {study['loinc'] + ' ' + study['display'] if study else 'generic, printed name only'}")
        out += [f"{k}: {v}" for k, v in x["study"].items() if k not in ("key", "measurements")]
        out += _compact_rows(parsed["measurements"])
    counts = extraction.summary(issues)
    out += ["", f"{counts['error']} error(s), {counts['warning']} warning(s)"]
    out += [f"! {i['code']} {i['path']}: {i['message']}" for i in issues if i["level"] != "info"]
    return "\n".join(out)


def cmd_review(args, root):
    """The sheet the owner checks against the paper before approving."""
    doc = store.document(root, args.doc)
    issues, parsed = _checked(root, doc)
    if args.compact:
        print(review_compact(doc, issues, parsed))
        return EXIT_ISSUES if extraction.summary(issues)["error"] else EXIT_OK
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
        out += [f"- {k}: {x['ecg'][k]}" for k in ("leads", "rhythm", "machine_interpretation", "conclusion", "device")
                if k in x["ecg"]]
    elif parsed["type"] in ("visit", "procedure") and parsed["type"] in x:
        out += [""] + [f"- {k}: {v}" for k, v in x[parsed["type"]].items()]
    elif parsed["type"] == "vaccination" and "vaccination" in x:
        if "screening" in x["vaccination"]:
            out += ["", f"- screening: {x['vaccination']['screening']}"]
        out += [""] + [f"- {line}" for line in _dose_lines(parsed["doses"], parsed["due"])]
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


def cmd_rebuild_pdf(args, root):
    docs = store.documents(root) if args.doc == "all" else [store.document(root, args.doc)]
    for doc in docs:
        changed = store.rebuild_pdf(doc)
        print(f"{doc.id}: {'rebuilt' if changed else 'unchanged'}")
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


def _one_target(env: dict, name: str | None) -> dict:
    targets = backup.targets_from_env(env, name)
    if not targets:
        raise store.ArchiveError(f"backup target {name} is not configured (docs/backup.md)" if name else
                                 "no backup target is configured: set the PAPER_AWS_*, PAPER_R2_*, PAPER_GDRIVE_* "
                                 "or PAPER_BACKUP_RCLONE_REMOTE settings (docs/backup.md)")
    return targets


def cmd_backup(args, root):
    env = backup.settings()
    if args.action == "new-key":
        print(f"PAPER_BACKUP_KEY={backup.new_key()}")
        print("Put this line in .env and keep a copy outside this computer (password manager, paper): "
              "without the key the encrypted backup cannot be read.", file=sys.stderr)
        return EXIT_OK
    if args.action == "status":
        result = backup.status(root, env)
        if args.json:
            print(json.dumps(result, indent=1))
            return EXIT_OK
        if result["error"]:
            print(f"backup: {result['error']}")
        elif not result["targets"]:
            print("backup: no target configured")
        for name, t in result["targets"].items():
            line = f"backup {name} ({result['mode']}): {t['backed_up']}/{t['documents']} document(s), "
            line += "never run" if t.get("never_run") else \
                f"{'up to date' if t['up_to_date'] else 'not up to date'}, checked {t['checked_at']}"
            print(line + (f"; ERROR {t['error']}" if t.get("error") else ""))
        return EXIT_OK
    targets, mode = _one_target(env, args.target), backup.mode_from_env(env)
    report = backup.backup(root, targets, mode, backup.Rclone(root, env))
    print(json.dumps(report, indent=1))
    return EXIT_ISSUES if backup.failed(report) else EXIT_OK


def cmd_restore(args, root):
    env = backup.settings()
    target, mode = _one_target(env, args.source)[args.source], backup.mode_from_env(env)
    out = Path(args.out).expanduser()
    report = backup.restore(target, mode, out, backup.Rclone(root, env))
    print(json.dumps(report, indent=1))
    print(f"restored {len(report['documents'])} document(s) into {out}: medical records, not encrypted. "
          f"Use it with PAPER_ARCHIVE_DIR={out} and run map.", file=sys.stderr)
    return EXIT_ISSUES if report["errors"] else EXIT_OK


def cmd_catalog(args, root):
    hits = catalog.find(args.text, "vaccine" if args.vaccine else "study" if args.study else "analyte")
    for entry in hits[:12]:
        if args.vaccine:
            print(f"{entry['key']:20} {'CVX ' + entry['cvx'] if 'cvx' in entry else 'not coded':9} "
                  f"{entry.get('display') or entry['reason']}  [{', '.join(entry['names'][:4])}]")
        elif args.study:
            print(f"{entry['key']:20} {entry['loinc']:9} {entry['display']}  [{entry['type']}]")
        else:
            for code in entry["codes"]:
                units = ", ".join(code["units"]) or "negative/positive"
                print(f"{entry['key']:20} {code['loinc']:9} {code['display']}  [{units}]")
        if entry.get("use"):
            print(f"{'':20} use: {entry['use']}")
    if not hits:
        print("no match: transcribe with analyte/study/key null (see the paper-catalog skill to add it)")
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
    p.add_argument("--compact", action="store_true", help="one line per value, for a chat")
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("approve", help="record the owner's approval (only on the owner's word)")
    p.add_argument("doc")
    p.add_argument("--by", required=True, help="the person who compared the sheet with the paper")
    p.add_argument("--accept-warnings", action="store_true")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("rebuild-pdf", help="build document.pdf again from the original scans")
    p.add_argument("doc", help="a document id, or all")
    p.set_defaults(fn=cmd_rebuild_pdf)
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
    p = sub.add_parser("backup", help="copy the data directory to the configured cloud targets (docs/backup.md)")
    p.add_argument("action", nargs="?", choices=["run", "status", "new-key"], default="run")
    p.add_argument("--target", choices=backup.TARGET_NAMES, help="only this target")
    p.add_argument("--json", action="store_true", help="status as JSON")
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("restore", help="write a target's backup into a new data directory")
    p.add_argument("--from", dest="source", required=True, choices=backup.TARGET_NAMES)
    p.add_argument("--out", required=True, help="a folder that does not exist yet")
    p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("status", help="every document and its state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("catalog", help="find a catalog key by a printed name")
    p.add_argument("action", choices=["find"])
    p.add_argument("text")
    p.add_argument("--study", action="store_true", help="search imaging studies instead of lab analytes")
    p.add_argument("--vaccine", action="store_true", help="search vaccines instead of lab analytes")
    p.set_defaults(fn=cmd_catalog)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        return args.fn(args, store.data_dir())
    except backup.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ISSUES
    except (store.ArchiveError, load.LoadError, pdf.PdfError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
