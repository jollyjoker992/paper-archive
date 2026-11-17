"""Approved transcription -> FHIR R4 resources. docs/fhir-mapping.md is the specification.

The mapper adds nothing the paper does not say: values, units, ranges and flags are the printed ones,
codes come only from the catalog, and a test the catalog does not know keeps its printed name alone.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import MAPPING_VERSION, catalog, duplicates, extraction, store

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
SERVICE_SECTION = "http://terminology.hl7.org/CodeSystem/v2-0074"
SECTION = {"lab-report": "LAB", "ecg": "EC", "ultrasound": "RUS", "xray": "RX"}      # endoscopy: no v2-0074 code
TYPE_TEXT = {"lab-report": "Laboratory report", "ecg": "Electrocardiogram", "ultrasound": "Ultrasound report",
             "xray": "X-ray report", "endoscopy": "Endoscopy report", "visit": "Outpatient visit note"}
OBS_KIND = {"lab-report": "laboratory", "ecg": "procedure", "ultrasound": "imaging", "xray": "imaging",
            "endoscopy": "procedure"}
VISIT_TITLES = {"reason": "Reason for visit", "medications": "Current medications", "family_history": "Family history",
                "past_history": "Past history", "allergies": "Allergies", "examination": "Clinical examination",
                "tests": "Tests", "diagnosis": "Diagnosis", "advice": "Advice"}


def loinc(code: dict, text: str | None = None) -> dict:
    concept = {"coding": [{"system": LOINC, "code": code["loinc"], "display": code["display"]}]}
    if text:
        concept["text"] = text
    return concept


def number(value: float, decimals: int):
    value = round(value, decimals)
    return int(value) if decimals == 0 else value


def plain(value: float):
    return int(value) if value == int(value) else value


def ident_of(resource: dict) -> dict:
    """A resource's first identifier (Composition has one identifier, not a list)."""
    ident = resource["identifier"]
    return ident[0] if isinstance(ident, list) else ident


