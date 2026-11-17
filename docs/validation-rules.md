# Validation rules

`paper_archive.py validate <doc>` runs every rule on `extraction.json`. There are four layers; a document
reaches Medplum only through all of them.

1. **Format** — the transcription has the right shape (`E-SCHEMA`, `E-FORMAT`, `E-BODY`).
2. **Plausibility** — printed strings parse, units are known, values are possible, the arithmetic of the
   report holds, the document belongs to the person. This layer exists to catch reading errors.
3. **The owner** — `review` prints everything, with what was derived; `approve` binds the approval to the
   SHA-256 of `extraction.json`. Any later change voids it.
4. **FHIR** — `map` validates again and writes resources; `tools/fhir-validate` runs Medplum's own
   validator (`@medplum/core`, the version of the server) over them; the loader checks every transaction
   entry.

Levels: **error** blocks approval and mapping. **warning** blocks approval until the owner has looked
(`approve --accept-warnings`; accepted warnings are recorded in `review.json`). **info** needs nothing.

## Format

| Code | Meaning |
|---|---|
| `E-SCHEMA` | Unknown field, missing required field, wrong type (a value must be the printed string, not a number), bad date. |
| `E-FORMAT` | `format` is not `paper-archive/extraction/1`. |
| `E-BODY` | The body does not fit the document type (`results`, `ecg`, `study`, `visit`, `procedure`, `vaccination`). |
| `E-EMPTY` | A lab report without results, an ECG or visit with nothing in it, a vaccination document without doses or appointments. |

## Whose document, and when

| Code | Meaning |
|---|---|
| `E-PATIENT-NAME` | Printed name is neither the person's name nor an alias (case and diacritics ignored). |
| `E-PATIENT-BIRTH` | Printed birth date or year differs. |
| `E-PATIENT-AGE` | Printed age fits neither full years nor year-minus-year at the document's date. |
| `E-PATIENT-SEX` | Printed sex differs. |
| `W-PATIENT-NO-NAME` | No name on the paper. |
| `E-DATE-MISSING` | No date the document's clinical time can be taken from; a vaccination dose with neither its own date nor a document date. |
| `E-DATE-FUTURE`, `E-DATE-BEFORE-BIRTH`, `E-DATE-ORDER` | A date after today, before birth, or collected > received > performed > reported out of order. |

## The same paper twice

Intake refuses any file already in the archive, whether it was taken in alone, as a page of another
document, or is a `document.pdf` given back. Different bytes of the same paper (photographed again, scanned
instead of photographed, a page taken in alone and later with the whole report) are found by comparing
transcriptions with the person's other documents. Only the document taken in later carries the issue, so the
first one keeps its approval and stays on the server. Reports with different printed numbers are never
duplicates of each other, which matters because one printed number can cover several examinations.

| Code | Meaning |
|---|---|
| `E-DUPLICATE-DOCUMENT` | Same person, type, facility, clinical date and time, no conflicting printed number, and every value and text identical: the same paper. It cannot be approved or mapped. The owner decides which copy goes; `paper_archive.py remove <full id>` deletes it. |
| `W-POSSIBLE-DUPLICATE` | Same person, type, facility and day, and either the same title or at least half of the results identical, but the transcriptions differ: the same paper read differently, part of it taken in twice, a corrected report reissued, or two papers. The owner compares both with the paper. |

## Rows (lab results, study measurements)

| Code | Meaning |
|---|---|
| `E-ANALYTE-UNKNOWN` | `analyte` is not a catalog key. |
| `E-DOMAIN` | A blood key in a urine section, an echo key in a lab report, and so on. |
| `W-NAME-UNLISTED` | Neither the printed name nor its heading is among the entry's names: possibly the wrong test. |
| `W-NAME-BETTER` | Another entry's name fits the printed name better (`Calcium ion hóa` assigned to `calcium`). |
| `W-ANALYTE-AVAILABLE` | No key given although the catalog has the test. |
| `I-UNCODED` | Not in the catalog, or known and deliberately not coded: stored with the printed name only. |
| `E-UNIT-UNKNOWN` / `W-UNIT-UNKNOWN` | Unit not in `units.json` (error for a coded row; an uncoded row keeps it as text). |
| `E-UNIT-MISSING` | A coded quantity with no unit in its column or in the reference. |
| `E-UNIT-ANALYTE` | The unit is known but this test is not measured in it: wrong test or wrong unit. |
| `W-UNIT-MISMATCH` | Unit column and reference disagree. |
| `I-UNIT-ASSUMED` | No unit printed for a quantity reported in one unit only (catalog `assume_unit`). |
| `E-VALUE-NOT-NUMBER` | A coded quantity whose value is not a number. |
| `E-VALUE-NOT-QUALITATIVE` | A negative/positive test with another kind of value (and no printed index rule). |
| `W-VALUE-TEXT` | Uncoded row whose value is neither number nor negative/positive: stored as text. |
| `W-NUMBER-AMBIGUOUS` | `1.250` on an uncoded row: decimal or thousands separator? |
| `E-IMPLAUSIBLE` | Value outside what a living person can have in that unit: decimal slip or wrong unit. The bounds are in the catalog, per code and unit. |
| `E-REFERENCE-ORDER` | Low above high. |
| `I-REFERENCE-TEXT` | Reference not in a parsed form (or per-sex with the sex unknown): kept as text. |
| `E-FLAG-UNKNOWN` | A mark that is not in the table of marks. |
| `W-FLAG-CONFLICT` | The mark contradicts value and range (H below the range, marked inside it). |
| `W-FLAG-MISSING` | Value outside its printed range and no mark transcribed. |
| `W-INDEX-OUTSIDE` | An index that does not satisfy the printed rule: no interpretation is stored. |
| `E-DUPLICATE` | Two rows resolve to the same LOINC code. |
| `W-CROSS-CHECK` | Blood count arithmetic fails: differential ≠ 100 % (±2), absolute count ≠ WBC × %, MCV ≠ HCT/RBC×10, MCH ≠ HGB/RBC×10, MCHC ≠ HGB/HCT×100 (6 % tolerance). |

