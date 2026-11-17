# FHIR mapping (version 0.1.0)

How an approved transcription becomes FHIR R4 resources. This is what `paper_archive.py map` implements
(`paperarchive/fhir.py`). FHIR is a view: it is rebuilt from `extraction.json` and the catalog, and the scan
plus the approved transcription stay the source of truth.

The structure follows garmin-archive and healthkit-archive: stable identifiers, conditional upserts, LOINC
first, never a near miss.

## Output

| File (under the data directory) | Content |
|---|---|
| `documents/<id>/fhir.ndjson` | All resources of one document, one per line. Exists only while the document is approved. |
| `fhir/shared.ndjson` | One Patient per person in `people.toml`, one Organization per facility name. |
| `fhir/manifest.json` | Mapping version, catalog version, base; per document the SHA-256 of its file. |
| `fhir/load-manifest.json` | Per document: what was loaded, its identifiers and Binary id on the server. |

A document with `E-DUPLICATE-DOCUMENT` is not mapped, even if it was approved before the other copy was
taken in: the same paper is on the server once. A removed document leaves the server on the next `load`.

A document is mapped again when its transcription, the mapping version, the catalog version or the base
changes, or with `--force`. Output is deterministic: mapping twice gives the same bytes, so `load` has
nothing to do. Resources have no `id`; references use `Type?identifier=<system>|<value>` and the server
resolves them in the transaction.

## Rules

1. **The paper's content only.** Values, units, ranges, flags and texts are the printed ones. Nothing is
   converted to another unit, no interpretation is computed from a value alone, no diagnosis is inferred.
2. **LOINC first, never a near miss.** Codes come only from the catalog; every code in it was checked
   against the NLM LOINC API (`tools/loinc_check.py`, last run 2026-11-17: 157 codes, none unknown or
   deprecated). A quantity with no exact code keeps `code.text` = the printed name and no coding. The
   printed name is always in `code.text`, coded or not.
3. **The unit picks the code.** Glucose in mmol/L is 14749-6, in mg/dL 2345-7: one catalog entry, one code
   per property.
4. **Stable identifiers make loading idempotent.** Every resource has an identifier in
   `<base>/identifier/<kind>`; base is `https://github.com/jollyjoker992/paper-archive/fhir`. The document
   id (`d_` + 16 hex) is derived from the SHA-256 of the scans, so the same paper always maps to the same
   resources.
5. **Times carry the person's offset.** Printed local times get the offset of the person's time zone
   (`2026-10-02T07:15:00+07:00`); a date without time stays a date.
6. **Every result points to its scan.** `Observation.derivedFrom` → the DocumentReference, whose attachment
   is the PDF (a `Binary`).
7. **Who read it is recorded.** The DocumentReference carries the `transcription` extension: agent and
   model, who approved and when, the SHA-256 of the approved transcription, mapping and catalog versions.
8. **Missing means absent.** No value, no element.

## Resources

