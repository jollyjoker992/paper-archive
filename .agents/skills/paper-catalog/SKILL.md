---
name: paper-catalog
description: Add or change entries in paper-archive's terminology catalog - a lab test, a printed spelling, a unit, an echo measurement, an imaging or endoscopy study, a vaccine - with LOINC codes verified against the NLM API and CVX codes against the CDC list. Use when validation reports I-UNCODED, W-NAME-UNLISTED, W-VACCINE-UNLISTED, E-UNIT-UNKNOWN or E-UNIT-ANALYTE for something that is read correctly, or when the user asks to code a test or a vaccine.
---

# Changing the catalog

The catalog (`paperarchive/catalog/`) is the only place a code comes from. A wrong entry mislabels every
document that uses it, silently, so it is changed deliberately and never in the middle of an intake to make
a warning disappear. Ask the owner before adding codes; adding a printed spelling to an existing entry needs
only that the owner confirmed it is the same test.

| File | Content |
|---|---|
| `analytes.json` | Measured quantities: blood tests (`domain` absent = blood), urine strip (`urine`), echo (`echo`). |
| `units.json` | Printed unit → UCUM. Case matters (`G/L` ≠ `g/L`). |
| `codes.json` | Document types, ECG measurements, vital signs, narrative sections, studies. |
| `vaccines.json` | Vaccines → CVX codes, and vaccines deliberately not coded (`not_coded`, with the reason). |

## Add a printed spelling

Add it to the entry's `names`. Names are matched as whole words, without case or diacritics; when two
entries match, the longer name wins, which is how `Calcium ion hóa` beats `Calcium`. Check the new name does
not also describe another test (`catalog find "<name>"` must show only the entry you mean), then run the
tests.

## Add a unit

Add `"printed": "UCUM"` to `units.json`, with the key written as printed minus spaces and with `µ` as `u`.
The value must be a valid UCUM code (`10*9/L`, `umol/L`, `m[IU]/L`, `mm[Hg]`, `{COI}` for an arbitrary
index). If you are not sure of the UCUM spelling, look it up in the UCUM specification; do not improvise.

## Add a coded quantity

1. Pin down what was measured: component, specimen (serum/plasma, blood, urine), property (mass vs moles vs
   presence), method only if the paper states it. LOINC has one code per combination.
2. Find candidates from the NLM search API (only the search words leave the machine):
   `https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search?terms=<words>&df=LOINC_NUM,LONG_COMMON_NAME`
3. Take a code only when its long common name says exactly this. Prefer the code without a method when the
   paper names none. Never take: a deprecated code, a code for another specimen or modality, a method-specific
   code for a report that does not state the method, a panel code for a single result.
4. If nothing fits, add the entry with `"codes": []` and a `"reason"`: the quantity is then recognised,
   stored with its printed name, and nobody tries the same near miss again.
5. Write the entry (one line in `analytes.json`):

   ```json
   {"key": "magnesium", "group": "chemistry", "names": ["Magnesium", "Magie", "Mg"],
    "codes": [{"loinc": "2601-3", "display": "", "units": {"mmol/L": [0.2, 3]}},
              {"loinc": "19123-9", "display": "", "units": {"mg/dL": [0.5, 7]}}]}
   ```

   - one code per property; `units` lists every UCUM unit that code accepts, each with the range a living
     person can have in that unit. The range is a transcription guard (decimal slips, wrong unit), wide on
     purpose; it is not a reference range.
   - `"": [low, high]` for a quantity printed without unit (ratio, pH, specific gravity).
   - `"qualitative": true` for negative/positive results; add `"index": true` only if reports print a cutoff
     index with its rule.
   - `"domain"` for urine or echo; `"use"` whenever another entry could be confused with this one.
6. `python3 tools/loinc_check.py --fix` writes the official display for every code and fails on unknown or
   deprecated ones. Read each display it prints for your new codes: that is the moment a near miss shows.
7. Bump `version` in the file, add a fixture row and a test if the entry introduces a new behaviour, run
   `python3 -m unittest discover -s tests`, and `map` (every document is mapped again when the catalog
   version changes).

## Add a study

Entries in `codes.json` → `studies`: `key`, `type` (`ultrasound`, `xray`, `endoscopy`), `loinc`, `names`, and
`use` when a more general entry exists. Same verification. A study that has no exact code stays unkeyed: it
is stored under the generic code of its document type with its printed name.

## Add a vaccine

Entries in `vaccines.json` → `vaccines` (one per line): `key`, `cvx`, `display`, `names` (printed product and
vaccine names), `diseases`, and `use` when the choice of code needs explaining.

1. Find candidates in the CDC CVX list (`https://www2a.cdc.gov/vaccines/iis/iisstandards/downloads/cvx.txt`,
   pipe-separated: code, short description, full name, notes, status, non-vaccine). Read the **full name**:
   it often names the product (146 is Vaxelis, PRP-OMP), which makes it a near miss for another product with
   the same antigens.
2. Take a code only when its full name describes this vaccine. Non-US codes (170 Pentaxim, 198 DTwP-HepB-Hib)
   and inactive codes are valid. When the printed name cannot tell formulations apart (hemisphere, valency,
   adult vs paediatric dose), use the "unspecified formulation" code of that vaccine. When nothing fits, add it
   to `not_coded` with `names` and a `reason`.
3. A name is matched as whole words: never add a phrase that fits two vaccines (`5 trong 1`), and check
   `catalog find "<name>" --vaccine` shows only the entry you mean.
4. `python3 tools/cvx_check.py --fix` writes CVX's short description as the display and fails on unknown
   codes or non-vaccines. Read the full name it prints for your new codes.
5. Bump `version` in `vaccines.json`, run the tests, and `map`.

