# Transcribing a procedure or operation note (`procedure`)

A report of something that was done to treat, not to examine (`TƯỜNG TRÌNH PHẪU THUẬT - THỦ THUẬT`,
`PHIẾU PHẪU THUẬT / THỦ THUẬT`): an abscess opened, a wound sutured, a mole removed, an operation. An
endoscopy, an ultrasound or an X-ray report is a `study`, also when a biopsy was taken during it.

```json
"staff": {"performed_by": "Bs Lê Thị Mẫu", "signed_by": "Bs Nguyễn Văn Mẫu"},
"dates": {"performed": "2026-10-20T09:15", "reported": "2026-10-20"},
"procedure": {
  "name": "Incision of Abscess < 5cm",
  "diagnosis": "Áp xe da cẳng tay Phải",
  "postoperative_diagnosis": null,
  "anesthesia": null,
  "description": "Sát trùng bề mặt, tiêm tê tại chỗ, xẻ rạch ổ áp xe, …",
  "advice": "Thay băng hằng ngày. Tái khám sau 3 ngày."
}
```

| Field | Printed |
|---|---|
| `name` | The name of the procedure: the line under the title, or `Phương pháp phẫu thuật / thủ thuật`. As printed, in the language printed. |
| `diagnosis` | `Chẩn đoán`, or `Chẩn đoán trước phẫu thuật` when the paper has two. |
| `postoperative_diagnosis` | `Chẩn đoán sau phẫu thuật`, only when the paper prints it as its own line. |
| `anesthesia` | `Phương pháp vô cảm`, only when it has its own line. Anaesthesia mentioned inside the description stays there. |
| `description` | The whole `MÔ TẢ` / `Trình tự phẫu thuật` block, every line, in order. |
| `advice` | `DẶN DÒ`, if it has text. An empty heading is left out. |

- `document.title` is the form's title (`TƯỜNG TRÌNH PHẪU THUẬT - THỦ THUẬT`); the procedure's own name goes
  in `name`. Decoration under it (`*******`) and a stamp over it are not transcribed.
- `staff.performed_by` is the doctor who did it (`Bác sĩ thực hiện`, `Phẫu thuật viên`); `staff.signed_by` the
  one who countersigned (`Bác sĩ Trưởng khoa`). Other team members (assistant, anaesthetist, nurse) have no
  field: quote them in `notes`.
- `dates.performed` is the printed time of the procedure (`Ngày làm thủ thuật`); `dates.reported` the date at
  the signature.
- A printed `ID` that is not labelled as the patient's number goes whole into `document.document_number`.
  Do not split it.
- The diagnosis is stored as text. Copy it whole; do not translate it or add a code. There is no catalog
  key for procedures: the name is stored as printed.
