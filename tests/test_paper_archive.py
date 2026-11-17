"""Offline tests: made-up documents only (tests/fixtures). Run: python3 -m unittest discover -s tests -v"""
import copy
import datetime as dt
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from paperarchive import DEFAULT_BASE, catalog, extraction, fhir, load, pdf, store  # noqa: E402

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
        for name in ("lab-report", "lab-report-columns", "ecg", "ultrasound", "xray", "echo", "visit"):
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


class Archive(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        shutil.copy(FIXTURES / "people.toml", self.root / "people.toml")

    def add(self, name: str, approve=True, accept=False) -> store.Document:
        scan = self.root / f"{name}.jpg"
        scan.write_bytes(FAKE_JPEG + name.encode())
        doc = store.intake(self.root, "example", [scan])
        doc.extraction_path.write_text(json.dumps(fixture(name), ensure_ascii=False), encoding="utf-8")
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
        vitals = {d.id: [r for r in self.resources(d) if r["identifier"][0]["system"].endswith("/paper-vital")]
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
    add, map = Archive.add, Archive.map

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
