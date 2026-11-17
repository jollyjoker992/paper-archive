---
name: paper-archive
description: Take paper medical documents (photos or scans of lab reports, ECGs, ultrasound, X-ray, endoscopy reports, clinic visit notes) into paper-archive - digitize to PDF, transcribe to extraction.json, validate, and prepare the review sheet for the owner. Use when the user gives health papers, scans or photos to file, digitize, read or load into Medplum.
---

# Taking a paper document into the archive

You transcribe; code does the rest. Read `AGENTS.md` first if you have not: the rules there (transcribe as
printed, only the owner approves, never guess a code) are not repeated here.

## 1. Sort the pages into documents

Look at every file before running anything. One document = one report as the hospital issued it:

- A lab report often runs over several pages (same header, continuing table): one document, pages in order.
  A panel heading at the bottom of one page belongs to the first row of the next.
- An X-ray film and its written report are one document: report first, film after.
- Reports of the same day from different departments (ECG, echo, abdominal ultrasound) are separate documents.
- Already filed? `paper_archive.py status` lists what the archive holds. Intake refuses a photo that is
  already in (also as a page of another document): take in only the new pages. A paper photographed again
  gets a new id; validation then reports `E-DUPLICATE-DOCUMENT` or `W-POSSIBLE-DUPLICATE` against the
  earlier one (step 4).
- Whose is it? The name on the paper decides `--person` (`paper_archive.py people`). If the person is not in
  `people.toml`, stop and ask the owner to add them.

## 2. Intake

```sh
python3 paper_archive.py intake --person <id> page1.heic page2.heic     # or one PDF
```

It copies the originals, builds `document.pdf` and prints the document id and the path to write
`extraction.json` to. The same files always give the same id; taking them in twice is refused.

## 3. Transcribe

`python3 paper_archive.py template <type>` prints the empty form. Read the page images themselves
(the originals listed by `intake`; convert HEIC to JPEG in a scratch folder if your reader needs it, and
re-read at full resolution any digit you are not sure of). Then follow the reference for the type:

| Document | Type | Reference |
|---|---|---|
| Blood and urine tests | `lab-report` | [references/lab-report.md](references/lab-report.md) |
| Electrocardiogram | `ecg` | [references/ecg.md](references/ecg.md) |
| Ultrasound (incl. echocardiography), X-ray, endoscopy | `ultrasound`, `xray`, `endoscopy` | [references/study.md](references/study.md) |
| Clinic visit / consultation result | `visit` | [references/visit.md](references/visit.md) |

For every type (`docs/extraction-format.md` is the full format):

- **Strings exactly as printed**: digits, decimal mark (`6,8` stays `6,8`), comparator (`< 0,5`), spelling,
  capitals, typos. Line breaks inside a text block become `\n`.
- **Not printed = leave out** (or null). Never copy a value from another document, the previous page or
  what would be normal.
- **Dates** are the one thing you convert: to `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, local time as printed, no
  offset. `02/10/26` is `2026-10-02`; `PM 2:05` is `14:05`. Which date is which:
  `collected` (lấy mẫu), `received`, `performed` (ngày khám, ngày chụp), `reported` (duyệt, ngày ký).
- **Patient block**: name as printed, `birth_date` or `birth_year` or `age` (whatever is printed), `sex`,
  `patient_number` (the hospital's patient ID). The address is not transcribed.
- **Header vitals** (`HA`, `Mạch`, `Cân nặng`, `Chiều cao`, `BMI`, `Nhiệt độ`) go into `vitals` as printed
  numbers, in mmHg, /min, kg, cm, kg/m2, °C. Transcribe nonsense too (`0`, `NaN`, `1600`): the validator
  drops what is impossible and tells the owner.
- **`uncertain`**: one entry per value you could not read with confidence, `{"path": "results[12].value",
  "reason": "glare; 4.52 or 4.58"}`. Put your best reading in the value and say so here. An honest
  `uncertain` costs the owner ten seconds; a silent guess costs a wrong number in a medical record.
- **`notes`**: things on the paper that have no field (stamps, handwritten remarks, what the attached
  pictures are, anything odd about the print).
- `extracted_by`: `{"agent": "<your harness>", "model": "<your model id>"}`.

## 4. Validate and fix

```sh
python3 paper_archive.py validate <doc>
```

- **Errors** mean the transcription and the rules disagree. Go back to the paper for that field. The usual
  causes: a decimal slip, a unit read wrong (`G/L` is 10⁹/L, `g/L` is gram per litre), a row assigned to the
  wrong catalog key, a page of someone else's report. Fix only what the paper supports.
- **Warnings** are for the owner: a cross-check that does not add up, a value outside its range with no
  mark, a mark on a value inside its range, an `uncertain` entry, a vital sign the hospital printed wrongly.
  Re-read the fields a warning names; if your transcription is right, leave it and let the owner decide.
- **Notes** (`I-…`) need nothing.

- **Duplicates**: `E-DUPLICATE-DOCUMENT` / `W-POSSIBLE-DUPLICATE` name the other document. Show the owner
  both review sheets. If it is the same paper, the owner picks which copy goes and you run
  `paper_archive.py remove <full id>` for that one only. Never remove on your own judgment, and never edit a
  transcription to make two documents look different.

`docs/validation-rules.md` explains every code. Never make an error go away by setting a field to null,
dropping a row or changing a value away from the print.

## 5. Review with the owner

```sh
python3 paper_archive.py review <doc>
```

Show the owner the sheet (all of it: header, every row with the range that will be used and the code it will
be stored under, every warning) and the path of `document.pdf`, and ask them to compare it with the paper.
State plainly what you were unsure of. Do not summarise the sheet into "looks fine".

When the owner says a document is right:

```sh
python3 paper_archive.py approve <doc> --by "<owner's name>" [--accept-warnings]
python3 paper_archive.py map && python3 paper_archive.py load      # load only when asked
```

Any later edit of `extraction.json` voids the approval: the document leaves Medplum on the next load until
it is approved again.

## What you never do

- Write or edit `fhir.ndjson`, or invent FHIR by hand.
- Approve for the owner, or accept warnings they have not seen.
- Interpret results for the record: no diagnosis, no "normal", no flag the paper does not carry. (Answering
  the owner's questions about their results in conversation is fine; it does not go into the archive.)
