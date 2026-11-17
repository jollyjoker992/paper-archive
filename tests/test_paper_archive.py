"""Offline tests: made-up documents only (tests/fixtures). Run: python3 -m unittest discover -s tests -v"""
import copy
import datetime as dt
import json
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from paperarchive import DEFAULT_BASE, catalog, cli, duplicates, extraction, fhir, load, pdf, store  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
TODAY = dt.date(2026, 11, 17)
PERSON = {"id": "example", "name": "Trần Thị Ví Dụ", "aliases": ["TRAN THI VI DU"], "birth_date": "1985-03-14",
          "sex": "female", "timezone": "Asia/Ho_Chi_Minh"}
FAKE_JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x40\x00\x30\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def codes(issues, level=None):
    return sorted({i["code"] for i in issues if level is None or i["level"] == level})


def check(doc):
    return extraction.validate(doc, PERSON, TODAY)


class Parsing(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(extraction.parse_number("6,8")["number"], 6.8)
        self.assertEqual(extraction.parse_number("< 0,5")["comparator"], "<")
        self.assertEqual(extraction.parse_number("≥ 90")["comparator"], ">=")
        self.assertEqual(extraction.parse_number("-12")["number"], -12)
        self.assertTrue(extraction.parse_number("1.250")["ambiguous"])
        self.assertFalse(extraction.parse_number("0.250")["ambiguous"])
        for text in ("5.4 mmol", "Âm tính", "1,2,3", "5-6", ""):
            self.assertIsNone(extraction.parse_number(text), text)

    def test_references(self):
        self.assertEqual(extraction.parse_reference("4,0 - 10,0"), {"low": 4.0, "high": 10.0})
        self.assertEqual(extraction.parse_reference("3.9–6.4"), {"low": 3.9, "high": 6.4})
        self.assertEqual(extraction.parse_reference("< 5,2"), {"high": 5.2})
        self.assertEqual(extraction.parse_reference("≥ 60"), {"low": 60.0})
        self.assertIsNone(extraction.parse_reference("Nữ: 44 - 80"))           # sex not known: text only
        self.assertIsNone(extraction.parse_reference("xem ghi chú"))

    def test_references_as_hospitals_print_them(self):
        ref = extraction.parse_reference
        self.assertEqual(ref("( 3,8 - 5,5 x 10^12 /L )"), {"low": 3.8, "high": 5.5, "unit": "x 10^12 /L"})
        self.assertEqual(ref("( 4- 10 x 10^9 /L )"), {"low": 4.0, "high": 10.0, "unit": "x 10^9 /L"})
        self.assertEqual(ref("( 6.5-11fL)"), {"low": 6.5, "high": 11.0, "unit": "fL"})
        self.assertEqual(ref("( <7.7mmol/L)"), {"high": 7.7, "unit": "mmol/L"})
        self.assertEqual(ref("Nam<41 U/L Nữ <31U/L", "male"), {"high": 41.0, "unit": "U/L", "sex": "male"})
        self.assertEqual(ref("Nam<41 U/L Nữ <31U/L", "female"), {"high": 31.0, "unit": "U/L", "sex": "female"})
        self.assertEqual(ref("(Nữ: 143 - 399 µmol/L, Nam: 202 - 416 µmol/L)", "male"),
                         {"low": 202.0, "high": 416.0, "unit": "µmol/L", "sex": "male"})
        self.assertEqual(ref("(Nữ < 5,7 ; Nam < 7 mg/dl)", "female"), {"high": 5.7, "unit": "mg/dl", "sex": "female"})
        self.assertEqual(ref("( Âm tính <1 COI )"), {"meaning": "NEG", "high": 1.0, "unit": "COI"})
        self.assertEqual(ref("( Âm tính )"), {"expect": "NEG"})
        self.assertEqual(ref("(bt:<101ms@41 tuổi)"), {"high": 101.0, "unit": "ms"})
        self.assertEqual(ref("(bt: <120@nam)"), {"high": 120.0})
        self.assertEqual(ref("(bt: 28-44%)"), {"low": 28.0, "high": 44.0, "unit": "%"})
        self.assertEqual(ref("( 1,005 - 1,030 )"), {"low": 1.005, "high": 1.03})

    def test_units_are_case_sensitive(self):
        self.assertEqual(catalog.ucum("G/L"), "10*9/L")
        self.assertEqual(catalog.ucum("g/L"), "g/L")
        self.assertEqual(catalog.ucum("µmol/L"), "umol/L")
        self.assertEqual(catalog.ucum("μIU/mL"), "u[IU]/mL")
        self.assertIsNone(catalog.ucum("furlongs"))

    def test_names(self):
        entries = catalog.analytes()
        self.assertTrue(extraction.name_matches("WBC (Số lượng bạch cầu)", entries["wbc"]))
        self.assertFalse(extraction.name_matches("RDW-SD", entries["rdw-cv"]))
        self.assertFalse(extraction.name_matches("RDW_SD", entries["rdw-cv"]))
        self.assertFalse(extraction.name_matches("Stress-FS", entries["echo-fs"]))
        self.assertGreater(extraction.name_score("Calcium ion hóa", entries["calcium-ionized"]),
                           extraction.name_score("Calcium ion hóa", entries["calcium"]))
        self.assertFalse(extraction.name_matches("hs-CRP", entries["crp"]))
        self.assertFalse(extraction.name_matches("NEU%", entries["neut-abs"]))


class Rules(unittest.TestCase):
    def test_fixtures_are_clean(self):
        for name in ("lab-report", "lab-report-columns", "ecg", "ultrasound", "xray", "echo", "visit", "vaccination",
                     "vaccination-booklet", "vaccination-card"):
            issues, _ = check(fixture(name))
            self.assertEqual(codes(issues, "error") + codes(issues, "warning"), [], name)
        self.assertEqual(codes(check(fixture("endoscopy"))[0], "warning"), ["W-VITAL-UNUSABLE"])

    def columns(self, index=None, **changes):
        doc = fixture("lab-report-columns")
        if index is not None:
            doc["results"][index].update(changes)
        return doc

    def test_report_without_unit_column(self):
        _, parsed = check(self.columns())
        rows = {r["slug"]: r for r in parsed["results"]}
        self.assertEqual((rows["rbc"]["ucum"], rows["rbc"]["unit_text"]), ("10*12/L", "x 10^12 /L"))
        self.assertEqual(rows["mcv"]["flag"], "L")                        # marked in colour, below the range
        self.assertEqual(rows["alt"]["flag"], "H")                        # 35 against the female range < 31
        self.assertEqual(rows["alt"]["reference"]["high"], 31.0)
        self.assertEqual(rows["alt"]["alt_value"], "0.58")
        self.assertEqual(rows["uric-acid"]["reference"], {"low": 143.0, "high": 399.0, "unit": "µmol/L", "sex": "female"})
        self.assertIsNone(rows["rdw-sd"]["code"])                         # known to the catalog, deliberately not coded
        self.assertEqual(rows["rdw-sd"]["ucum"], "fL")
        self.assertEqual((rows["hbsag"]["kind"], rows["hbsag"]["flag"], rows["hbsag"]["ucum"]), ("quantity", "NEG", "{COI}"))
        self.assertEqual(rows["urine-specific-gravity"]["code"]["loinc"], "5811-5")
        self.assertEqual(rows["urine-blood"]["code"]["loinc"], "5794-3")  # 'Hồng cầu' under the urine heading
        self.assertEqual(rows["urine-urobilinogen"]["flag"], "N")
        male = extraction.validate(self.columns(), {**PERSON, "sex": "male"}, TODAY)
        self.assertIn("E-PATIENT-SEX", codes(male[0]))

    def test_rows_in_the_wrong_place(self):
        self.assertIn("E-DOMAIN", codes(check(self.columns(9, analyte="rbc"))[0]))             # blood count under urine
        self.assertIn("W-ANALYTE-AVAILABLE", codes(check(self.columns(9, analyte=None))[0]))
        self.assertIn("W-NAME-BETTER", codes(check(self.columns(5, analyte="calcium"))[0]))
        self.assertIn("W-FLAG-CONFLICT", codes(check(self.columns(0, flag="marked"))[0]))      # marked but in range
        self.assertIn("W-FLAG-MISSING", codes(check(self.columns(1, flag=None))[0]))
        self.assertIn("W-INDEX-OUTSIDE", codes(check(self.columns(6, value="1.80"))[0]))
        self.assertIn("E-VALUE-NOT-QUALITATIVE", codes(check(self.columns(6, reference=None))[0]))
        self.assertIn("E-UNIT-MISSING", codes(check(self.columns(0, reference=None))[0]))
        self.assertIn("E-IMPLAUSIBLE", codes(check(self.columns(7, value="1.500"))[0]))

    def test_vitals_and_age(self):
        _, parsed = check(fixture("endoscopy"))
        self.assertEqual(sorted(parsed["vitals"]), ["blood_pressure", "pulse", "weight"])       # 1600 cm and BMI 0.3 dropped
        doc = fixture("visit")
        doc["vitals"].update(bmi="25.0", blood_pressure="74/116", temperature="NaN")
        issues, parsed = check(doc)
        self.assertEqual([i["path"] for i in issues], ["vitals.blood_pressure", "vitals.temperature", "vitals.bmi"])
        self.assertEqual(sorted(parsed["vitals"]), ["height", "pulse", "weight"])
        doc = fixture("echo")
        doc["patient"]["age"] = 52
        self.assertIn("E-PATIENT-AGE", codes(check(doc)[0]))
        doc["patient"]["age"] = 42                                         # neither full years (41) nor year minus year (41)
        self.assertIn("E-PATIENT-AGE", codes(check(doc)[0]))

    def test_study_measurements(self):
        issues, parsed = check(fixture("echo"))
        rows = {r["slug"]: r for r in parsed["measurements"]}
        self.assertEqual(rows["echo-lvef"]["code"]["loinc"], "18043-0")
        self.assertEqual((rows["echo-edt"]["ucum"], rows["echo-edt"]["reference"]), ("ms", {"high": 220.0}))
        self.assertIn("I-UNIT-ASSUMED", codes(issues))
        self.assertIsNone(rows["echo-midwall-fs"]["code"])
        doc = fixture("echo")
        doc["study"]["measurements"][2]["value"] = "660"
        self.assertIn("E-IMPLAUSIBLE", codes(check(doc)[0]))
        doc = fixture("echo")
        doc["study"]["measurements"][0]["analyte"] = "hgb"
        self.assertIn("E-DOMAIN", codes(check(doc)[0]))

    def lab(self, index=None, **changes):
        doc = fixture("lab-report")
        if index is not None:
            doc["results"][index].update(changes)
        return doc

    def test_decimal_slip_and_wrong_unit(self):
        self.assertIn("E-IMPLAUSIBLE", codes(check(self.lab(16, value="540"))[0]))
        self.assertIn("E-IMPLAUSIBLE", codes(check(self.lab(16, unit="mg/dL"))[0]))        # 5.4 mg/dL
        self.assertIn("E-UNIT-ANALYTE", codes(check(self.lab(2, unit="G/L"))[0]))          # haemoglobin in 10^9/L
        self.assertIn("E-UNIT-UNKNOWN", codes(check(self.lab(2, unit="g/cL"))[0]))
        self.assertIn("E-UNIT-MISSING", codes(check(self.lab(2, unit=None))[0]))
        self.assertIn("E-VALUE-NOT-NUMBER", codes(check(self.lab(2, value="13S"))[0]))

    def test_unit_picks_the_loinc_code(self):
        _, parsed = check(self.lab(16, value="97", unit="mg/dL", reference="70 - 115"))
        self.assertEqual(parsed["results"][16]["code"]["loinc"], "2345-7")
        _, parsed = check(self.lab())
        self.assertEqual(parsed["results"][16]["code"]["loinc"], "14749-6")

    def test_analyte_assignment(self):
        self.assertIn("E-ANALYTE-UNKNOWN", codes(check(self.lab(0, analyte="leuko"))[0]))
        self.assertIn("W-NAME-UNLISTED", codes(check(self.lab(0, analyte="plt"))[0]))
        self.assertIn("E-DUPLICATE", codes(check(self.lab(0, analyte="plt"))[0]))
        self.assertIn("W-ANALYTE-AVAILABLE", codes(check(self.lab(0, analyte=None))[0]))
        self.assertIn("E-VALUE-NOT-QUALITATIVE", codes(check(self.lab(26, value="0,31"))[0]))

    def test_flags(self):
        self.assertIn("W-FLAG-MISSING", codes(check(self.lab(17, flag=None))[0]))
        self.assertIn("W-FLAG-CONFLICT", codes(check(self.lab(16, flag="H"))[0]))
        self.assertIn("E-FLAG-UNKNOWN", codes(check(self.lab(16, flag="??"))[0]))
        _, parsed = check(self.lab())
        self.assertEqual(parsed["results"][26]["flag"], "NEG")

    def test_blood_count_arithmetic(self):
        self.assertIn("W-CROSS-CHECK", codes(check(self.lab(4, value="98,6"))[0]))         # MCV
        self.assertIn("W-CROSS-CHECK", codes(check(self.lab(9, value="68,2"))[0]))         # differential sum
        self.assertIn("W-CROSS-CHECK", codes(check(self.lab(14, value="8,96"))[0]))        # NEU# vs WBC x NEU%
        self.assertIn("W-CROSS-CHECK", codes(check(self.lab(2, value="185", reference="120 - 190"))[0]))

    def test_wrong_person_and_dates(self):
        doc = self.lab()
        doc["patient"]["name"] = "TRẦN THỊ KHÁC"
        self.assertIn("E-PATIENT-NAME", codes(check(doc)[0]))
        doc = self.lab()
        doc["patient"]["birth_year"] = 1958
        self.assertIn("E-PATIENT-BIRTH", codes(check(doc)[0]))
        doc = self.lab()
        doc["patient"]["sex"] = "male"
        self.assertIn("E-PATIENT-SEX", codes(check(doc)[0]))
        doc = self.lab()
        doc["dates"]["reported"] = "2026-10-01T10:42"
        self.assertIn("E-DATE-ORDER", codes(check(doc)[0]))
        doc["dates"] = {"collected": "2027-01-02"}
        self.assertIn("E-DATE-FUTURE", codes(check(doc)[0]))
        doc["dates"] = {"performed": "2026-10-02"}
        self.assertIn("E-DATE-MISSING", codes(check(doc)[0]))

    def test_schema(self):
        doc = self.lab()
        doc["results"][0]["value"] = 6.8                    # a number instead of the printed string
        doc["results"][1]["confidence"] = 0.9
        del doc["facility"]
        issues, _ = check(doc)
        self.assertEqual({i["path"] for i in issues}, {"results[0].value", "results[1].confidence", "facility"})
        doc = self.lab()
        doc["study"] = fixture("xray")["study"]
        self.assertIn("E-BODY", codes(check(doc)[0]))

    def test_uncertain_needs_the_owner(self):
        doc = self.lab()
        doc["uncertain"] = [{"path": "results[3].value", "reason": "smudged, 40,5 or 46,5"}]
        self.assertEqual(codes(check(doc)[0], "warning"), ["W-UNCERTAIN"])

    def test_ecg(self):
        doc = fixture("ecg")
        doc["ecg"]["measurements"].update(qtc_interval="481")
        self.assertIn("W-CROSS-CHECK", codes(check(doc)[0]))
        doc["ecg"]["measurements"].update(qtc_interval="421", heart_rate="720")
        self.assertIn("E-IMPLAUSIBLE", codes(check(doc)[0]))
        doc["ecg"]["measurements"].update(heart_rate="72 bpm")
        self.assertIn("E-VALUE-NOT-NUMBER", codes(check(doc)[0]))

    def test_imaging(self):
        doc = fixture("ultrasound")
        doc["study"]["key"] = "xr-chest"
        self.assertIn("E-STUDY-TYPE", codes(check(doc)[0]))
        doc["study"]["key"] = None
        self.assertIn("W-STUDY-AVAILABLE", codes(check(doc)[0]))
        del doc["study"]["impression"]
        self.assertIn("W-NO-CONCLUSION", codes(check(doc)[0]))


class Vaccination(unittest.TestCase):
    def dose(self, index=0, name="vaccination", **changes):
        doc = fixture(name)
        for key, value in changes.items():
            if value is None:
                doc["vaccination"]["doses"][index].pop(key, None)
            else:
                doc["vaccination"]["doses"][index][key] = value
        return check(doc)

    def test_given_is_the_owners_answer(self):
        issues, parsed = self.dose(given=None)
        self.assertEqual(codes(issues, "error"), ["E-DOSE-UNCONFIRMED"])
        self.assertEqual([d["given"] for d in parsed["doses"]], [None, "unknown"])
        self.assertEqual(codes(self.dose(given="probably")[0], "error"), ["E-SCHEMA"])
        self.assertIn("I-DOSE-ORDERED", codes(check(fixture("vaccination"))[0], "info"))

    def test_vaccine_assignment(self):
        self.assertEqual(codes(self.dose(key="hpv10")[0], "error"), ["E-VACCINE-UNKNOWN"])
        issues, _ = self.dose(key="hpv4")                               # 'Gardasil 9' is not Gardasil (4)
        self.assertEqual(codes(issues, "warning"), ["W-VACCINE-UNLISTED"])
        self.assertIn("'hpv9' better", next(i["message"] for i in issues if i["code"] == "W-VACCINE-UNLISTED"))
        self.assertEqual(codes(self.dose(key="pcv13")[0], "warning"), ["W-VACCINE-UNLISTED"])
        self.assertEqual(codes(self.dose(key=None, vaccine="Prevenar 13")[0], "warning"), ["W-VACCINE-AVAILABLE"])
        issues, parsed = self.dose(key=None, vaccine="Vắc xin Ví Dụ", disease="")
        self.assertEqual(codes(issues, "warning"), [])
        self.assertIn("I-UNCODED", codes(issues, "info"))
        self.assertIsNone(parsed["doses"][0]["code"])
        _, parsed = check(fixture("vaccination-booklet"))
        self.assertEqual([d["code"] and d["code"]["cvx"] for d in parsed["doses"]], ["35", "35", "45", None])

    def test_dates(self):
        self.assertEqual(codes(self.dose(date="2027-01-05")[0], "error"), ["E-DATE-FUTURE"])
        self.assertEqual(codes(self.dose(1, date="2027-01-05")[0], "error"), [])      # an order for a later day
        self.assertEqual(codes(self.dose(date="1980-01-01")[0], "error"), ["E-DATE-BEFORE-BIRTH"])
        self.assertEqual(codes(self.dose(next_due="2026-09-01")[0], "error"), ["E-DATE-ORDER"])
        self.assertEqual(codes(self.dose(expiry="08/2027")[0], "error"), ["E-SCHEMA"])
        self.assertEqual(codes(self.dose(expiry="2027-13")[0], "error"), ["E-SCHEMA"])
        _, parsed = check(fixture("vaccination"))
        self.assertEqual(parsed["doses"][0]["date"], "2026-10-02T08:30")          # the slip's date
        _, parsed = check(fixture("vaccination-booklet"))                         # a booklet has none of its own
        self.assertEqual(parsed["dates"], {"effective": "first_dose", "first_dose": "2010-05-04"})
        issues, _ = self.dose(2, name="vaccination-booklet", date=None)
        self.assertEqual([i["path"] for i in issues if i["code"] == "E-DATE-MISSING"], ["vaccination.doses[2].date"])

    def test_doses(self):
        doc = fixture("vaccination")
        doc["vaccination"]["doses"] = []
        self.assertEqual(codes(check(doc)[0], "error"), ["E-EMPTY"])
        doc = fixture("vaccination-booklet")
        doc["vaccination"]["doses"][1]["date"] = "2010-05-04"
        self.assertEqual(codes(check(doc)[0], "warning"), ["W-DOSE-REPEATED"])
        issues, parsed = self.dose(dose_quantity="1 liều")
        self.assertIn("I-DOSE-TEXT", codes(issues, "info"))
        self.assertIsNone(parsed["doses"][0]["quantity"])
        self.assertEqual(self.dose()[1]["doses"][0]["quantity"]["ucum"], "mL")

    def test_card_columns(self):
        # 'Liều lượng (ml)': the unit is printed once, in the column header
        _, parsed = self.dose(name="vaccination-card")
        self.assertEqual(parsed["doses"][0]["quantity"]["number"], 0.5)
        doc = fixture("vaccination-card")
        del doc["vaccination"]["dose_unit"]
        issues, parsed = check(doc)
        self.assertIn("no vaccination.dose_unit", next(i["message"] for i in issues if i["code"] == "I-DOSE-TEXT"))
        self.assertIsNone(parsed["doses"][0]["quantity"])
        # an expiry before the day of the shot is a misread (or a wrong paper)
        issues, _ = self.dose(name="vaccination-card", expiry="2024-02")
        self.assertEqual(codes(issues, "warning"), ["W-DOSE-EXPIRED"])
        self.assertEqual(codes(self.dose(name="vaccination-card", expiry="2025-09")[0], "warning"), [])  # to month end
        # Td is not Tdap
        self.assertEqual(codes(self.dose(1, name="vaccination-card", key="tdap")[0], "warning"), ["W-VACCINE-UNLISTED"])

    def test_appointments(self):
        issues, parsed = check(fixture("vaccination-card"))
        self.assertEqual([(d["slug"], d["code"]["cvx"], d["date"]) for d in parsed["due"]],
                         [("due-dengue", "330", "2025-10-23")])
        doc = fixture("vaccination-card")
        doc["vaccination"]["due"][0].update(key=None)
        self.assertEqual(codes(check(doc)[0], "warning"), ["W-VACCINE-AVAILABLE"])
        doc["vaccination"]["due"][0].update(key="dengue", date="1980-01-01")
        self.assertEqual(codes(check(doc)[0], "error"), ["E-DATE-BEFORE-BIRTH"])
        del doc["vaccination"]["due"][0]["date"]
        self.assertEqual(codes(check(doc)[0], "error"), ["E-SCHEMA"])
        # a card with appointments only is not empty, but it needs a date of its own
        doc = fixture("vaccination-card")
        doc["vaccination"]["doses"] = []
        self.assertEqual(codes(check(doc)[0], "error"), ["E-DATE-MISSING"])
        doc["dates"] = {"reported": "2025-09-23"}
        self.assertEqual(codes(check(doc)[0], "error"), [])


class Archive(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        shutil.copy(FIXTURES / "people.toml", self.root / "people.toml")

    def add(self, name: str, approve=True, accept=False, photo="", content=None) -> store.Document:
        """Take a fixture in. `photo` makes another photograph of it (other bytes); `content` replaces it."""
        scan = self.root / f"{name}{photo}.jpg"
        scan.write_bytes(FAKE_JPEG + (name + photo).encode())
        doc = store.intake(self.root, "example", [scan])
        doc.extraction_path.write_text(json.dumps(content or fixture(name), ensure_ascii=False), encoding="utf-8")
        if approve:
            issues, _ = extraction.validate(doc.extraction(), PERSON, TODAY)
            store.approve(doc, "Owner", issues, accept_warnings=accept)
        return doc

    def map(self, **kw):
        return fhir.map_all(self.root, DEFAULT_BASE, today=TODAY, **kw)

    def resources(self, doc):
        return [json.loads(line) for line in (doc.path / "fhir.ndjson").read_text(encoding="utf-8").splitlines()]

    def test_intake(self):
        doc = self.add("ecg", approve=False)
        self.assertEqual(doc.state(), "extracted")
        self.assertTrue((doc.path / "document.pdf").read_bytes().startswith(b"%PDF-1.4"))
        self.assertEqual(doc.meta["pdf"]["pages"], 1)
        self.assertEqual(pdf.jpeg_info(FAKE_JPEG), (48, 64, 3))
        with self.assertRaises(store.ArchiveError):                 # the same scan again
            store.intake(self.root, "example", [self.root / "ecg.jpg"])
        with self.assertRaises(store.ArchiveError):
            store.intake(self.root, "nobody", [self.root / "ecg.jpg"])

    def checked(self, doc):
        with unittest.mock.patch.object(extraction.dt, "date", wraps=dt.date) as fake:
            fake.today.return_value = TODAY
            return cli._checked(self.root, doc)

    def test_photo_orientation_becomes_page_rotation(self):
        tiff = b"MM\0*\0\0\0\x08" + b"\0\x01" + b"\x01\x12\0\x03\0\0\0\x01\0\x06\0\0" + b"\0\0\0\0"
        app1 = b"\xff\xe1" + (len(tiff) + 8).to_bytes(2, "big") + b"Exif\0\0" + tiff
        rotated = FAKE_JPEG[:2] + app1 + FAKE_JPEG[2:]                  # 48 x 64 pixels, orientation 6
        self.assertEqual(pdf.jpeg_orientation(rotated), 6)
        self.assertEqual(pdf.jpeg_orientation(FAKE_JPEG), 1)
        page = self.root / "page.jpg"
        page.write_bytes(rotated)
        out = self.root / "out.pdf"
        pdf.images_to_pdf([page], out)
        self.assertIn(b"/Rotate 90", out.read_bytes())
        self.assertIn(b"/MediaBox [0 0 446.25 595.0]", out.read_bytes())   # stored 48 x 64, shown 595 wide

    def test_rebuild_pdf(self):
        doc = self.add("xray")
        before = dict(doc.meta["pdf"])
        self.assertFalse(store.rebuild_pdf(doc))                          # same builder, same bytes
        (doc.path / "document.pdf").write_bytes(b"%PDF-1.4 old")
        doc.meta["pdf"]["sha256"] = "0" * 64
        self.assertTrue(store.rebuild_pdf(doc))
        self.assertEqual(store.Document(doc.path).meta["pdf"], before)

    def test_compact_review(self):
        doc = self.add("lab-report-columns", approve=False)
        issues, parsed = self.checked(doc)
        text = cli.review_compact(doc, issues, parsed)
        self.assertIn("#1 MCV: 78.4 fL (( 85 - 95 fL )) L -> 787-2", text)
        self.assertIn("#2 RDW_SD: 41.0 fL (( 35 - 46fL)) -> name only", text)
        self.assertIn("[also 0.58]", text)
        self.assertNotIn("|---", text)

    def test_intake_refuses_pages_already_in(self):
        pages = [self.root / f"page{n}.jpg" for n in (1, 2, 3)]
        for n, page in enumerate(pages, 1):
            page.write_bytes(FAKE_JPEG + bytes([n]))
        first = store.intake(self.root, "example", pages[:1])
        with self.assertRaisesRegex(store.ArchiveError, f"page1.jpg is already in the archive: {first.id}/original/01-page1.jpg"):
            store.intake(self.root, "example", pages)                # the whole report after its first page
        with self.assertRaisesRegex(store.ArchiveError, "given twice"):
            store.intake(self.root, "example", [pages[1], pages[1]])
        whole = store.intake(self.root, "example", pages[1:])
        with self.assertRaisesRegex(store.ArchiveError, "document.pdf"):  # the PDF built from photos, given back
            store.intake(self.root, "example", [whole.path / "document.pdf"])

    def test_same_paper_photographed_twice(self):
        first = self.add("lab-report")
        second = self.add("lab-report", approve=False, photo="-again")
        self.assertEqual([i["code"] for i in self.checked(first)[0] if i["level"] != "info"], [])
        issues, _ = self.checked(second)
        dup = [i for i in issues if i["code"] == "E-DUPLICATE-DOCUMENT"]
        self.assertEqual(len(dup), 1)
        self.assertIn(first.id, dup[0]["message"])
        with self.assertRaises(store.ArchiveError):
            store.approve(second, "Owner", issues, accept_warnings=True)
        result = self.map()
        self.assertEqual(result["mapped"], [first.id])
        self.assertEqual(result["skipped"], {second.id: "extracted"})

    def test_duplicate_approved_before_the_first_was_checked_is_not_mapped(self):
        first = self.add("ecg")
        second = self.add("ecg", photo="-again")                       # approved without the archive check
        result = self.map()
        self.assertIn(first.id, result["mapped"])
        self.assertEqual(list(result["failed"]), [second.id])

    def test_same_paper_read_differently(self):
        first = self.add("lab-report")
        misread = fixture("lab-report")
        misread["results"][16]["value"] = "5,9"                       # one digit read differently
        second = self.add("lab-report", approve=False, photo="-again", content=misread)
        self.assertIn("W-POSSIBLE-DUPLICATE", [i["code"] for i in self.checked(second)[0]])
        self.assertNotIn("E-DUPLICATE-DOCUMENT", [i["code"] for i in self.checked(second)[0]])
        partial = fixture("lab-report")
        partial["results"] = partial["results"][16:]                  # page 2 taken in alone, re-photographed
        partial["document"]["title"] = "SINH HÓA"
        third = self.add("lab-report", approve=False, photo="-page2", content=partial)
        self.assertIn("W-POSSIBLE-DUPLICATE", [i["code"] for i in self.checked(third)[0]])

    def test_different_papers_are_not_duplicates(self):
        def report(number, **study):
            doc = fixture("ultrasound")
            doc["document"]["document_number"] = number
            doc["study"].update(study)
            return doc

        def dups(doc):
            return [i["code"] for i in self.checked(doc)[0] if "DUPLICATE" in i["code"]]

        abdomen = self.add("ultrasound", approve=False, photo="-a", content=report("US-0001"))
        # Same day, facility and printed number, another examination (hospitals do reuse a number).
        thyroid = self.add("ultrasound", approve=False, photo="-b", content=report(
            "US-0001", key="us-thyroid", procedure="SIÊU ÂM TUYẾN GIÁP",
            findings="Tuyến giáp: kích thước và hồi âm bình thường.", impression="Bình thường."))
        # Same content under a different printed number: two reports.
        renumbered = self.add("ultrasound", approve=False, photo="-c", content=report("US-0002"))
        self.assertEqual(dups(abdomen) + dups(thyroid) + dups(renumbered), [])
        # The same paper again, with its number left out of the transcription: still the same paper.
        again = self.add("ultrasound", approve=False, photo="-d", content=report(None))
        self.assertEqual(dups(again), ["E-DUPLICATE-DOCUMENT", "E-DUPLICATE-DOCUMENT"])

    def test_remove(self):
        first = self.add("xray")
        second = self.add("xray", approve=False, photo="-again")
        with unittest.mock.patch.dict("os.environ", {"PAPER_ARCHIVE_DIR": str(self.root)}):
            self.assertEqual(cli.main(["remove", second.id[:6]]), 2)    # a prefix is not enough
            self.assertTrue(second.path.exists())
            self.assertEqual(cli.main(["remove", second.id]), 0)
        self.assertFalse(second.path.exists())
        self.assertEqual([d.id for d in store.documents(self.root)], [first.id])
        self.assertEqual([i for i in self.checked(first)[0] if i["level"] != "info"], [])

    def test_only_approved_documents_are_mapped(self):
        doc = self.add("xray", approve=False)
        self.assertEqual(self.map()["skipped"], {doc.id: "extracted"})
        issues, _ = extraction.validate(doc.extraction(), PERSON, TODAY)
        store.approve(doc, "Owner", issues, False)
        self.assertEqual(self.map()["mapped"], [doc.id])
        changed = fixture("xray")
        changed["study"]["impression"] = "Something else."
        doc.extraction_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertEqual(self.map()["skipped"], {doc.id: "changed-after-approval"})
        self.assertFalse((doc.path / "fhir.ndjson").exists())

    def test_approval_rules(self):
        doc = self.add("lab-report", approve=False)
        bad = fixture("lab-report")
        bad["results"][17]["flag"] = None
        doc.extraction_path.write_text(json.dumps(bad), encoding="utf-8")
        issues, _ = extraction.validate(doc.extraction(), PERSON, TODAY)
        with self.assertRaises(store.ArchiveError):
            store.approve(doc, "Owner", issues, accept_warnings=False)
        review = store.approve(doc, "Owner", issues, accept_warnings=True)
        self.assertEqual(review["accepted_warnings"], ["W-FLAG-MISSING results[17].flag"])
        bad["results"][16]["value"] = "540"
        doc.extraction_path.write_text(json.dumps(bad), encoding="utf-8")
        issues, _ = extraction.validate(doc.extraction(), PERSON, TODAY)
        with self.assertRaises(store.ArchiveError):
            store.approve(doc, "Owner", issues, accept_warnings=True)

    def test_lab_mapping(self):
        doc = self.add("lab-report")
        self.map()
        res = self.resources(doc)
        obs = {r["identifier"][0]["value"].split("/")[1]: r for r in res if r["resourceType"] == "Observation"}
        self.assertEqual([r["resourceType"] for r in res[:2]], ["DocumentReference", "DiagnosticReport"])
        self.assertEqual(len(obs), 27)
        self.assertEqual(len(res[1]["result"]), 27)
        glucose = obs["glucose"]
        self.assertEqual(glucose["code"]["coding"][0]["code"], "14749-6")
        self.assertEqual(glucose["code"]["text"], "Glucose")
        self.assertEqual(glucose["valueQuantity"], {"value": 5.4, "unit": "mmol/L",
                                                    "system": "http://unitsofmeasure.org", "code": "mmol/L"})
        self.assertEqual(glucose["effectiveDateTime"], "2026-10-02T07:15:00+07:00")
        self.assertEqual(glucose["referenceRange"][0]["low"]["value"], 3.9)
        self.assertNotIn("interpretation", glucose)                 # nothing printed, nothing computed
        self.assertEqual(obs["cholesterol"]["interpretation"][0]["coding"][0]["code"], "H")
        self.assertEqual(obs["crp"]["valueQuantity"]["comparator"], "<")
        self.assertEqual(obs["wbc"]["valueQuantity"]["code"], "10*9/L")
        self.assertEqual(obs["wbc"]["valueQuantity"]["unit"], "G/L")
        self.assertNotIn("coding", obs["rdw-sd"]["code"])           # not in the catalog: no guessed code
        self.assertEqual(obs["hbsag"]["valueString"], "Âm tính")
        self.assertEqual(obs["hbsag"]["interpretation"][0]["coding"][0]["code"], "NEG")
        self.assertEqual(obs["creatinine"]["referenceRange"][0]["low"]["value"], 44)      # the range of her sex
        self.assertEqual(obs["creatinine"]["referenceRange"][0]["text"], "Nữ: 44 - 80")
        self.assertEqual(obs["plt"]["valueQuantity"]["value"], 256)

    def test_other_mappings_and_references(self):
        docs = [self.add(n) for n in ("ecg", "ultrasound", "xray", "lab-report", "echo", "visit", "lab-report-columns")]
        self.map()
        shared = [json.loads(line) for line in (self.root / "fhir" / "shared.ndjson").read_text().splitlines()]
        self.assertEqual([r["resourceType"] for r in shared], ["Patient", "Organization"])
        known = set()
        everything = shared + [r for d in docs for r in self.resources(d)]
        for r in everything:
            ident = fhir.ident_of(r)
            known.add(f"{r['resourceType']}?identifier={ident['system']}|{ident['value']}")

        def refs(node):
            if isinstance(node, dict):
                if "reference" in node:
                    yield node["reference"]
                for v in node.values():
                    yield from refs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from refs(v)

        for r in everything:
            for ref in refs(r):
                self.assertIn(ref, known)
        ecg = self.by_slug(docs[0])
        self.assertEqual(ecg["qrs-axis"]["valueQuantity"], {"value": -12, "unit": "deg",
                                                            "system": "http://unitsofmeasure.org", "code": "deg"})
        self.assertEqual(ecg["heart-rate"]["code"]["coding"][0]["code"], "8867-4")
        self.assertEqual(ecg["heart-rate"]["effectiveDateTime"], "2026-10-02T08:05:31+07:00")
        self.assertIn("method", ecg["machine-interpretation"])
        us = self.resources(docs[1])
        self.assertIn("extension", us[0])                               # who transcribed, who approved
        self.assertEqual(us[1]["code"]["coding"][0]["code"], "24558-9")
        self.assertEqual(us[1]["conclusion"], "Gan nhiễm mỡ độ I. Nang thận trái.")
        self.assertEqual(us[1]["category"][0]["coding"][0]["code"], "RUS")
        xr = self.resources(docs[2])
        self.assertEqual(xr[1]["effectiveDateTime"], "2026-10-02")
        self.assertNotIn("issued", xr[1])

    def by_slug(self, doc):
        return {r["identifier"][0]["value"].split("/", 1)[1]: r for r in self.resources(doc)
                if r["resourceType"] == "Observation"}

    def test_vitals_are_stored_once(self):
        us, echo, endo = self.add("ultrasound"), self.add("echo"), self.add("endoscopy", accept=True)
        self.map()
        vitals = {d.id: [r for r in self.resources(d) if r["identifier"][0]["value"].startswith("vital/")]
                  for d in (us, echo, endo)}
        first, second = sorted([us, echo], key=lambda d: d.id)
        self.assertEqual(len(vitals[first.id]), 5)                      # same readings on both papers of 2026-10-02
        self.assertEqual(len(vitals[second.id]), 0)
        self.assertEqual(sorted(v["code"]["coding"][0]["code"] for v in vitals[endo.id]), ["29463-7", "85354-9", "8867-4"])
        bp = next(v for v in vitals[first.id] if v["code"]["coding"][0]["code"] == "85354-9")
        self.assertEqual([c["valueQuantity"]["value"] for c in bp["component"]], [118, 76])
        self.assertEqual(bp["effectiveDateTime"], "2026-10-02")
        report = self.resources(endo)[1]
        self.assertEqual(report["code"]["coding"][0]["code"], "18746-8")
        self.assertEqual(report["category"], [{"text": "Endoscopy report"}])
        self.assertEqual(self.by_slug(endo)["findings"]["code"]["coding"][0]["code"], "19805-1")

    def test_echo_and_columns_mapping(self):
        echo, lab = self.add("echo"), self.add("lab-report-columns")
        self.map()
        obs = self.by_slug(echo)
        self.assertEqual(self.resources(echo)[1]["category"][0]["coding"][0]["code"], "CUS")
        self.assertEqual(obs["echo-lvef"]["valueQuantity"], {"value": 66, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"})
        self.assertEqual(obs["echo-lvef"]["category"][0]["coding"][0]["code"], "imaging")
        self.assertEqual(obs["echo-edt"]["valueQuantity"]["code"], "ms")
        self.assertEqual(obs["echo-midwall-fs"]["code"], {"text": "Midwall FS"})
        obs = self.by_slug(lab)
        self.assertEqual(obs["alt"]["referenceRange"][0]["high"]["value"], 31)
        self.assertEqual(obs["alt"]["referenceRange"][0]["text"], "Nam<41 U/L Nữ <31U/L")
        self.assertEqual(obs["alt"]["extension"][1]["valueString"], "0.58 ( Nữ<0,52 mckat/L Nam<0,68 mckat/L )")
        self.assertEqual(obs["hbsag"]["valueQuantity"], {"value": 0.42, "unit": "COI", "system": "http://unitsofmeasure.org", "code": "{COI}"})
        self.assertEqual(obs["hbsag"]["interpretation"][0]["coding"][0]["code"], "NEG")
        self.assertEqual(obs["urine-specific-gravity"]["valueQuantity"], {"value": 1.015})

    def test_visit_mapping(self):
        doc = self.add("visit")
        self.map()
        res = self.resources(doc)
        self.assertEqual([r["resourceType"] for r in res[:4]], ["DocumentReference", "Encounter", "Composition", "Condition"])
        reference, encounter, composition, condition = res[:4]
        self.assertEqual(encounter["period"], {"start": "2026-10-05"})
        self.assertEqual(encounter["reasonCode"], [{"text": "Ho khan 5 ngày"}])
        self.assertEqual(condition["code"], {"text": "Viêm họng cấp"})
        self.assertEqual(composition["identifier"]["value"], doc.id)
        self.assertEqual([s["code"]["coding"][0]["code"] for s in composition["section"]],
                         ["10154-3", "10160-0", "10157-6", "11348-0", "48765-2", "29545-1", "30954-2", "29548-5", "18776-5"])
        self.assertIn("Tim đều &lt;100 lần/phút &amp; không tiếng thổi.", composition["section"][5]["text"]["div"])
        self.assertIn("<br/>", composition["section"][5]["text"]["div"])
        self.assertEqual(reference["context"]["related"][0]["reference"].split("?")[0], "Composition")
        self.assertEqual(len([r for r in res if r["resourceType"] == "Observation"]), 6)        # the six vital signs

    def test_vaccination_mapping(self):
        doc = self.add("vaccination")
        self.map()
        res = self.resources(doc)
        self.assertEqual([r["resourceType"] for r in res],
                         ["DocumentReference", "Immunization", "ImmunizationRecommendation", "MedicationRequest",
                          "Observation", "Observation", "Observation"])
        reference, imm, rec, order = res[:4]
        self.assertEqual(reference["type"]["coding"][0]["code"], "87273-9")
        self.assertEqual([r["reference"].split("?")[0] for r in reference["context"]["related"]],
                         ["Immunization", "ImmunizationRecommendation", "MedicationRequest"])
        self.assertEqual(imm["status"], "completed")
        self.assertEqual(imm["vaccineCode"], {"coding": [{"system": fhir.CVX, "code": "165", "display": "HPV9"}],
                                              "text": "Gardasil 9 (0,5ml)"})
        self.assertEqual(imm["occurrenceDateTime"], "2026-10-02T08:30:00+07:00")
        self.assertEqual((imm["lotNumber"], imm["expirationDate"]), ("X012345", "2027-08"))
        self.assertEqual(imm["doseQuantity"], {"value": 0.5, "unit": "ml", "system": fhir.UCUM, "code": "mL"})
        self.assertEqual(imm["protocolApplied"], [{"targetDisease": [{"text": "HPV"}], "doseNumberString": "Mũi 2"}])
        self.assertEqual(imm["performer"][1]["actor"], {"display": "ĐD. Lê Thị Mẫu"})
        self.assertIn({"text": "Pre-vaccination screening: Đủ điều kiện tiêm chủng ngay"}, imm["note"])
        self.assertEqual(rec["recommendation"][0]["dateCriterion"][0]["value"], "2027-04-02")
        self.assertEqual(rec["recommendation"][0]["dateCriterion"][0]["code"]["coding"][0]["code"], "30980-7")
        self.assertEqual((order["status"], order["intent"]), ("unknown", "order"))
        self.assertEqual(order["medicationCodeableConcept"]["coding"][0]["code"], "88")
        self.assertEqual(order["requester"], {"display": "BS. Nguyễn Văn Mẫu"})

        booklet = self.add("vaccination-booklet")
        self.map()
        res = {r["identifier"][0]["value"].split("/")[-1]: r for r in self.resources(booklet)}
        self.assertEqual(res[booklet.id]["context"]["period"], {"start": "2010-05-04"})
        self.assertEqual(res["hepb"]["performer"], [{"actor": {"display": "Bệnh viện Ví Dụ"}}])
        self.assertEqual(res["dtap-ipv-hib-hepb"]["status"], "not-done")
        self.assertEqual(res["dtap-ipv-hib-hepb"]["vaccineCode"], {"text": "Hexaxim"})
        self.assertEqual(res["tetanus-2"]["occurrenceDateTime"], "2010-06-08")

    def test_same_booklet_photographed_twice(self):
        first = self.add("vaccination-booklet")
        second = self.add("vaccination-booklet", approve=False, photo="-again")
        self.assertEqual(codes(self.checked(second)[0], "error"), ["E-DUPLICATE-DOCUMENT"])
        changed = fixture("vaccination-booklet")
        changed["vaccination"]["doses"][2]["lot"] = "AB12"
        third = self.add("vaccination-booklet", approve=False, photo="-third", content=changed)
        self.assertEqual(codes(self.checked(third)[0], "warning"), ["W-POSSIBLE-DUPLICATE"])
        self.assertIn(first.id, next(i["message"] for i in self.checked(third)[0] if i["code"] == "W-POSSIBLE-DUPLICATE"))

    def test_same_shot_on_two_papers(self):
        booklet = self.add("vaccination-booklet")
        self.assertEqual(codes(self.checked(booklet)[0], "warning"), [])
        card = fixture("vaccination-card")
        card["vaccination"]["doses"][1].update(vaccine="Uốn ván hấp phụ", key="tetanus", date="2010-06-08")
        card = self.add("vaccination-card", approve=False, content=card)
        issues, _ = self.checked(card)
        self.assertEqual([(i["code"], i["path"]) for i in issues if i["level"] == "warning"],
                         [("W-DOSE-ELSEWHERE", "vaccination.doses[1]")])
        self.assertIn(f"doses[1] of {booklet.id}", next(i["message"] for i in issues if i["code"] == "W-DOSE-ELSEWHERE"))
        self.assertEqual(codes(self.checked(booklet)[0], "warning"), ["W-DOSE-ELSEWHERE"])     # either may be first
        # a booklet photographed twice is one duplicate document, not one warning per dose
        again = self.add("vaccination-booklet", approve=False, photo="-again")
        issues, _ = self.checked(again)
        self.assertEqual(codes(issues, "error"), ["E-DUPLICATE-DOCUMENT"])
        self.assertEqual([i["message"].split(" of ")[1][:len(card.id)] for i in issues if i["code"] == "W-DOSE-ELSEWHERE"],
                         [card.id])                                       # the card's shot, not the first booklet's
        self.assertNotIn(again.id, " ".join(i["message"] for i in self.checked(booklet)[0]))

    def test_vaccination_review(self):
        doc = self.add("vaccination", approve=False)
        text = cli.review_compact(doc, *self.checked(doc))
        self.assertIn("#0 2026-10-02T08:30 Gardasil 9 (0,5ml) (disease HPV, dose_number Mũi 2, lot X012345", text)
        self.assertIn("-> CVX 165 HPV9; Immunization completed; next dose due", text)
        self.assertIn("-> CVX 88 influenza, unspecified formulation; order only (MedicationRequest)", text)
        card = self.add("vaccination-card", approve=False)
        text = cli.review_compact(card, *self.checked(card))
        self.assertIn("country Pháp", text)
        self.assertIn("due#0 2025-10-23 SXH -> CVX 330 Dengue fever, unspecified; appointment", text)

    def test_vaccination_card_mapping(self):
        doc = self.add("vaccination-card")
        self.map()
        res = self.resources(doc)
        self.assertEqual([r["resourceType"] for r in res],
                         ["DocumentReference", "Immunization", "ImmunizationRecommendation", "Immunization",
                          "ImmunizationRecommendation", "ImmunizationRecommendation"])
        flu, td, dengue = res[1], res[3], res[5]
        self.assertEqual(flu["doseQuantity"]["value"], 0.5)
        self.assertNotIn("manufacturer", flu)                         # 'Nước sản xuất' is a country, not a maker
        self.assertIn({"text": "Country of manufacture: Pháp"}, flu["note"])
        self.assertEqual(td["vaccineCode"]["coding"][0]["code"], "139")
        self.assertEqual(dengue["identifier"][0]["value"], f"{doc.id}/due-dengue")
        self.assertEqual(dengue["date"], "2020-10-05")                 # the card's first dose
        self.assertEqual(dengue["recommendation"][0]["vaccineCode"][0]["coding"][0]["code"], "330")
        self.assertEqual(dengue["recommendation"][0]["dateCriterion"][0]["value"], "2025-10-23")
        self.assertEqual(len(res[0]["context"]["related"]), 5)

    def test_mapping_is_repeatable(self):
        doc = self.add("lab-report")
        self.map()
        first = (doc.path / "fhir.ndjson").read_bytes()
        self.assertEqual(self.map()["unchanged"], [doc.id])
        self.map(force=True)
        self.assertEqual((doc.path / "fhir.ndjson").read_bytes(), first)


class FakeMedplum(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        state = self.server.state
        if self.path == "/oauth2/token":
            return self._reply(200, {"access_token": "token"})
        if self.path == "/fhir/R4/Binary":
            state["binaries"].append(body)
            return self._reply(201, {"resourceType": "Binary", "id": f"b{len(state['binaries'])}"})
        entries = []
        for entry in json.loads(body)["entry"]:
            url = entry["request"]["url"]
            entries.append({"response": {"status": "200 OK" if url in state["resources"] else "201 Created"}})
            state["resources"][url] = entry["resource"]
        self._reply(200, {"resourceType": "Bundle", "type": "transaction-response", "entry": entries})

    def do_DELETE(self):
        self.server.state["deleted"].append(self.path)
        self._reply(204)


class Loading(unittest.TestCase):
    add, map, resources = Archive.add, Archive.map, Archive.resources

    def setUp(self):
        Archive.setUp(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMedplum)
        self.server.state = {"binaries": [], "resources": {}, "deleted": []}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def load(self, **kw):
        client = load.FhirClient(f"http://127.0.0.1:{self.server.server_port}", "id", "secret")
        return load.load_all(self.root, client, "project-1", sleep=lambda s: None, **kw)

    def test_references_inside_a_document_use_its_full_urls(self):
        # Medplum resolves a conditional reference only against resources that already exist: references
        # between the resources of one document must point at entries of the same transaction.
        doc = self.add("visit")
        self.map()
        resources = self.resources(doc)
        bundle = load.transaction(resources)
        full_urls = {e["fullUrl"] for e in bundle["entry"]}
        self.assertEqual(len(full_urls), len(resources))
        self.assertTrue(all(u.startswith("urn:uuid:") for u in full_urls))
        self.assertEqual(load.transaction(resources), bundle)                  # deterministic

        def refs(node):
            if isinstance(node, dict):
                if "reference" in node:
                    yield node["reference"]
                for v in node.values():
                    yield from refs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from refs(v)

        found = [ref for e in bundle["entry"] for ref in refs(e["resource"])]
        self.assertTrue(any(r.startswith("urn:uuid:") for r in found))
        for ref in found:
            self.assertTrue(ref in full_urls or ref.startswith(("Patient?", "Organization?")), ref)
        self.assertEqual(resources[1]["subject"]["reference"].split("?")[0], "Patient")    # input left unchanged

    def test_load_is_idempotent_and_follows_approval(self):
        lab, ecg = self.add("lab-report"), self.add("ecg")
        self.map()
        result = self.load()
        state = self.server.state
        self.assertEqual(result["loaded"], ["shared", ecg.id, lab.id])
        self.assertEqual(len(state["binaries"]), 2)
        self.assertEqual(state["binaries"][0], (ecg.path / "document.pdf").read_bytes())
        self.assertEqual(len(state["resources"]), 2 + 2 + 12 + 2 + 27)
        sent = next(r for r in state["resources"].values() if r["resourceType"] == "DocumentReference")
        self.assertTrue(sent["content"][0]["attachment"]["url"].startswith("Binary/b"))
        self.assertEqual(self.load()["unchanged"], ["shared", ecg.id, lab.id])
        self.assertEqual(len(state["binaries"]), 2)
        # The owner edits the ECG transcription: it is unapproved, so it leaves the server.
        changed = copy.deepcopy(fixture("ecg"))
        changed["ecg"]["rhythm"] = "Nhịp xoang chậm"
        ecg.extraction_path.write_text(json.dumps(changed), encoding="utf-8")
        self.map()
        result = self.load()
        self.assertEqual(result["removed"], [ecg.id])
        self.assertEqual(len(state["deleted"]), 14 + 1)             # 14 resources and the PDF


if __name__ == "__main__":
    unittest.main()