The interpretation stored is only ever the paper's: a printed flag, a `marked` value resolved by its printed
range, a word result, or an index under its printed rule. A value outside its range with no mark gets no
interpretation, only `W-FLAG-MISSING`.

## ECG, studies, visits, procedures, vitals

| Code | Meaning |
|---|---|
| `E-VALUE-NOT-NUMBER`, `E-IMPLAUSIBLE` | ECG measurement not a number or outside its range (`codes.json` → `ecg`). |
| `W-CROSS-CHECK` | RR does not fit the heart rate; QTc fits no usual correction (Bazett, Fridericia, Framingham, Hodges, ±25 ms) of QT at that rate. |
| `W-NO-CONCLUSION` | No conclusion/impression/diagnosis transcribed (a procedure note: neither `diagnosis` nor `postoperative_diagnosis`). |
| `E-STUDY-UNKNOWN`, `E-STUDY-TYPE` | `study.key` not in the catalog, or of another document type. |
| `W-STUDY-AVAILABLE` | No key although the catalog has the study. |
| `W-VITAL-UNUSABLE` | A header vital sign that is impossible, not a number, or a BMI that does not follow from height and weight. It is left out of FHIR; the document is not blocked, because hospital software really prints `0kg`, `1600cm`, `BMI: NaN`. |
| `W-UNCERTAIN` | The agent listed a value it could not read with confidence. |

## Vaccinations

| Code | Meaning |
|---|---|
| `E-DOSE-UNCONFIRMED` | `given` is missing: ask the owner whether this dose was given (`yes`, `no`, `unknown`). The paper does not decide it; a slip shows an order. |
| `I-DOSE-ORDERED` | `given: unknown`: stored as an order (`MedicationRequest`), not in the vaccination history. |
| `E-VACCINE-UNKNOWN` | `key` is not a vaccine catalog key. |
| `W-VACCINE-UNLISTED` | The printed name and disease are not among the entry's names, or the printed name fits another entry better (`Gardasil 9` keyed as `hpv4`). |
| `W-VACCINE-AVAILABLE` | No key given although the catalog has the vaccine. |
| `I-UNCODED` | Not in the vaccine catalog, or known and deliberately not coded (`Hexaxim`): stored with the printed name only. |
| `E-DATE-FUTURE`, `E-DATE-BEFORE-BIRTH` | A dose date after today (allowed only for `given: unknown`, an order for a later day) or before birth. |
| `E-DATE-ORDER` | `next_due` is not after the dose's date. |
| `W-DOSE-REPEATED` | The same vaccine twice on the same day in one document: a row read twice, or a different vaccine. |
| `I-DOSE-TEXT` | `dose_quantity` is not a number with a known unit (a bare number without `dose_unit`): kept as a note. |
| `W-DOSE-ELSEWHERE` | The same vaccine on the same day is a dose of another document of the person (a loose card and the booklet it belongs in): one shot would be in the history twice. Not reported between two documents already reported as the same paper. |
| `W-DOSE-EXPIRED` | `expiry` (to the end of its month) is before the dose's date: a misread expiry or date, or a wrong paper. |

The vaccine checks (`E-VACCINE-UNKNOWN`, `W-VACCINE-UNLISTED`, `W-VACCINE-AVAILABLE`, `I-UNCODED`) and
`E-DATE-BEFORE-BIRTH` apply to `due` appointments too.

## What the rules cannot catch

A wrong digit that stays plausible and breaks no arithmetic (glucose 5.4 read as 5.9), a row skipped
entirely, text mis-typed inside a findings block. That is why the owner's comparison with the paper is a
required step and why the scan is stored next to every result (`Observation.derivedFrom`).
