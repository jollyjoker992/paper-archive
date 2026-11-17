# Transcribing a vaccination document (`vaccination`)

Three kinds of paper, one format:

| Paper | Looks like | Dates |
|---|---|---|
| Pre-shot slip | `PHIẾU CHỈ ĐỊNH TIÊM`, `PHIẾU KHÁM SÀNG LỌC TRƯỚC TIÊM CHỦNG`: the vaccines ordered for today, often screening vitals and a conclusion (`Đủ điều kiện tiêm chủng ngay`, `Tạm hoãn tiêm`) | `dates.performed` = the slip's date and time; doses have no `date` of their own |
| Booklet or card | `SỔ TIÊM CHỦNG`, `PHIẾU TIÊM CHỦNG` with a table of rows over years | each dose has its own `date`; `dates` may be left out entirely |
| Certificate | `GIẤY XÁC NHẬN TIÊM CHỦNG` | each dose's `date`; `dates.reported` = the date it was issued |

```json
"vitals": {"temperature": "36,7", "pulse": "82", "weight": "54"},
"vaccination": {
  "screening": "Đủ điều kiện tiêm chủng ngay",
  "doses": [
    {"vaccine": "Gardasil 9 (0,5ml)", "key": "hpv9", "disease": "HPV", "dose_number": "Mũi 2",
     "lot": "X012345", "expiry": "2027-08", "manufacturer": "MSD", "route": "Tiêm bắp", "site": "Cánh tay trái",
     "dose_quantity": "0,5 ml", "given_by": "ĐD. …", "next_due": "2027-04-02", "given": "yes"}
  ]
}
```

| Field | Printed as |
|---|---|
| `vaccine` | `Tên vắc xin`, the whole cell (product name, presentation): `Gardasil 9 (0,5ml)` |
| `disease` | `Phòng bệnh` |
| `dose_number` | `Mũi`, `Mũi thứ`, `Liều`: as printed (`Mũi 2`, `2`, `Nhắc lại`) |
| `date` | `Ngày tiêm` of a booklet row |
| `lot`, `expiry` | `Số lô`, `Hạn dùng` (`08/2027` → `2027-08`, `15/08/2027` → `2027-08-15`) |
| `manufacturer`, `route`, `site`, `dose_quantity` | `Nhà sản xuất`, `Đường tiêm`, `Vị trí tiêm`, `Liều lượng` |
| `country` | `Nước sản xuất` / `MFR` when it holds a country (`Pháp`, `Hàn Quốc`): never `manufacturer` |
| `given_by` | the vaccinator's printed or stamped name (`Người tiêm`, `ĐD.`) |
| `facility` | a booklet row's place when it is not the document's facility |
| `next_due` | `Hẹn tiêm mũi tiếp theo`, `Ngày hẹn` for this vaccine |
| `staff.ordered_by` | the doctor who did the screening / signed the order |

- **Key**: `python3 paper_archive.py catalog find "<printed name>" --vaccine`. Take the key only when it is the
  same vaccine (`Gardasil 9` is `hpv9`, not `hpv4`). Influenza, hepatitis B and others deliberately use
  CVX's "unspecified" code; Hexaxim and Infanrix Hexa are deliberately uncoded. Never key a dose from a
  generic phrase (`5 trong 1`, `6 trong 1`): set `key` null and keep the printed name.
