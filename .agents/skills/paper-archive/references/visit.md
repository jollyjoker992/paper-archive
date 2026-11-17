# Transcribing a clinic visit note (`visit`)

A consultation result (`KẾT QUẢ KHÁM BỆNH`, `PHIẾU KHÁM BỆNH`): vital signs, history, examination,
conclusion, advice.

```json
"vitals": {"pulse": "70", "blood_pressure": "116/74", "temperature": "36.8", "height": "160", "weight": "54", "bmi": "21.09"},
"visit": {
  "reason": "…",            "medications": "không",   "family_history": "Bình thường.",
  "past_history": "…",      "allergies": "Chưa.",     "examination": "…",
  "tests": "- Không thực hiện", "diagnosis": "…",     "advice": "1. Thuốc: …\n2. Chế độ dinh dưỡng: …"
}
```

| Field | Printed section |
|---|---|
| `reason` | `LÝ DO ĐI KHÁM` |
| `medications` | `Thuốc đang điều trị` |
| `family_history` | `Tiền sử gia đình` |
| `past_history` | `Tiền sử bản thân` |
| `allergies` | `Dị ứng` |
| `examination` | `KHÁM LÂM SÀNG` (the whole block) |
| `tests` | `CẬN LÂM SÀNG` |
| `diagnosis` | `KẾT LUẬN` / `CHẨN ĐOÁN` |
| `advice` | `LỜI KHUYÊN` / `HƯỚNG ĐIỀU TRỊ`: all numbered items, as one text |

- Each field is the section's text exactly as printed, including answers that say nothing (`không`,
  `Chưa.`, `Bình thường.`): "no known allergy" written by a doctor is information. A section that is absent
  or blank is left out.
- Typos stay; mention an obvious one in `notes`.
- `diagnosis` is stored as the encounter diagnosis. Copy it whole; do not split it, translate it or add a
  code.
- `dates.performed` is the visit time (`Thời gian khám bệnh`). If that field is blank, use the date at the
  signature and say so in `notes`.
- A prescription printed on the same sheet is not part of this format yet: quote it in `notes` and tell the
  owner it was not structured.
