# Transcribing an ECG (`ecg`)

```json
"ecg": {
  "measurements": {"heart_rate": "72", "pr_interval": "156", "qrs_duration": "92", "qt_interval": "384",
                   "qtc_interval": "421", "p_axis": "48", "qrs_axis": "-12", "t_axis": "35", "rr_interval": "833"},
  "leads": 12,
  "rhythm": "Nhịp xoang",
  "machine_interpretation": "Sinus rhythm. Normal ECG.",
  "conclusion": "Nhịp xoang đều 72 ck/ph.\nTrục trung gian.",
  "device": "10mm/mV 25mm/s Filter: 25Hz"
}
```

- `measurements`: only the numbers the device or the doctor printed, as printed, without unit. The units are
  fixed: heart rate /min, intervals and durations in ms, axes in degrees. If the paper uses another unit
  (QT in seconds), do not convert: leave the measurement out, quote it in `notes` and add an `uncertain` entry.
- **Never measure the tracing yourself.** No intervals from the grid, no axis from the leads, no rhythm
  from the strip. A recording that prints only the heart rate has one measurement.
- `conclusion` is what the doctor wrote or signed (`Kết luận`). `machine_interpretation` is the device's
  automatic text. Keep them apart; fill only what exists.
- `rhythm` only when the paper has a separate rhythm line. A rhythm stated inside the conclusion stays in
  the conclusion.
- `leads`: how many leads the recording has, a number (1, 2, 3, 6, 12, 15), when the paper says it: a
  printed count (`12 chuyển đạo`, `12-lead`, `6L`, `Single lead`), or the strips' own lead labels counted
  (`I II III aVR aVL aVF V1-V6` → 12; one strip labelled `Lead I` → 1). A handheld recorder or a rhythm strip
  is usually one lead. Nothing printed about the leads: leave it out, never assume 12.
- `device`: the print settings line (gain, speed, filter) and device name if printed.
- **An Apple Watch ECG (the PDF exported from the Health app) is not taken in**: Apple Health already
  brings it, with the trace, from the person's iPhone. Tell the owner and stop. Other single-lead recorders
  (KardiaMobile, a clinic's handheld, a printed rhythm strip) are taken in as `ecg` with `leads: 1`.
- `dates.performed`: the recording time printed on the strip (`2026/10/02 PM 2:05` → `2026-10-02T14:05`).
- Things printed as placeholders (`* cm * kg */* mmHg`) are not values: mention them in `notes`.

The validator checks ranges, that RR fits the heart rate, that QTc fits QT at that rate, and that a one-lead
recording has no axis.
