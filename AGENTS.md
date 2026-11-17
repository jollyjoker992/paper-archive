# Agent guide: paper-archive

Paper medical documents (lab reports, ECGs, ultrasound, X-ray, endoscopy, clinic visit notes) become FHIR R4
resources in the family's Medplum. It is the third feeder next to garmin-archive and healthkit-archive and
follows their conventions: stable identifiers, verified LOINC codes, Medplum's own validator.

```
scans ─intake─▶ document.pdf ─(you transcribe)─▶ extraction.json ─validate─▶ review ─▶ owner approves ─map─▶ fhir.ndjson ─load─▶ Medplum
```

You read the paper and write `extraction.json`: what is printed, as strings. You never write FHIR. Numbers,
units, ranges, codes and resources are derived by code (`paperarchive/`), so every rule is in one place and
is tested. To take a document in, follow [`.agents/skills/paper-archive/SKILL.md`](.agents/skills/paper-archive/SKILL.md).

## Answering questions about the records: use Medplum, not `data/`

Questions about someone's lab results, reports or visits are answered from Medplum with the read-only
agent client, following [`.agents/skills/paper-health-analysis/SKILL.md`](.agents/skills/paper-health-analysis/SKILL.md)
(`python3 tools/medplum_agent.py people|documents|document|results|pdf`; credentials
`PAPER_FHIR_URL`, `PAPER_FHIR_AGENT_CLIENT_ID`, `PAPER_FHIR_AGENT_CLIENT_SECRET`). Medplum holds only
approved documents. Reading transcriptions in `data/` to answer a question needs the owner's explicit OK.

## Rules

- **Transcribe, do not interpret.** Every value is the printed string. Do not convert units, round, compute,
  fix typos, translate, or fill a field the paper leaves empty. What you cannot read goes into `uncertain`,
  never a best guess in the value.
- **Only the owner approves.** `approve` records that a person compared the review sheet with the paper.
  Run it only after the owner said so in this conversation, for that document, and pass their name in `--by`.
  Never approve to "unblock" a load, and never pass `--accept-warnings` unless the owner settled each warning.
- **Never guess a code.** A row gets a catalog key only when it is the same test (`catalog find`). No match:
  `analyte` / `study.key` is null and the result is stored with its printed name. To add a code, follow
  [`.agents/skills/paper-catalog/SKILL.md`](.agents/skills/paper-catalog/SKILL.md): every LOINC code is
  checked against the NLM API (`tools/loinc_check.py`); a code that only looks right is a defect.
- **One person per document, checked.** `--person` must be the person on the paper. A name, birth date, age
  or sex that does not match `people.toml` is an error on purpose; do not add an alias to make it pass
  unless the owner confirms the spelling is theirs.
- **Personal data.** `data/` (default data directory) holds scans and transcriptions of real people. Never
  commit it, never send it to an external service, never paste a real document into tests, fixtures, docs
  or examples: those use made-up people only (`tests/fixtures/`). `tools/loinc_check.py` sends LOINC numbers
  only.
- Never print or commit `.env` or any `PAPER_FHIR_*` secret. `load` writes to the family's Medplum: run it
  only when asked.
- **The same paper only once.** Intake refuses files already in the archive. A paper photographed or
  scanned again is caught by validation (`E-DUPLICATE-DOCUMENT`, `W-POSSIBLE-DUPLICATE`). Tell the owner
  which documents look the same; `remove` deletes a document with its scans, so run it only on the owner's
  word, for the id they chose.
- Errors are never bypassed by editing the transcription away from what the paper says. If the paper itself
  is wrong (hospital software prints `Chiều cao: 1600cm`), transcribe it as printed: the validator knows
  which fields may be dropped (`W-VITAL-UNUSABLE`) and which block the document.

## Commands

```sh
python3 paper_archive.py people                          # who the archive knows
python3 paper_archive.py intake --person <id> <files>    # page photos in reading order, or one PDF
python3 paper_archive.py template <type>                 # empty extraction: lab-report ecg ultrasound xray endoscopy visit
python3 paper_archive.py catalog find "<printed name>"   # catalog key for a test; --study for imaging/endoscopy
python3 paper_archive.py validate <doc> [--json]         # every rule; exit 1 on errors
python3 paper_archive.py review <doc> [--compact]        # the sheet the owner checks against the paper (--compact: for a chat)
python3 paper_archive.py approve <doc> --by "<owner>"    # owner's decision only
python3 paper_archive.py rebuild-pdf <doc>|all             # document.pdf again from the original scans
python3 paper_archive.py remove <full doc id>              # delete a document, e.g. a duplicate: owner's decision only
python3 paper_archive.py map [<doc>] [--force]           # approved documents -> FHIR NDJSON
python3 paper_archive.py load [--force]                  # -> Medplum (PAPER_FHIR_* in .env)
python3 paper_archive.py status [--json]                 # intake | extracted | approved | mapped | loaded
python3 -m unittest discover -s tests                    # offline tests, made-up documents
python3 tools/loinc_check.py                             # every catalog code against the NLM LOINC API
python3 tools/fhir_fixtures.py <dir>                     # the fixtures as FHIR NDJSON, for the validator
cd tools/fhir-validate && npm ci && node validate.mjs <ndjson|dir>   # Medplum's validator
```

Exit codes: `0` done, `1` validation errors or failed documents, `2` something the caller must fix
(unknown person, missing file, missing configuration). A document id may be shortened to a unique prefix.

## Where things are

| Part | Files |
|---|---|
| Format of a transcription | `docs/extraction-format.md`, schema in `paperarchive/extraction.py` (`SCHEMA`) |
| Validation rules (every code) | `docs/validation-rules.md`, `paperarchive/extraction.py` |
| FHIR mapping | `docs/fhir-mapping.md`, `paperarchive/fhir.py` |
| Catalog: tests, units, ECG, vitals, studies, note sections | `paperarchive/catalog/analytes.json`, `units.json`, `codes.json` |
| Archive layout, intake, approval, removal | `paperarchive/store.py`, PDF builder `paperarchive/pdf.py` |
| The same paper twice | `paperarchive/duplicates.py` (intake refuses known files in `store.py`) |
| Loader | `paperarchive/load.py` (transaction of conditional updates per document) |
| Skills | `.agents/skills/paper-archive/` (intake), `paper-catalog/` (codes), `paper-health-analysis/` (questions) (`.claude/skills` links here) |
| Read-only queries | `tools/medplum_agent.py` |
| Tests | `tests/test_paper_archive.py`, made-up documents in `tests/fixtures/` |

## Changing the code

- A new rule gets a code (`E-…` blocks, `W-…` needs the owner, `I-…` informs), a row in
  `docs/validation-rules.md` and a test that triggers it.
- A change to what `map` produces bumps `MAPPING_VERSION` (`paperarchive/__init__.py`) and
  `docs/fhir-mapping.md`; a catalog change bumps the catalog `version`. Either makes `map` rewrite every
  document. Validate the output with `tools/fhir-validate` before committing.
- A change to the transcription format changes `SCHEMA`, `docs/extraction-format.md`, `template` and the
  fixtures together. Once real documents exist in an archive, a breaking change needs a new format number
  and a migration: approvals are bound to the exact bytes of `extraction.json`.
- Identifier systems (`<base>/identifier/…`) never change after the first load.
- Standard library only (Python 3.11+). `sips` (macOS) or Pillow is needed only to take in non-JPEG photos.