class DocumentMapper:
    def __init__(self, doc: store.Document, person: dict, parsed: dict, base: str):
        self.doc, self.person, self.parsed, self.base = doc, person, parsed, base.rstrip("/")
        self.x = parsed["doc"]
        self.type = parsed["type"]
        self.zone = ZoneInfo(person["timezone"])
        self.observations: list[dict] = []

    # -- helpers ---------------------------------------------------------------

    def ident(self, kind: str, value: str) -> list[dict]:
        return [{"system": f"{self.base}/identifier/{kind}", "value": value}]

    def ref(self, rtype: str, kind: str, value: str) -> dict:
        return {"reference": f"{rtype}?identifier={self.base}/identifier/{kind}|{value}"}

    def when(self, text: str) -> str:
        """Printed local time -> FHIR dateTime with the person's offset; a bare date stays a date."""
        value = extraction.parse_when(text)
        if isinstance(value, dt.datetime):
            return value.replace(tzinfo=self.zone).isoformat(timespec="seconds")
        return text

    @property
    def effective(self) -> str:
        return self.when(self.parsed["dates"][self.parsed["dates"]["effective"]])

    @property
    def issued(self) -> str | None:
        reported = self.parsed["dates"].get("reported")
        return self.when(reported) if reported and "T" in reported else None

    @property
    def org_id(self) -> str:
        return catalog.slug(self.x["facility"]["name"])

    @property
    def patient(self) -> dict:
        return self.ref("Patient", "paper-user", self.person["id"])

    @property
    def org(self) -> dict:
        return self.ref("Organization", "paper-organization", self.org_id)

    def organization(self) -> dict:
        facility = self.x["facility"]
        org = {"resourceType": "Organization", "identifier": self.ident("paper-organization", self.org_id),
               "name": facility["name"]}
        if "address" in facility:
            org["address"] = [{"text": facility["address"]}]
        return org

    def observation(self, slug: str, code: dict, category: str | None = None, **fields) -> dict:
        obs = {"resourceType": "Observation", "identifier": self.ident("paper-observation", f"{self.doc.id}/{slug}"),
               "status": "final",
               "category": [{"coding": [{"system": OBS_CATEGORY, "code": category or OBS_KIND[self.type]}]}],
               "code": code, "subject": self.patient, "effectiveDateTime": self.effective}
        if self.issued:
            obs["issued"] = self.issued
        obs["performer"] = [self.org]
        obs.update({k: v for k, v in fields.items() if v is not None})
        obs["derivedFrom"] = [self.ref("DocumentReference", "paper-document", self.doc.id)]
        self.observations.append(obs)
        return obs

    # -- rows: lab results and study measurements --------------------------------

    def row(self, row: dict, category: str | None = None) -> None:
        code = loinc(row["code"], row["name"]) if row["code"] else {"text": row["name"]}
        fields: dict = {}
        if row["kind"] == "quantity":
            quantity = {"value": number(row["number"], row["decimals"])}
            if row["comparator"]:
                quantity["comparator"] = row["comparator"]
            if row["unit_text"]:
                quantity["unit"] = row["unit_text"]
            if row["ucum"]:
                quantity.update(system=UCUM, code=row["ucum"])
            fields["valueQuantity"] = quantity
        else:
            fields["valueString"] = row["value_text"]
        if row["flag"]:
            fields["interpretation"] = [{"coding": [{"system": INTERPRETATION, "code": row["flag"],
                                                     "display": extraction.INTERPRETATION[row["flag"]]}]}]
        if row["reference_text"]:
            rng: dict = {}
            for side in ("low", "high"):
                if row["reference"] and side in row["reference"]:
                    rng[side] = {"value": plain(row["reference"][side])}
                    if row["unit_text"]:
                        rng[side]["unit"] = row["unit_text"]
                    if row["ucum"]:
                        rng[side].update(system=UCUM, code=row["ucum"])
            rng["text"] = row["reference_text"]
            fields["referenceRange"] = [rng]
        if row["method"]:
            fields["method"] = {"text": row["method"]}
        if row["specimen"]:
            fields["specimen"] = {"display": row["specimen"]}
        notes = [n for n in (f"Section: {row['section']}" if row["section"] else None, row["note"]) if n]
        if notes:
            fields["note"] = [{"text": n} for n in notes]
        printed = [{"url": f"{self.base}/StructureDefinition/printed-value", "valueString": row["value_text"]}]
        if row["alt_value"]:
            printed.append({"url": f"{self.base}/StructureDefinition/printed-alternate",
                            "valueString": " ".join(filter(None, [row["alt_value"], row["alt_reference"]]))})
        fields["extension"] = printed
        self.observation(row["slug"], code, category, **fields)

    # -- vital signs printed in the header --------------------------------------

    def vitals(self) -> list[dict]:
        """One Observation per usable vital sign. The identifier is person/day/kind/value, so the same reading
        printed on three reports of one day is one resource (map_all keeps the first)."""
        spec, day, out = catalog.codes()["vitals"], self.effective[:10], []
        for key, v in self.parsed["vitals"].items():
            text = v["text"].replace(" ", "")
            obs = {"resourceType": "Observation",
                   "identifier": self.ident("paper-vital", f"{self.person['id']}/{day}/{key}/{text}"),
                   "status": "final",
                   "category": [{"coding": [{"system": OBS_CATEGORY, "code": "vital-signs"}]}],
                   "code": loinc(spec[key]), "subject": self.patient, "effectiveDateTime": day,
                   "performer": [self.org]}
            if key == "blood_pressure":
                obs["component"] = [{"code": loinc(spec[part]), "valueQuantity": {
                    "value": v[part], "unit": "mmHg", "system": UCUM, "code": "mm[Hg]"}} for part in ("systolic", "diastolic")]
            else:
                unit = spec[key]["unit"]
                obs["valueQuantity"] = {"value": number(v["number"], v["decimals"]),
                                        "unit": {"/min": "beats/minute", "Cel": "°C"}.get(unit, unit),
                                        "system": UCUM, "code": unit}
            obs["derivedFrom"] = [self.ref("DocumentReference", "paper-document", self.doc.id)]
            out.append(obs)
        return out

    # -- document kinds ----------------------------------------------------------

    def lab(self) -> None:
        for row in self.parsed["results"]:
            self.row(row)

    def ecg(self) -> str | None:
        spec, ecg = catalog.codes()["ecg"], self.x["ecg"]
        for key, value in self.parsed["ecg"].items():
            self.observation(key.replace("_", "-"), loinc(spec[key]), valueQuantity={
                "value": number(value["number"], value["decimals"]), "unit": spec[key]["unit"], "system": UCUM,
                "code": spec[key]["unit"]})
        narrative = catalog.codes()["narrative"]
        if "rhythm" in ecg:
            self.observation("rhythm", loinc(narrative["ecg_rhythm"]), valueString=ecg["rhythm"])
        if "conclusion" in ecg:
            self.observation("conclusion", loinc(narrative["ecg_impression"]), valueString=ecg["conclusion"])
        if "machine_interpretation" in ecg:
            self.observation("machine-interpretation", loinc(narrative["ecg_impression"]),
                             valueString=ecg["machine_interpretation"],
                             method={"text": "Automated interpretation printed by the ECG device"})
        return ecg.get("conclusion") or ecg.get("machine_interpretation")

    def study(self) -> str | None:
        narrative, study = catalog.codes()["narrative"], self.x["study"]
        family = "endoscopy" if self.type == "endoscopy" else "imaging"
        notes = [{"text": f"{label}: {study[key]}"} for key, label in (("indication", "Indication"),
                                                                      ("technique", "Technique")) if key in study]
        self.observation("findings", loinc(narrative[f"{family}_findings"]), valueString=study["findings"],
                         note=notes or None)
        if "impression" in study:
            self.observation("impression", loinc(narrative[f"{family}_impression"]), valueString=study["impression"])
        if "recommendation" in study:
            code = loinc(narrative["imaging_recommendation"]) if family == "imaging" else {"text": "Recommendation"}
            self.observation("recommendation", code, valueString=study["recommendation"])
        for row in self.parsed["measurements"]:
            self.row(row)
        return study.get("impression")

    def visit(self, reference: dict) -> list[dict]:
        visit, sections = self.x["visit"], catalog.codes()["visit_sections"]
        signer = self.signer
        encounter_ref = self.ref("Encounter", "paper-encounter", self.doc.id)
        encounter = {"resourceType": "Encounter", "identifier": self.ident("paper-encounter", self.doc.id),
                     "status": "finished",
                     "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB",
                               "display": "ambulatory"},
                     "subject": self.patient, "period": {"start": self.effective}, "serviceProvider": self.org}
        if signer:
            encounter["participant"] = [{"individual": {"display": signer}}]
        if "reason" in visit:
            encounter["reasonCode"] = [{"text": visit["reason"]}]
        composition = {
            "resourceType": "Composition",
            "identifier": self.ident("paper-composition", self.doc.id)[0],
            "status": "final", "type": loinc(catalog.codes()["documents"]["visit"], TYPE_TEXT["visit"]),
            "subject": self.patient, "encounter": encounter_ref, "date": self.effective, "author": [self.org],
            "title": self.x["document"].get("title") or TYPE_TEXT["visit"], "custodian": self.org,
            "section": [{"title": VISIT_TITLES[key], "code": loinc(sections[key]),
                         "text": {"status": "additional", "div": '<div xmlns="http://www.w3.org/1999/xhtml">'
                                  + html.escape(visit[key], quote=False).replace("\n", "<br/>") + "</div>"}}
                        for key in extraction.VISIT_SECTIONS if key in visit]}
        out = [encounter, composition]
        if "diagnosis" in visit:
            condition = {
                "resourceType": "Condition", "identifier": self.ident("paper-condition", f"{self.doc.id}/diagnosis"),
                "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                                   "code": "confirmed"}]},
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category",
                                          "code": "encounter-diagnosis", "display": "Encounter Diagnosis"}]}],
                "code": {"text": visit["diagnosis"]}, "subject": self.patient, "encounter": encounter_ref,
                "recordedDate": self.effective}
            if signer:
                condition["asserter"] = {"display": signer}
            out.append(condition)
        reference["context"]["encounter"] = [encounter_ref]
        reference["context"]["related"] = [self.ref("Composition", "paper-composition", self.doc.id)]
        return out

    @property
    def signer(self) -> str | None:
        staff = self.x.get("staff", {})
        return staff.get("signed_by") or staff.get("performed_by")

    # -- the document ----------------------------------------------------------

    def resources(self) -> list[dict]:
        x, meta, review = self.x, self.doc.meta, self.doc.review()
        study = self.parsed["study"]
        title = x.get("study", {}).get("procedure") or x["document"].get("title") or TYPE_TEXT[self.type]
        transcription = [{"url": "agent", "valueString": " ".join(filter(None, [
            x["extracted_by"]["agent"], x["extracted_by"].get("model")]))},
                         {"url": "reviewedBy", "valueString": review["approved_by"]},
                         {"url": "reviewedAt", "valueDateTime": review["approved_at"]},
                         {"url": "extractionSha256", "valueString": review["extraction_sha256"]},
                         {"url": "mappingVersion", "valueString": MAPPING_VERSION},
                         {"url": "catalogVersion", "valueString": catalog.catalog_version()}]
        reference = {
            "resourceType": "DocumentReference", "identifier": self.ident("paper-document", self.doc.id),
            "extension": [{"url": f"{self.base}/StructureDefinition/transcription", "extension": transcription}],
            "status": "current", "docStatus": "final",
            "type": loinc(catalog.codes()["documents"][self.type], TYPE_TEXT[self.type]),
            "subject": self.patient, "date": meta["intake_at"], "custodian": self.org, "description": title,
            "content": [{"attachment": {
                "contentType": "application/pdf", "size": meta["pdf"]["bytes"], "hash": meta["pdf"]["sha1_base64"],
                "title": f"{self.doc.id}.pdf",
                "extension": [{"url": f"{self.base}/StructureDefinition/archive-path",
                               "valueString": f"documents/{self.doc.id}/document.pdf"}]}}],
            "context": {"period": {"start": self.effective}}}
        if "language" in x["document"]:
            reference["content"][0]["attachment"]["language"] = x["document"]["language"]
        if "document_number" in x["document"]:
            reference["identifier"].append({"system": f"{self.base}/identifier/printed-number/{self.org_id}",
                                            "value": x["document"]["document_number"]})
        if self.type == "visit":
            return [reference, *self.visit(reference), *self.vitals()]

        conclusion = {"lab-report": self.lab, "ecg": self.ecg}.get(self.type, self.study)()
        reference["context"]["related"] = [self.ref("DiagnosticReport", "paper-report", self.doc.id)]
        category: dict = {"text": TYPE_TEXT[self.type]}
        if self.type in SECTION:
            section = "CUS" if study and study["key"] == "us-heart" else SECTION[self.type]
            category = {"coding": [{"system": SERVICE_SECTION, "code": section}], **category}
        report = {
            "resourceType": "DiagnosticReport", "identifier": self.ident("paper-report", self.doc.id),
            "status": "final", "category": [category],
            "code": loinc(study or catalog.codes()["documents"][self.type], title),
            "subject": self.patient, "effectiveDateTime": self.effective}
        if self.issued:
            report["issued"] = self.issued
        report["performer"] = [self.org]
        if self.signer:
            report["resultsInterpreter"] = [{"display": self.signer}]
        report["result"] = [self.ref("Observation", "paper-observation", o["identifier"][0]["value"])
                            for o in self.observations]
        if conclusion:
            report["conclusion"] = conclusion
        return [reference, report, *self.observations, *self.vitals()]


