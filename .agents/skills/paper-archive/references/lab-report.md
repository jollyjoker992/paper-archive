# Transcribing a lab report (`lab-report`)

One entry in `results` per printed result row, in the order printed, across all pages.

```json
{"section": "SGPT (ALT)", "name": "Alanine aminotransferase", "analyte": "alt",
 "value": "35", "unit": null, "reference": "Nam<41 U/L Nữ <31U/L", "flag": "marked",
 "alt_value": "0.58", "alt_reference": "( Nữ<0,52 mckat/L Nam<0,68 mckat/L )", "page": 2}
```

| Field | What goes in |
|---|---|
| `section` | The panel or group heading the row is printed under (`HUYẾT HỌC`, `SGPT (ALT)`, `Nước tiểu 10 thông số (máy)`), without the times printed in it. It tells blood from urine and helps identify the test. |
| `name` | The row's test name exactly as printed, including any bracket: `Tỷ lệ BC trung tính (NEUT)`. |
| `value` | The result exactly as printed: `4.52`, `6,8`, `< 0,5`, `Âm tính`. |
| `unit` | Only when the report has a unit column (or prints the unit next to the value). Many Vietnamese reports print the unit inside the reference column instead: then leave `unit` out, it is read from `reference`. |
| `reference` | The whole printed normal-range cell, brackets and all: `( 3,8 - 5,5 x 10^12 /L )`, `Nam<41 U/L Nữ <31U/L`, `( Âm tính <1 COI )`. Do not pick the patient's sex yourself; the code does. |
| `flag` | The mark the paper puts on an abnormal value: `H`, `L`, `HH`, `LL`, `*`, `↑`, `↓`. If the paper only prints the value in colour or bold, write `marked`. No mark: leave out. Never add a flag because the value is outside the range. |
| `alt_value`, `alt_reference` | A second result column in another unit system (`Đơn vị ít dùng`). It is the same measurement, so it is kept with the row, not as a second row. |
| `analyte` | The catalog key, or null. See below. |
| `specimen`, `method`, `note` | Only if printed for the row. |
| `page` | Page of the document the row is on. |

Document level: `dates.collected` is the sampling time, `dates.reported` the (latest) approval or signing
time. When each panel prints its own times, use the earliest sampling and the latest approval and say so in
`notes`. `document.document_number` is the report or sample id (`SID`, `ID`), `patient.patient_number` the
patient id.

## Choosing `analyte`

```sh
python3 paper_archive.py catalog find "Tỷ lệ BC trung tính"
```

- Use the key whose entry is this test. Read the `use:` line when there is one: it tells apart tests that
  look alike (glucose vs fasting glucose, urea vs BUN, LDL calculated vs direct, total vs ionised calcium,
  CRP vs hs-CRP, FT4 vs total T4).
- The same word is a different test in another specimen: `Hồng cầu`, `Bạch cầu`, `Glucose`, `Protein`,
  `Bilirubin` under a urine heading are the `urine-…` entries. The validator refuses a blood key under a
  urine section and the other way round (`E-DOMAIN`).
- You do not choose between mass and mole codes: the unit decides (`mg/dL` vs `mmol/L`).
- Percent and count are different entries: `NEU%`/`Tỷ lệ` is `neut-pct`, `NEU#`/`Số lượng` is `neut-abs`.
- An entry that says it is known but not coded (`rdw-sd`) is still the right key: use it.
- Not in the catalog: `analyte` null. The row is stored with its printed name. Tell the owner which rows
  those are; adding them is the `paper-catalog` skill, not something to improvise during intake.

## Things reports do

- **Qualitative results**: `Âm tính`, `Dương tính`, `Negative`, `Bình thường` go in `value` as printed.
- **Index with a rule** (`0.42` with reference `Âm tính <1 COI`): value `0.42`, reference as printed. The
  interpretation is taken from the printed rule.
- **Decimal marks** differ between columns on the same page (values `4.52`, ranges `3,8 - 5,5`): keep each
  as printed.
- **A heading at the bottom of a page with its row on the next**: the row goes with that heading.
- **Calculated rows, ratios, comments** printed in the table are rows too. Free-text comments under the
  table go to `notes`.

## Before you validate, check yourself

- Count the rows on each page against your rows.
- Re-read every flagged or coloured value and every value with three or more digits.
- The validator will check the blood count arithmetic (differential sums to 100, MCV = HCT/RBC, MCH, MCHC,
  absolute counts = WBC × %): a `W-CROSS-CHECK` almost always means a mis-read digit.
