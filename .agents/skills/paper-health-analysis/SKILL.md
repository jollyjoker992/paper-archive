---
name: paper-health-analysis
description: Answer questions about a person's paper medical records stored in Medplum by paper-archive - lab results (blood count, glucose, lipids, liver and kidney tests, thyroid, urine), ECG, ultrasound, echocardiography, X-ray and endoscopy reports, clinic visit notes, vaccinations (doses given, ordered, next doses due) and the vital signs printed on them. Use for questions like "what was my LDL last time", "how has my cholesterol changed", "what did the abdominal ultrasound say", "list my documents from 2023", "when was my last flu shot", "which vaccines are due", "send me the ECG scan".
---

# Paper records analysis (read-only)

Paper documents reach Medplum only after their owner compared the transcription with the paper and
approved it. Each value is stored as printed: the printed value and unit, the printed reference range and
an interpretation only when the paper carried one. Every result points back to the scan of its document.

```sh
python3 tools/medplum_agent.py people                          # who has paper records
python3 tools/medplum_agent.py documents [--person P] [--type lab] [--from 2023-01-01] [--to ...]
python3 tools/medplum_agent.py document d_42a9                 # one document: conclusion, results, vitals
python3 tools/medplum_agent.py results ldl [--person P]        # one test over time (catalog key)
python3 tools/medplum_agent.py results 2093-3                  # or a LOINC code
python3 tools/medplum_agent.py results "RDW-SD"                # or words of the printed name
python3 tools/medplum_agent.py vaccinations [--person P]       # every vaccine dose, oldest first
python3 tools/medplum_agent.py pdf d_42a9 [--out FILE]         # the scan, to send or to look at
```

Run from the repository root. `python3 paper_archive.py catalog find "<name>"` gives the catalog key of a
test (`hba1c`, `egfr`, `urea`, `alt`, ...).

## Answering

1. **Who.** One person per question. If `people` lists several and the question does not say whom, ask.
2. **Find, then read.** `documents` for "what do I have", `results <test>` for trends and "last time",
   `document <id>` for one report. Do not open `data/` (transcriptions and scans on disk) to answer: what
   is in Medplum is what the owner approved.
3. **Quote, do not re-interpret.** Give the value with its unit and the paper's own reference range, and
   say "marked high/low on the report" only when `interpretation` is there. A value outside its printed
   range without a mark is described as outside the printed range, not as abnormal.
4. **Same test, different units.** A test can appear in mmol/L on one report and mg/dL on another; they are
   different LOINC codes of the same catalog entry, and `results <key>` returns both. Never compare numbers
   across units; convert only if asked, and say you converted.
5. **Reference ranges differ between labs.** Compare a value with the range printed on its own report.
6. **Imaging, ECG, endoscopy and visit notes** are text: quote the conclusion (impression, diagnosis)
   verbatim, then summarise the findings in plain words if asked, without adding findings.
7. **Vaccinations** have three states; never merge them. `given` (the owner confirmed it at review),
   `not given`, and `ordered, not known whether given` (a slip whose shot nobody confirmed): only `given`
   counts as a dose received. `next dose due (printed)` is the appointment printed on that paper; a later
   dose of the same vaccine may already have taken place, so check the list before saying a dose is overdue.
   The vaccine name is the printed one; the CVX code may be an "unspecified formulation" code on purpose.
8. **Dates** are the clinical date (sample collected, examination performed), in the person's time zone.
9. **The scan.** For "show me", "send me" or when a value is questioned, get the PDF with `pdf` and attach
   it. If a value looks wrong against the scan, say so: the owner can correct and re-approve the
   transcription.
10. **Not medical advice.** Describe what the records say and how values changed. For results marked
   abnormal, persistent changes or worrying findings, suggest discussing them with a doctor; never
   diagnose.

## Limits

- Only approved documents are in Medplum. Papers still being transcribed or reviewed are not; the
  `paper-archive` skill and `python3 paper_archive.py status` show those.
- A test the catalog does not code (`I-UNCODED` at intake) has no LOINC code: find it by its printed name.
- Vital signs printed in report headers (blood pressure, pulse, weight, height, BMI, temperature) are
  stored once per reading and day, in the `vital_signs` of the first document that printed them.
