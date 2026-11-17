# Transcribing ultrasound, X-ray and endoscopy reports (`ultrasound`, `xray`, `endoscopy`)

All three use the `study` block. Echocardiography is an `ultrasound`.

```json
"study": {
  "key": "us-abdomen",
  "procedure": "SIÊU ÂM Ổ BỤNG TỔNG QUÁT",
  "indication": null,
  "technique": "Đầu dò convex 3.5MHz",
  "findings": "Gan: kích thước bình thường, nhu mô tăng âm nhẹ, bờ đều.\nTúi mật: thành mỏng, không sỏi.",
  "measurements": [],
  "impression": "- Gan nhiễm mỡ độ I.",
  "recommendation": null
}
```

| Field | What goes in |
|---|---|
| `procedure` | The name of the examination as printed in the title. |
| `key` | Catalog key of the study (`paper_archive.py catalog find --study "siêu âm tuyến giáp"`), or null. Take a specific key only when the paper states what makes it specific: `xr-chest-pa-upright` needs "đứng" and "sau - trước" (or "Chest PA") on the paper; otherwise `xr-chest`. |
| `indication` | Reason for the examination, if printed. |
| `technique` | Probe, body part, position, exposure (`THÔNG SỐ KỸ THUẬT`), anaesthesia: as printed. |
| `findings` | The whole description block (`MÔ TẢ`), every line, in order, with its bullets. Do not shorten "normal" organs away and do not reorder. Measurements written inside it stay in it too. |
| `impression` | The conclusion (`KẾT LUẬN`) as printed. |
| `recommendation` | `LỜI KHUYÊN` / `ĐỀ NGHỊ`, if it has text. |
| `measurements` | See below. Only for reports that print named quantities, typically echocardiography. |

- The pictures printed on the report are not transcribed and never described from your own reading: you do
  not report what an X-ray film or an ultrasound frame shows. Say in `notes` that they are there
  ("Kèm 3 ảnh siêu âm", "Trang 2 là phim in").
- Numbers burned into a picture (device overlays) are not transcribed. If they differ from the report text,
  the report text is the record; mention the difference in `notes`.
- `dates.performed`: `Ngày khám` / `Ngày chụp`, with the time if printed. `dates.reported`: the signing
  date when it is a different day.
- `staff.performed_by` / `signed_by`: the doctor named at the signature (`Bác sĩ thực hiện`,
  `Bác sĩ chuyên khoa`).

## Measurements (echocardiography and similar)

One row per named quantity, same shape as a lab row:

```json
{"name": "EF", "analyte": "echo-lvef", "value": "66", "unit": "%", "reference": "(bt: >55%)"},
{"name": "LVMI", "analyte": "echo-lvmi", "value": "70", "unit": "g/m2", "reference": "(bt: <120@nam)"},
{"name": "EDT", "analyte": "echo-edt", "value": "190", "reference": "(bt: <220@41 tuổi)"}
```

- `name` is the abbreviation or label as printed; `unit` the unit printed after the value; if none is
  printed, leave it out (do not supply the usual one).
- `reference` is the printed normal range with its brackets and conditions (`bt:` = bình thường).
- `LA / AO = 30 mm / 28 mm` is two rows.
- `analyte`: `catalog find` as for lab rows. Echo entries exist for the usual M-mode and Doppler
  quantities; several are known but deliberately not coded (the key still applies). Variants are different
  quantities: `FS`, `Midwall FS`, `Stress-FS` each have their own entry.
- A size mentioned in a sentence ("nang kích thước # 12 mm") stays in `findings`; it is not a measurement row.
