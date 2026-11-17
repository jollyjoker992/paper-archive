# The transcription format (`paper-archive/extraction/1`)

`extraction.json` is what the agent writes for one document: the paper's content as strings, with no
interpretation. It is the reviewed record; FHIR is derived from it and can be rebuilt at any time. The
schema is `SCHEMA` in `paperarchive/extraction.py`; `paper_archive.py template <type>` prints an empty one.
Unknown fields are errors. A field that is null, an empty string or absent means "not printed".

## Common part

```json
{
 "format": "paper-archive/extraction/1",
 "document": {"type": "lab-report", "language": "vi", "title": "…", "document_number": "…", "pages": 3},
 "patient": {"name": "…", "birth_date": "1985-03-14", "birth_year": 1985, "age": 41, "sex": "female", "patient_number": "…"},
 "facility": {"name": "…", "department": "…", "address": "…"},
 "staff": {"ordered_by": "…", "performed_by": "…", "signed_by": "…"},
 "dates": {"collected": "2026-10-02T07:15", "received": "…", "performed": "…", "reported": "2026-10-02T10:42"},
 "vitals": {"pulse": "70", "blood_pressure": "116/74", "temperature": "36.8", "height": "160", "weight": "54", "bmi": "21.09"},
 "extracted_by": {"agent": "claude-code", "model": "…"},
 "uncertain": [{"path": "results[3].value", "reason": "…"}],
 "notes": "…"
}
```

| Field | Rule |
|---|---|
| `document.type` | `lab-report`, `ecg`, `ultrasound`, `xray`, `endoscopy`, `visit`. Decides which body is required. |
| `document.document_number` | Report or sample number printed on the paper. |
| `patient.*` | As printed; only what is printed. Used solely to check the document belongs to the person it was filed under. `age` is the printed age in years. |
| `facility.name` | Required. Becomes the FHIR Organization (one per distinct name). |
| `dates.*` | `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM[:SS]`, local time, no offset (the person's time zone supplies it). A lab report needs `collected`, `received` or `reported`; the others need `performed` or `reported`. The first of those is the clinical date of everything in the document. |
| `vitals.*` | Numbers as printed; units are fixed (/min, mmHg as `systolic/diastolic`, °C, cm, kg, kg/m2). Impossible values are dropped with a warning. |
| `uncertain` | Every value the agent could not read with confidence. Each entry is a warning the owner must settle. |
| `notes` | Printed content with no field. Kept in the transcription; not mapped to FHIR. |

## `results` (lab-report) and `study.measurements`

A list of rows:

| Field | |
|---|---|
| `name` (required), `section` | Printed test name; printed heading above it. |
| `value` (required) | Printed result: number, number with comparator, or word. |
| `unit` | Printed unit when it has its own place; otherwise it is read from `reference`. |
| `reference` | The printed normal-range cell, whole. Parsed forms: `a - b`, `< a`, `> a`, `≤`, `≥`, optional brackets and `bt:` label, optional trailing unit, per-sex ranges (`Nam … Nữ …`), a word with a threshold (`Âm tính <1 COI`), a word alone (`Âm tính`), a trailing condition (`<120@nam`). Anything else is kept as text. |
| `flag` | Printed abnormal mark (`H`, `L`, `HH`, `LL`, `*`, `↑`, `↓`, `A`, `N`) or `marked` for colour/bold only. |
| `alt_value`, `alt_reference` | The same result in a second unit system, as printed. |
| `analyte` | Catalog key (`paperarchive/catalog/analytes.json`) or null. |
| `specimen`, `method`, `note`, `page` | As printed. |

## `ecg`

`measurements` (`heart_rate`, `rr_interval`, `pr_interval`, `qrs_duration`, `qt_interval`, `qtc_interval`,
`p_axis`, `qrs_axis`, `t_axis`: printed numbers, /min, ms, degrees), `rhythm`, `machine_interpretation`,
`conclusion`, `device`.

## `study` (ultrasound, xray, endoscopy)

`key` (catalog key of the study or null), `procedure` (required), `indication`, `technique`, `findings`
(required), `measurements` (rows as above), `impression`, `recommendation`.

## `visit`

`reason`, `medications`, `family_history`, `past_history`, `allergies`, `examination`, `tests`, `diagnosis`,
`advice`: each the printed text of that section.

## What is derived, never transcribed

Numeric values and comparators, UCUM units, low/high of the range (for the person's sex), the interpretation
(`H`/`L` from a `marked` value and its printed range; negative from a printed index rule), LOINC codes, time
offsets, identifiers. They are computed by `paperarchive/extraction.py` and shown on the review sheet, which
is what the owner approves.