| Resource | One per | Identifier kind | Notes |
|---|---|---|---|
| `Patient` | person | `paper-user` = person id | `name.text`, `gender`, `birthDate` from `people.toml`. One Patient per source, as in the other archives; a FHIR Person links them (agentic-assistant's steward). |
| `Organization` | facility name | `paper-organization` = slug of the name | `name`, `address.text`. |
| `DocumentReference` | document | `paper-document` = document id (second identifier: the printed report number, system `…/printed-number/<organization>`) | `type` by document type, `date` = intake time, `content.attachment` = the PDF (`size`, SHA-1 `hash`, `url` set by the loader), `context.period.start` = clinical date, `context.related` → the report or Composition. |
| `DiagnosticReport` | lab report, ECG, ultrasound, X-ray, endoscopy | `paper-report` = document id | `category`, `code`, `effectiveDateTime`, `issued` (when a report time is printed), `performer` = Organization, `resultsInterpreter.display` = signing doctor, `result` = its Observations, `conclusion`. |
| `Observation` | result row, measurement, narrative block | `paper-observation` = `<document id>/<slug>` | See below. |
| `Observation` (vital sign) | printed vital sign | `paper-vital` = `<person>/<date>/<kind>/<value>` | See "Vital signs". |
| `Encounter`, `Composition`, `Condition` | visit note | `paper-encounter`, `paper-composition`, `paper-condition` | See "Visit notes". |
| `Binary` | document | (server id) | The PDF; uploaded by the loader. |

### Document types

| Type | DocumentReference.type | DiagnosticReport.code | category (v2-0074) | Observation.category |
|---|---|---|---|---|
| `lab-report` | 11502-2 Laboratory report | 11502-2 | `LAB` | `laboratory` |
| `ecg` | 11524-6 EKG study | 11524-6 | `EC` | `procedure` |
| `ultrasound` | 18748-4 Diagnostic imaging study | the study's code, else 18748-4 | `RUS` (`CUS` for `us-heart`) | `imaging` |
| `xray` | 18748-4 | the study's code, else 18748-4 | `RX` | `imaging` |
| `endoscopy` | 18751-8 Endoscopy study | the study's code, else 18751-8 | text only (v2-0074 has no endoscopy code) | `procedure` |
| `visit` | 34108-1 Outpatient Note | (no report) | | |

`DiagnosticReport.code.text` is the printed title of the examination.

### Result rows (lab results, study measurements)

| Element | From |
|---|---|
| `code` | Catalog code for the row's unit + `text` = printed name; text only when not coded. |
| `valueQuantity` | Parsed number (decimal comma accepted, printed decimals kept), `comparator` for `<`/`>`, `unit` = printed unit, `system`/`code` = UCUM from `units.json`. A quantity printed without unit has a bare value. |
| `valueString` | A word result (`Âm tính`), as printed. |
| `interpretation` | v3 ObservationInterpretation, only from the paper: printed flag (`H`, `L`, `HH`, `LL`, `A`, `N`); a value marked by colour or bold with its direction taken from the printed range; `NEG`/`POS`/`N` for a word result; `NEG` for an index under its printed rule. |
| `referenceRange` | `low`/`high` parsed from the printed range (the range of the person's sex when both are printed) + `text` = the whole printed cell. Unparsed ranges are text only. |
| `method`, `specimen.display` | As printed. |
| `note` | `Section: <printed heading>`, printed row note. |
| extension `printed-value` | The value string exactly as printed. |
| extension `printed-alternate` | The second-unit result and its range, as printed. It is the same measurement and is not a second Observation. |

**Presence tests reported as an index.** HBsAg and anti-HCV are printed here as a cutoff index with a rule
(`0.42`, `Âm tính <1 COI`). No LOINC code for the index was found, so the presence code is used
(5195-3, 16128-1) with `valueQuantity` = the index in `{COI}` and `interpretation` = what the printed rule
gives. Consumers should read the interpretation, not threshold the value themselves.

**Urine strip.** `Hồng cầu` and `Bạch cầu` under a urine heading are the strip's blood and leukocyte pads
(5794-3 Hemoglobin [Presence], 5799-2 Leukocyte esterase [Presence]), not cell counts.

### ECG

One Observation per printed measurement (`procedure`): heart rate 8867-4 (`/min`), RR 8637-1, PR 8625-6,
QRS 8633-0, QT 8634-8, QTc 8636-3 (`ms`), P/QRS/T axis 8626-4 / 8632-2 / 8638-9 (`deg`). Rhythm 8884-9 and
the conclusion 18844-1 are `valueString`. A device's automatic interpretation is a second 18844-1
Observation with `method.text` saying so. `DiagnosticReport.conclusion` is the doctor's conclusion, else the
device's. The tracing itself is only in the PDF.

### Ultrasound, X-ray, endoscopy

Narrative Observations with `valueString`:

| Block | Imaging | Endoscopy |
|---|---|---|
| findings | 18782-3 Radiology Study observation (narrative) | 19805-1 Endoscopy procedure Study observation Narrative |
| impression | 19005-8 Radiology Imaging study [Impression] (narrative) | 59769-0 Postprocedure diagnosis Narrative |
| recommendation | 18783-1 Radiology Study recommendation (narrative) | text only |

Indication and technique are notes on the findings Observation. `DiagnosticReport.conclusion` = impression.
Measurements are result rows (above). The images are only in the PDF; no `ImagingStudy` is created because
there is no DICOM data.

### Vital signs

Header vital signs that pass validation become `vital-signs` Observations dated with the document's date:
blood pressure 85354-9 with components 8480-6 / 8462-4 (`mm[Hg]`), heart rate 8867-4 (`/min`), temperature
8310-5 (`Cel`), height 8302-2 (`cm`), weight 29463-7 (`kg`), BMI 39156-5 (`kg/m2`).

Hospitals print the same reading on every report of a visit. The identifier is
`<person>/<date>/<kind>/<value>`, and `map` emits each one only with the first document that carries it, so
three reports of one afternoon give one blood pressure, not three.

### Visit notes

| Resource | Content |
|---|---|
| `Encounter` | `status` finished, `class` AMB, `period.start`, `serviceProvider`, `participant.individual.display` = the doctor, `reasonCode.text` = printed reason. |
| `Composition` | `type` 34108-1, one `section` per printed section, text as printed (escaped XHTML): reason 10154-3, medications 10160-0, family history 10157-6, past history 11348-0, allergies 48765-2, examination 29545-1, tests 30954-2, diagnosis 29548-5, advice 18776-5. |
| `Condition` | The printed diagnosis: `code.text` only (the paper carries no code), `category` encounter-diagnosis, `verificationStatus` confirmed, `encounter`, `recordedDate`, `asserter.display`. |

History, allergies and medications stay narrative sections: a doctor's free-text line is not turned into
`AllergyIntolerance` or `MedicationStatement` resources.

## Not mapped

`notes`, `uncertain`, `patient.*` (used for checking only), `staff.ordered_by`, `document.pages`, printed
pictures. They remain in `extraction.json` and in the PDF.

## Loading

`paper_archive.py load` sends `shared.ndjson`, then one transaction per document: every entry is
`PUT Type?identifier=…`, so a second load changes nothing. The PDF is uploaded once as a `Binary` and
re-used while its hash is unchanged. Resources a document no longer produces are deleted, and a document
that lost its approval (or its folder) is removed from the server with its PDF; a resource that another
current document still produces is never deleted. Configuration: `PAPER_FHIR_URL`, `PAPER_FHIR_PROJECT_ID`,
`PAPER_FHIR_CLIENT_ID`, `PAPER_FHIR_CLIENT_SECRET`.

A read-only client for agents needs, beyond what the other archives' policies list: `DiagnosticReport`,
`Organization`, `Encounter`, `Composition`, `Condition`.