def patient_resource(person: dict, base: str) -> dict:
    patient = {"resourceType": "Patient",
               "identifier": [{"system": f"{base}/identifier/paper-user", "value": person["id"]}],
               "name": [{"text": person["name"]}]}
    if person.get("sex"):
        patient["gender"] = person["sex"]
    if person.get("birth_date"):
        patient["birthDate"] = person["birth_date"]
    return patient


def ndjson(resources: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in resources)


def map_all(root: Path, base: str, only: str | None = None, force: bool = False, today: dt.date | None = None) -> dict:
    """Map every approved document; write fhir.ndjson per document, fhir/shared.ndjson and fhir/manifest.json."""
    people = store.load_people(root)
    fhir_dir = root / "fhir"
    fhir_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = fhir_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    stamp = {"mapping_version": MAPPING_VERSION, "catalog_version": catalog.catalog_version(), "base": base}
    if any(manifest.get(k) != v for k, v in stamp.items()):
        force = True
    previous = manifest.get("documents", {})
    result = {"mapped": [], "unchanged": [], "skipped": {}, "failed": {}}
    entries, organizations, vitals_seen = {}, {}, set()
    index = duplicates.Index(root, people, today)
    for doc in store.documents(root):
        if not doc.approved():
            result["skipped"][doc.id] = doc.state()
            (doc.path / "fhir.ndjson").unlink(missing_ok=True)     # an unapproved change is never loaded
            continue
        person = people.get(doc.meta["person"])
        issues, parsed = extraction.validate(doc.extraction(), person, today)
        issues += index.issues(doc, parsed)
        errors = [i for i in issues if i["level"] == "error"]
        if not person or errors:
            result["failed"][doc.id] = (f"{len(errors)} validation error(s): run validate" if person else
                                        f"person {doc.meta['person']!r} is no longer in people.toml")
            continue
        mapper = DocumentMapper(doc, person, parsed, base)
        resources = []
        for resource in mapper.resources():
            key = ident_of(resource)
            if key["system"].endswith("/paper-vital"):             # the same reading on several papers: once
                if key["value"] in vitals_seen:
                    continue
                vitals_seen.add(key["value"])
            resources.append(resource)
        organizations.setdefault(mapper.org_id, mapper.organization())
        text = ndjson(resources)
        digest = hashlib.sha256(text.encode()).hexdigest()
        target = doc.path / "fhir.ndjson"
        old = previous.get(doc.id, {})
        selected = only is None or doc.id == only
        if selected and (force or old.get("sha256") != digest or not target.is_file()):
            target.write_text(text, encoding="utf-8")
            entries[doc.id] = {"sha256": digest, "resources": len(resources), "person": person["id"],
                               "type": parsed["type"], "effective": mapper.effective, "mapped_at": store.now_iso()}
            result["mapped"].append(doc.id)
        else:
            entries[doc.id] = old or {"sha256": digest, "resources": len(resources)}
            result["unchanged"].append(doc.id)
    shared = [patient_resource(p, base) for p in people.values()] + [organizations[k] for k in sorted(organizations)]
    (fhir_dir / "shared.ndjson").write_text(ndjson(shared), encoding="utf-8")
    store.write_json(manifest_path, {**stamp, "updated_at": store.now_iso(), "documents": entries})
    return result