- A row that is an appointment only (a booklet's `Ngày hẹn`, or a bare date written under a dose) is the
  `next_due` of the previous dose of that vaccine, not a dose. An appointment that names another vaccine with no
  dose on the paper (`23-10-25 SXH` under a flu row) goes into `due`:
  `{"vaccine": "SXH", "key": "dengue", "date": "2025-10-23"}`. When it is unclear which dose a bare date
  belongs to, ask the owner.
- Screening vitals go into `vitals` as on every document.

## Vaccination centre cards and booklets (`PHIẾU TIÊM CHỦNG / VACCINATION CARD`, `LỊCH SỬ TIÊM CHỦNG`)

Columns: `Phòng bệnh` | `Mũi tiêm` | `Ngày tiêm, uống` | `Tên vắc xin` | `Liều lượng (ml)` | `Lô – hạn sử dụng` |
`Nước sản xuất` | `Bác sĩ chỉ định` | `Đường và vị trí tiêm` | `KTV tiêm`. Mostly handwritten, with a stamped
date and a round `ĐÃ TIÊM` stamp.

- **Patient**: the printed sticker (name, birth date, barcode number) is the patient block; the barcode number
  under the name goes into `patient.patient_number`.
- **Disease**: a booklet's row heading (`3. BẠCH HẦU, UỐN VÁN, HO GÀ, BẠI LIỆT`) is the printed disease, even when
  the vaccine covers fewer diseases (Td).
- **Dose number**: a handwritten `nhắc` over a printed `Mũi 1` is `Nhắc`; the printed label is not the value.
- **Dates**: write `YYYY-MM-DD` (`23-09-2025` → `2025-09-23`, `5-10-21` → `2021-10-05`). A two-digit year or a
  stamp with the year written over is an `uncertain` entry if it could be read two ways.
- **Amount**: `Liều lượng (ml)` with `0,5` in the cell: `dose_quantity` `0,5` and `vaccination.dose_unit` `ml`.
  A name and amount run together (`Vaxigrip Tetra 0,5ml`) is split by column, as the paper's lines fall.
- **Lot and expiry** share one cell: the lot first, the expiry after it (`2/26` → `2026-02`). Handwritten lots
  are often unreadable; put what you can read in `lot` and the doubt in `uncertain`, never a guess.
  `W-DOSE-EXPIRED` usually means one of the two was misread.
- **Country**: `Nước sản xuất` (`Pháp`) is `country`.
- **Physician / vaccinator**: usually signatures; leave them out unless a name is legible.
  `Đường và vị trí tiêm` (`TB`, `TBTT`) goes whole into `route`.
- **`ĐÃ TIÊM` stamp**: copy it into the dose `note` (`Dấu: ĐÃ TIÊM`) and mention it when asking the owner, but it
  does not set `given`.
- **Free text under the table** (`XN: HbsAg, Anti HbsAg`, advised tests) goes into `notes`, as printed.
- A loose card is meant to be attached to the booklet: if the same shot is also written in the booklet, tell
  the owner. Both documents would put it in the history (see "The same shot twice" below).

## `given`: ask the owner, for each dose

The paper does not say whether the shot happened: a slip is printed before it, and a filled-in lot number or
signature is still not the owner's word. After `validate`, show the doses (`review --compact`) and ask:

> Were these given? 1. Gardasil 9, Mũi 2 (2026-10-02) 2. Vaxigrip Tetra, Mũi 1 (2026-10-02): yes / no / don't know

Write the answer into each dose: `yes` (an `Immunization`, completed), `no` (an `Immunization`, not-done;
the reason the owner gives goes in `note`), `unknown` (stored only as the order). On a booklet the owner may
answer for all rows at once ("all of them were given"); take that only when they say it. If the owner says a
slip's vaccine was given on another day than printed, note that in `notes` and ask how to record it; do not
change the printed date.

`E-DOSE-UNCONFIRMED` blocks approval until every dose has an answer.

## The same shot twice, a booklet photographed again

Each document stores its own doses. A shot written both on a loose card and in the booklet
(`W-DOSE-ELSEWHERE`), or a booklet photographed again after new rows were added (`W-POSSIBLE-DUPLICATE`),
would put the same dose in the history twice. Tell the owner which documents and rows. For a re-photographed
booklet the owner decides which document to keep (`remove` the other, on their word). A shot on two different
papers may stay on both when the owner accepts the warning; never answer `given: no` for a shot that was given
to hide the second copy.
