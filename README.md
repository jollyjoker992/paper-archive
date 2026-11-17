# paper-archive

Paper medical documents into FHIR. Photograph or scan a lab report, ECG, ultrasound, X-ray, endoscopy report,
clinic visit note or vaccination slip, booklet or certificate; an AI agent digitizes it to a PDF and transcribes it; code validates the transcription,
you check it against the paper, and it is loaded into a [Medplum](https://www.medplum.com) FHIR R4 server
next to the data of [garmin-archive](https://github.com/jollyjoker992/garmin-archive) and
[healthkit-archive](https://github.com/jollyjoker992/healthkit-archive).

```
scans ─▶ document.pdf ─▶ extraction.json ─▶ validation ─▶ your approval ─▶ FHIR NDJSON ─▶ Medplum
          (intake)        (the agent)        (rules)        (review)         (map)          (load)
```

The design choice that matters: **the agent never writes FHIR.** It fills in a strict form with what is
printed, as strings. Parsing numbers, choosing units and codes, picking the reference range for the person's
sex, building resources — all of that is deterministic code with tests, and nothing reaches the server
without a person having compared the review sheet with the paper.

## What it handles

| Document | Becomes |
|---|---|
| Blood and urine test reports | `DiagnosticReport` + one `Observation` per result (LOINC, UCUM, printed range and flag) |
| ECG | `DiagnosticReport` + printed measurements and conclusion |
| Ultrasound, echocardiography, X-ray | `DiagnosticReport` + findings, impression, named measurements |
| Endoscopy | the same, with endoscopy codes |
| Clinic visit note | `Encounter`, `Composition` (sections as printed), `Condition` (the diagnosis) |
| Vaccination slip, booklet, certificate | per dose, by your answer to "was it given?": `Immunization` (CVX, lot, dose number) completed or not-done, or a `MedicationRequest` for an order you cannot confirm; `ImmunizationRecommendation` for a printed next-dose date |
| Vital signs printed in any header | `Observation` (vital-signs), once per reading |
| Every document | `DocumentReference` + the PDF as `Binary`; every result links back to its scan |

The same paper is stored once: intake refuses files already in the archive, and a paper photographed or
scanned again is recognised from its content and held back until you decide which copy stays.

Written first for Vietnamese hospital reports (units inside the reference column, `Nam`/`Nữ` ranges,
abnormal values marked by colour, a second column in other units), in Vietnamese or English.

## Quick start

```sh
mkdir -p data && cp people.example.toml data/people.toml     # who the archive is for; fill it in
python3 paper_archive.py intake --person me ~/scans/lab-1.jpg ~/scans/lab-2.jpg
```

Then ask your agent (Claude Code reads `AGENTS.md` and the `paper-archive` skill) to transcribe the document,
or do the steps yourself:

```sh
python3 paper_archive.py template lab-report > data/documents/<id>/extraction.json   # fill in
python3 paper_archive.py validate <id>
python3 paper_archive.py review <id>          # compare with the paper
python3 paper_archive.py approve <id> --by "Your Name"
python3 paper_archive.py map
python3 paper_archive.py load                 # needs PAPER_FHIR_* in .env
```

Python 3.11+, standard library only. Photos other than JPEG (HEIC, PNG) are converted with macOS `sips` or,
elsewhere, Pillow (plus pillow-heif for HEIC). A phone photo's orientation is kept, so pages show upright.

## Documentation

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules and commands for AI agents (and a good overview for people) |
| [docs/extraction-format.md](docs/extraction-format.md) | The transcription format |
| [docs/validation-rules.md](docs/validation-rules.md) | Every validation rule and what it catches |
| [docs/fhir-mapping.md](docs/fhir-mapping.md) | The FHIR mapping |
| [.agents/skills/](.agents/skills/) | `paper-archive` (taking a document in), `paper-catalog` (adding codes), `paper-health-analysis` (answering questions from Medplum) |

## Status

Works end to end: intake, validation, review, approval, FHIR, loading, and read-only questions
(`tools/medplum_agent.py`). It runs standalone or inside
[agentic-assistant](https://github.com/jollyjoker992/agentic-assistant), where papers can also be sent to
the assistant on Telegram. Not done yet: a standalone Medplum setup script (`medplum/setup.py` in the
other archives) and prescriptions.

## Privacy

`data/` holds scans and transcriptions of real people and is never committed. Tests and examples use made-up
documents only. The only network calls are `load` (to your Medplum), `tools/loinc_check.py` (LOINC
numbers to the NLM search API) and `tools/cvx_check.py` (downloads the CDC's CVX list; sends nothing). The agent that transcribes necessarily sees the document: use one you trust
with health records.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). LOINC codes and names are © Regenstrief Institute,
used under the LOINC licence. CVX vaccine codes are published by the CDC. Reporting a vulnerability: [SECURITY.md](SECURITY.md).

This software stores what a paper document says. It is not a medical device and gives no medical advice.
