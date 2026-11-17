"""Check a transcription (extraction.json) and turn its printed strings into typed values.

The agent transcribes what the paper says, as strings. Everything numeric is derived here, by code, so a
number is parsed in exactly one place. docs/extraction-format.md is the format, docs/validation-rules.md
lists every rule by its code.
"""
from __future__ import annotations

import datetime as dt
import math
import re

from . import DOCUMENT_TYPES, EXTRACTION_FORMAT, catalog

ERROR, WARNING, INFO = "error", "warning", "info"

# ---------------------------------------------------------------- schema

S, I = "str", "int"
DATE, WHEN = "date", "when"
ROW = {"section": S, "name": (S, True), "analyte": S, "value": (S, True), "unit": S, "reference": S, "flag": S,
       "alt_value": S, "alt_reference": S, "specimen": S, "method": S, "note": S, "page": I}
VITALS = {k: S for k in ("pulse", "blood_pressure", "temperature", "height", "weight", "bmi")}
ECG = {"measurements": {k: S for k in ("heart_rate", "rr_interval", "pr_interval", "qrs_duration", "qt_interval",
                                       "qtc_interval", "p_axis", "qrs_axis", "t_axis")},
       "rhythm": S, "machine_interpretation": S, "conclusion": S, "device": S}
STUDY = {"key": S, "procedure": (S, True), "indication": S, "technique": S, "findings": (S, True),
         "measurements": [ROW], "impression": S, "recommendation": S}
VISIT_SECTIONS = ("reason", "medications", "family_history", "past_history", "allergies", "examination", "tests",
                  "diagnosis", "advice")
VISIT = {k: S for k in VISIT_SECTIONS}
SCHEMA = {
    "format": (S, True),
    "document": ({"type": (DOCUMENT_TYPES, True), "language": S, "title": S, "document_number": S, "pages": I}, True),
    "patient": ({"name": S, "birth_date": DATE, "birth_year": I, "age": I, "sex": ("male", "female"),
                 "patient_number": S}, True),
    "facility": ({"name": (S, True), "department": S, "address": S}, True),
    "staff": {"ordered_by": S, "performed_by": S, "signed_by": S},
    "dates": ({"collected": WHEN, "received": WHEN, "performed": WHEN, "reported": WHEN}, True),
    "extracted_by": ({"agent": (S, True), "model": S}, True),
    "vitals": VITALS,
    "uncertain": [{"path": (S, True), "reason": (S, True)}],
    "notes": S,
    "results": [ROW],
    "ecg": ECG,
    "study": STUDY,
    "visit": VISIT,
}
BODY = {"lab-report": "results", "ecg": "ecg", "ultrasound": "study", "xray": "study", "endoscopy": "study",
        "visit": "visit"}


def clean(value):
    """Drop nulls, empty strings and empty containers: 'not printed' has one spelling, absence."""
    if isinstance(value, dict):
        out = {k: clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", {}, [])}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value.strip() if isinstance(value, str) else value


def parse_when(text: str):
    """'YYYY-MM-DD' -> date, 'YYYY-MM-DDTHH:MM[:SS]' -> naive datetime, else None."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return dt.date.fromisoformat(text)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", text):
            return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    return None


def _check_schema(value, spec, path: str, issues: list) -> None:
    def bad(message):
        issues.append(issue(ERROR, "E-SCHEMA", path, message))

    if isinstance(spec, tuple) and len(spec) == 2 and isinstance(spec[1], bool):
        spec = spec[0]
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return bad("must be an object")
        for key in value:
            if key not in spec:
                issues.append(issue(ERROR, "E-SCHEMA", f"{path}.{key}".lstrip("."), "unknown field"))
        for key, sub in spec.items():
            required = isinstance(sub, tuple) and len(sub) == 2 and sub[1] is True
            if key in value:
                _check_schema(value[key], sub, f"{path}.{key}".lstrip("."), issues)
            elif required:
                issues.append(issue(ERROR, "E-SCHEMA", f"{path}.{key}".lstrip("."), "required field is missing"))
    elif isinstance(spec, list):
        if not isinstance(value, list):
            return bad("must be a list")
        for i, item in enumerate(value):
            _check_schema(item, spec[0], f"{path}[{i}]", issues)
    elif isinstance(spec, tuple):
        if value not in spec:
            bad(f"must be one of {', '.join(spec)}")
    elif spec == S:
        if not isinstance(value, str):
            bad("must be a string, exactly as printed")
    elif spec == I:
        if not isinstance(value, int) or isinstance(value, bool):
            bad("must be a whole number")
    elif spec in (DATE, WHEN):
        parsed = parse_when(value) if isinstance(value, str) else None
        if parsed is None or (spec == DATE and isinstance(parsed, dt.datetime)):
            bad("must be YYYY-MM-DD" + ("" if spec == DATE else " or YYYY-MM-DDTHH:MM (local time, no offset)"))


def issue(level: str, code: str, path: str, message: str) -> dict:
    return {"level": level, "code": code, "path": path, "message": message}


# ---------------------------------------------------------------- printed strings -> values

_NUM = r"[-−]?\d+(?:[.,]\d+)?"
_CMP = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">=", "=<": "<=", "=>": ">="}
_CMP_RE = r"<=|>=|=<|=>|[<>≤≥]"
_SEX = {"nam": "male", "nu": "female", "male": "male", "female": "female"}
FLAGS = {"h": "H", "high": "H", "cao": "H", "↑": "H", "▲": "H", "hh": "HH", "↑↑": "HH",
         "l": "L", "low": "L", "thap": "L", "↓": "L", "▼": "L", "ll": "LL", "↓↓": "LL",
         "*": "A", "a": "A", "!": "A", "abnormal": "A", "bat thuong": "A",
         "n": "N", "normal": "N", "binh thuong": "N"}
MARKED = "marked"       # the paper marks the value by colour or bold type only; the range gives the direction
QUALITATIVE = {"am tinh": "NEG", "negative": "NEG", "neg": "NEG", "non-reactive": "NEG", "nonreactive": "NEG",
               "non reactive": "NEG", "khong phan ung": "NEG", "(-)": "NEG",
               "duong tinh": "POS", "positive": "POS", "pos": "POS", "reactive": "POS", "co phan ung": "POS",
               "(+)": "POS", "binh thuong": "N", "normal": "N"}
INTERPRETATION = {"H": "High", "HH": "Critical high", "L": "Low", "LL": "Critical low", "A": "Abnormal",
                  "N": "Normal", "NEG": "Negative", "POS": "Positive"}


def _float(text: str) -> float:
    return float(text.replace(",", ".").replace("−", "-"))


def parse_number(text: str) -> dict | None:
    """'6,8' -> 6.8; '< 0.5' -> comparator '<'. None when the text is not one number."""
    m = re.fullmatch(rf"\s*({_CMP_RE})?\s*({_NUM})\s*", text)
    if not m:
        return None
    digits = m.group(2)
    # '1.250' may be 1.25 or one thousand two hundred and fifty.
    ambiguous = bool(re.fullmatch(r"[-−]?[1-9]\d{0,2}[.,]\d{3}", digits))
    decimals = len(re.split(r"[.,]", digits)[-1]) if re.search(r"[.,]", digits) else 0
    return {"comparator": _CMP.get(m.group(1) or ""), "number": _float(digits), "decimals": decimals,
            "ambiguous": ambiguous}


def _bare(text: str) -> str:
    """Without the brackets and the 'bt:' (bình thường = normal) label hospitals put around a range."""
    t = text.strip()
    while t.startswith("(") and t.endswith(")") and t.count("(") == t.count(")") == 1:
        t = t[1:-1].strip()
    t = t.strip("()[] ")
    return re.sub(r"^(bt|bình thường|normal|ref)\s*[:.]\s*", "", t, flags=re.I).strip()


def _parse_range(segment: str) -> dict | None:
    seg = segment.strip(" ;,")
    m = re.fullmatch(rf"(?P<q>[^\d<>≤≥=]+?)?\s*(?P<c>{_CMP_RE})?\s*(?P<a>{_NUM})"
                     rf"(?:\s*(?:-|–|—|~|đến|to)\s*(?P<b>{_NUM}))?\s*(?P<u>[^\d\s].*)?", seg)
    if not m or (m.group("c") and m.group("b")) or not (m.group("c") or m.group("b")):
        return None
    out: dict = {}
    if m.group("q"):
        meaning = QUALITATIVE.get(catalog.fold(m.group("q").strip(" :")))
        if not meaning:
            return None
        out["meaning"] = meaning
    if m.group("b"):
        if m.group("b").startswith(("-", "−")):
            return None
        out["low"], out["high"] = _float(m.group("a")), _float(m.group("b"))
    else:
        out["high" if _CMP[m.group("c")].startswith("<") else "low"] = _float(m.group("a"))
    if m.group("u"):
        out["unit"] = m.group("u").strip()
    return out


def parse_reference(text: str, sex: str | None = None) -> dict | None:
    """The printed normal range as low/high (+ the unit and the meaning when the paper puts them there).

    '( 3,8 - 5,5 x 10^12 /L )' -> low, high, unit; '<7.7mmol/L' -> high, unit; 'Âm tính <1 COI' -> high,
    unit, meaning NEG; 'Nam<41 U/L Nữ <31U/L' -> the range of the person's sex; 'Âm tính' -> expect NEG.
    None when it is something else: the text is kept as it is.
    """
    t = re.sub(r"@[^;,)]*", "", _bare(text)).strip()          # '<120@nam', '<101ms@41 tuổi': the condition stays in the text
    expected = QUALITATIVE.get(catalog.fold(t))
    if expected:
        return {"expect": expected}
    labels = list(re.finditer(r"(?<![^\W\d_])(Nam|Nữ|Nu|Male|Female)(?![^\W\d_])\s*:?", t, flags=re.I))
    if not labels:
        return _parse_range(t)
    ranges = {}
    for i, label in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(t)
        parsed = _parse_range(t[label.end():end])
        if parsed:
            ranges[_SEX[catalog.fold(label.group(1))]] = parsed
    chosen = ranges.get(sex or "")
    if not chosen:
        return None
    unit = next((r["unit"] for r in ranges.values() if "unit" in r), None)      # 'Nữ < 5,7 ; Nam < 7 mg/dl'
    return {**chosen, **({"unit": unit} if unit and "unit" not in chosen else {}), "sex": sex}


def name_score(printed: str, entry: dict) -> int:
    """Length of the longest catalog name of the entry found as whole words in the printed text (0 = none)."""
    text = catalog.fold(printed)
    return max((len(catalog.fold(n)) for n in entry["names"]
                if re.search(rf"(?<![a-z0-9_-]){re.escape(catalog.fold(n))}(?![a-z0-9_#%-])", text)), default=0)


def name_matches(printed: str, entry: dict) -> bool:
    return name_score(printed, entry) > 0


# ---------------------------------------------------------------- validation


def validate(raw: dict, person: dict | None = None, today: dt.date | None = None) -> tuple[list[dict], dict]:
    """Returns (issues, parsed). `parsed` is what the mapper uses; it is complete only without errors."""
    issues: list[dict] = []
    doc = clean(raw) if isinstance(raw, dict) else {}
    _check_schema(doc, SCHEMA, "", issues)
    if doc.get("format") != EXTRACTION_FORMAT:
        issues.append(issue(ERROR, "E-FORMAT", "format", f"must be {EXTRACTION_FORMAT!r}"))
    dtype = (doc.get("document") or {}).get("type")
    parsed = {"type": dtype, "doc": doc, "results": [], "measurements": [], "ecg": {}, "study": None, "dates": {},
              "vitals": {}}
    if any(i["level"] == ERROR for i in issues) or dtype not in BODY:
        return issues, parsed

    body = BODY[dtype]
    for other in set(BODY.values()) - {body}:
        if other in doc:
            issues.append(issue(ERROR, "E-BODY", other, f"a {dtype} has '{body}', not '{other}'"))
    if body not in doc:
        issues.append(issue(ERROR, "E-BODY", body, f"a {dtype} needs '{body}'"))

    parsed["dates"] = _check_dates(doc, dtype, person, today or dt.date.today(), issues)
    _check_patient(doc, person, parsed["dates"], issues)
    parsed["vitals"] = _check_vitals(doc.get("vitals", {}), issues)
    for i, entry in enumerate(doc.get("uncertain", [])):
        issues.append(issue(WARNING, "W-UNCERTAIN", f"uncertain[{i}]",
                            f"{entry['path']}: {entry['reason']} (the owner must read this from the paper)"))
    sex = (person or {}).get("sex") or doc["patient"].get("sex")
    if dtype == "lab-report":
        if not doc.get("results"):
            issues.append(issue(ERROR, "E-EMPTY", "results", "a lab report has at least one result"))
        parsed["results"] = _check_rows(doc.get("results", []), "results", sex, issues,
                                        lambda row: "urine" if re.search(r"nuoc tieu|urine|urinalysis", catalog.fold(
                                            f"{row.get('section', '')} {row.get('specimen', '')}")) else "blood")
        _cross_checks(parsed["results"], issues)
    elif dtype == "ecg":
        parsed["ecg"] = _check_ecg(doc.get("ecg", {}), issues)
    elif dtype == "visit":
        visit = doc.get("visit", {})
        if not visit:
            issues.append(issue(ERROR, "E-EMPTY", "visit", "a visit note has at least one section"))
        elif "diagnosis" not in visit:
            issues.append(issue(WARNING, "W-NO-CONCLUSION", "visit.diagnosis",
                                "no diagnosis/conclusion transcribed: confirm the paper has none"))
    else:
        study = doc.get("study", {})
        parsed["study"] = _check_study(study, dtype, issues)
        domain = "echo" if study.get("key") == "us-heart" else "imaging"
        parsed["measurements"] = _check_rows(study.get("measurements", []), "study.measurements", sex, issues,
                                             lambda row: domain)
    return issues, parsed


def _check_patient(doc: dict, person: dict | None, dates: dict, issues: list) -> None:
    printed = doc["patient"]
    if not person:
        return
    if "name" not in printed:
        issues.append(issue(WARNING, "W-PATIENT-NO-NAME", "patient.name",
                            "no patient name on the paper: confirm whose document this is"))
    else:
        known = {catalog.fold(n) for n in [person["name"], *person.get("aliases", [])]}
        if catalog.fold(printed["name"]) not in known:
            issues.append(issue(ERROR, "E-PATIENT-NAME", "patient.name",
                                f"printed name {printed['name']!r} is not {person['id']}'s name or one of their "
                                "aliases in people.toml: wrong person, or add the spelling as an alias"))
    birth = person.get("birth_date")
    year = printed.get("birth_year") or (int(printed["birth_date"][:4]) if "birth_date" in printed else None)
    if birth and "birth_date" in printed and printed["birth_date"] != str(birth):
        issues.append(issue(ERROR, "E-PATIENT-BIRTH", "patient.birth_date",
                            f"printed {printed['birth_date']}, {person['id']} was born {birth}"))
    elif birth and year and year != int(str(birth)[:4]):
        issues.append(issue(ERROR, "E-PATIENT-BIRTH", "patient.birth_year",
                            f"printed {year}, {person['id']} was born {birth}"))
    effective = dates.get(dates.get("effective") or "")
    if birth and "age" in printed and effective:
        born, seen = dt.date.fromisoformat(str(birth)), dt.date.fromisoformat(effective[:10])
        exact = seen.year - born.year - ((seen.month, seen.day) < (born.month, born.day))
        if printed["age"] not in (exact, seen.year - born.year):         # full years, or year minus year
            issues.append(issue(ERROR, "E-PATIENT-AGE", "patient.age",
                                f"printed age {printed['age']} on {effective[:10]}, but {person['id']} (born {birth}) "
                                f"was {exact}: wrong person or wrong date"))
    if person.get("sex") and printed.get("sex") and printed["sex"] != person["sex"]:
        issues.append(issue(ERROR, "E-PATIENT-SEX", "patient.sex",
                            f"printed {printed['sex']}, {person['id']} is {person['sex']}"))


def _check_dates(doc: dict, dtype: str, person: dict | None, today: dt.date, issues: list) -> dict:
    dates = {k: parse_when(v) for k, v in doc["dates"].items()}

    def day(value):
        return value.date() if isinstance(value, dt.datetime) else value

    order = ["collected", "received", "performed", "reported"]
    wanted = ("collected", "received", "reported") if dtype == "lab-report" else ("performed", "reported")
    effective = next((k for k in wanted if k in dates), None)
    if not effective:
        issues.append(issue(ERROR, "E-DATE-MISSING", "dates",
                            f"a {dtype} needs one of: {', '.join(wanted)}"))
    birth = person.get("birth_date") if person else None
    for key, value in dates.items():
        if day(value) > today:
            issues.append(issue(ERROR, "E-DATE-FUTURE", f"dates.{key}", f"{doc['dates'][key]} is in the future"))
        if birth and day(value) < dt.date.fromisoformat(str(birth)):
            issues.append(issue(ERROR, "E-DATE-BEFORE-BIRTH", f"dates.{key}",
                                f"{doc['dates'][key]} is before the birth date {birth}"))
    present = [k for k in order if k in dates]
    for a, b in zip(present, present[1:]):
        both_times = isinstance(dates[a], dt.datetime) and isinstance(dates[b], dt.datetime)
        if (dates[a] > dates[b]) if both_times else (day(dates[a]) > day(dates[b])):
            issues.append(issue(ERROR, "E-DATE-ORDER", f"dates.{b}",
                                f"{b} ({doc['dates'][b]}) is before {a} ({doc['dates'][a]})"))
    return {"effective": effective, **{k: doc["dates"][k] for k in dates}}


def _check_vitals(vitals: dict, issues: list) -> dict:
    """Header vital signs. Hospital software prints nonsense here ('0kg', '1600cm', 'BMI: NaN'): such a value
    is reported and left out of FHIR, it does not block the document."""
    spec, out = catalog.codes()["vitals"], {}

    def unusable(key, why):
        issues.append(issue(WARNING, "W-VITAL-UNUSABLE", f"vitals.{key}",
                            f"{vitals[key]!r} {why}: not loaded. If the paper really prints this, accept the warning"))

    for key, text in vitals.items():
        if key == "blood_pressure":
            m = re.fullmatch(r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*", text)
            sys_range, dia_range = spec["systolic"]["range"], spec["diastolic"]["range"]
            if not m:
                unusable(key, "is not systolic/diastolic")
            elif not (sys_range[0] <= int(m.group(1)) <= sys_range[1] and dia_range[0] <= int(m.group(2)) <= dia_range[1]
                      and int(m.group(1)) > int(m.group(2))):
                unusable(key, "is not a possible blood pressure")
            else:
                out[key] = {"systolic": int(m.group(1)), "diastolic": int(m.group(2)), "text": text}
            continue
        number = parse_number(text)
        low, high = spec[key]["range"]
        if not number or number["comparator"]:
            unusable(key, "is not a number")
        elif not low <= number["number"] <= high:
            unusable(key, f"is outside {low} to {high} {spec[key]['unit']}")
        else:
            out[key] = {"number": number["number"], "decimals": number["decimals"], "text": text}
    if "bmi" in out and "height" in out and "weight" in out:
        expected = out["weight"]["number"] / (out["height"]["number"] / 100) ** 2
        if abs(expected - out["bmi"]["number"]) > 0.6:
            unusable("bmi", f"does not follow from the height and weight (about {expected:.1f})")
            del out["bmi"]
    return out


def _check_rows(rows: list, root: str, sex: str | None, issues: list, domain_of) -> list[dict]:
    """Result rows of a lab report, or the measurements of a study: same shape, same rules."""
    analytes = catalog.analytes()
    out, seen, slugs = [], {}, {}
    for i, row in enumerate(rows):
        path = f"{root}[{i}]"

        def add(level, code, field, message):
            issues.append(issue(level, code, f"{path}.{field}", f"{row['name']}: {message}"))

        domain = domain_of(row)
        key = row.get("analyte")
        entry = analytes.get(key) if key else None
        item = {"index": i, "name": row["name"], "analyte": key if entry else None, "code": None, "kind": "text",
                "value_text": row["value"], "unit_text": row.get("unit"), "ucum": None, "comparator": None,
                "reference_text": row.get("reference"), "reference": None, "flag": None, "domain": domain,
                **{k: row.get(k) for k in ("section", "specimen", "method", "note", "alt_value", "alt_reference")}}
        out.append(item)

        # which catalog entry
        label = f"{row['name']} {row.get('section', '')}"
        peers = [e for e in analytes.values() if e.get("domain", "blood") == domain]
        best = max(peers, key=lambda e: name_score(row["name"], e), default=None)
        if key and not entry:
            add(ERROR, "E-ANALYTE-UNKNOWN", "analyte", f"{key!r} is not a catalog key (see: catalog find)")
        elif entry and entry.get("domain", "blood") != domain:
            add(ERROR, "E-DOMAIN", "analyte", f"{key!r} is a {entry.get('domain', 'blood')} test but this row is "
                f"in a {domain} section")
            entry = None
        elif entry and not name_matches(label, entry):
            add(WARNING, "W-NAME-UNLISTED", "analyte",
                f"neither the printed name nor its heading is among the catalog names of {key!r}: confirm it is "
                "the same test, then add the spelling to the catalog")
        elif entry and best and best is not entry and name_score(row["name"], best) > name_score(row["name"], entry):
            add(WARNING, "W-NAME-BETTER", "analyte",
                f"the printed name fits {best['key']!r} better than {key!r}")
        elif entry and not entry["codes"]:
            add(INFO, "I-UNCODED", "analyte", f"known to the catalog as not coded ({entry['reason']}): stored with "
                "its printed name only")
        elif not key:
            if best and name_score(row["name"], best):
                add(WARNING, "W-ANALYTE-AVAILABLE", "analyte", f"no analyte given but the catalog has {best['key']!r}")
            else:
                add(INFO, "I-UNCODED", "analyte", "not in the catalog: stored with its printed name only, no LOINC")

        # reference range (it may also carry the unit and the meaning)
        ref = None
        if row.get("reference"):
            ref = parse_reference(row["reference"], sex)
            if ref is None:
                add(INFO, "I-REFERENCE-TEXT", "reference", f"{row['reference']!r} is kept as text (not low/high)")
            elif ref.get("low", -math.inf) > ref.get("high", math.inf):
                add(ERROR, "E-REFERENCE-ORDER", "reference", f"low is above high in {row['reference']!r}")
                ref = None
        item["reference"] = ref

        # unit: its own column, or inside the reference text
        ref_unit = (ref or {}).get("unit")
        unit = row.get("unit") or ref_unit
        if not unit and entry and entry.get("assume_unit"):
            unit = entry["assume_unit"]
            add(INFO, "I-UNIT-ASSUMED", "unit", f"no unit printed: {unit} is assumed (the only unit {key!r} is reported in)")
        coded = bool(entry and entry["codes"])
        if unit:
            item["unit_text"] = unit
            item["ucum"] = catalog.ucum(unit)
            if not item["ucum"]:
                add(ERROR if coded else WARNING, "E-UNIT-UNKNOWN" if coded else "W-UNIT-UNKNOWN", "unit",
                    f"unit {unit!r} is not in units.json" + ("" if coded else ": stored as text, without UCUM"))
            elif row.get("unit") and ref_unit and catalog.ucum(ref_unit) not in (None, item["ucum"]):
                add(WARNING, "W-UNIT-MISMATCH", "unit", f"unit {row['unit']!r} but the reference is in {ref_unit!r}")

        # value
        number = parse_number(row["value"])
        qualitative = QUALITATIVE.get(catalog.fold(row["value"]))
        if number:
            item.update(kind="quantity", **number)
        elif qualitative:
            item.update(kind="qualitative", flag=qualitative)
        if coded and entry.get("qualitative"):
            if item["kind"] == "qualitative":
                item["code"] = entry["codes"][0]
            elif item["kind"] == "quantity" and entry.get("index") and ref and "meaning" in ref:
                item["code"] = entry["codes"][0]                 # cutoff index + the printed rule, e.g. 'Âm tính <1 COI'
                inside = ref.get("low", -math.inf) <= number["number"] <= ref.get("high", math.inf)
                strict = number["number"] not in (ref.get("low"), ref.get("high"))
                if inside and strict:
                    item["flag"] = ref["meaning"]
                else:
                    add(WARNING, "W-INDEX-OUTSIDE", "value",
                        f"{row['value']} does not satisfy {row['reference']!r}: no interpretation is stored; "
                        "check what the paper concludes")
            else:
                add(ERROR, "E-VALUE-NOT-QUALITATIVE", "value",
                    f"{key!r} takes negative/positive (âm tính/dương tính)"
                    + (" or an index with its printed rule" if entry.get("index") else "") + f", got {row['value']!r}")
        elif coded:
            if item["kind"] != "quantity":
                add(ERROR, "E-VALUE-NOT-NUMBER", "value", f"{row['value']!r} is not a number")
            elif unit and not item["ucum"]:
                pass                                              # E-UNIT-UNKNOWN already said so
            else:
                code = catalog.analyte_code(key, item["ucum"] or "")
                accepted = sorted(u for c in entry["codes"] for u in c["units"])
                if not code and not unit:
                    add(ERROR, "E-UNIT-MISSING", "unit", f"{key!r} needs a unit (in its own column or in the "
                        f"reference): {', '.join(accepted)}")
                elif not code:
                    add(ERROR, "E-UNIT-ANALYTE", "unit",
                        f"{key!r} is not measured in {unit!r} (accepted: {', '.join(u or 'no unit' for u in accepted)}): "
                        "wrong analyte, wrong unit, or the catalog needs a new code for this unit")
                else:
                    item["code"] = code
                    low, high = code["units"][item["ucum"] or ""]
                    if not number["comparator"] and not low <= number["number"] <= high:
                        add(ERROR, "E-IMPLAUSIBLE", "value",
                            f"{number['number']} {unit or ''} is outside what a person can have ({low} to {high}): "
                            "check the decimal point and the unit against the paper")
        elif item["kind"] == "text":
            add(WARNING, "W-VALUE-TEXT", "value", f"{row['value']!r} is neither a number nor negative/positive: "
                "stored as text")
        if number and number["ambiguous"] and not item["code"]:
            add(WARNING, "W-NUMBER-AMBIGUOUS", "value",
                f"{row['value']!r} is read as {number['number']}; it could be a thousands separator")

        # flag
        if row.get("flag") and item["kind"] == "quantity" and "meaning" not in (ref or {}):
            raw = catalog.fold(row["flag"])
            flag = MARKED if raw in ("marked", "colour", "color", "highlighted", "bold") else FLAGS.get(raw)
            if not flag:
                add(ERROR, "E-FLAG-UNKNOWN", "flag", f"flag {row['flag']!r} is not known (H, L, HH, LL, *, ↑, ↓, "
                    "or 'marked' when the paper only colours or bolds the value)")
            else:
                item["flag"] = flag
            _check_flag(item, add)
        elif row.get("flag") and item["kind"] == "text":
            add(ERROR, "E-FLAG-UNKNOWN", "flag", "a flag needs a numeric value")
        elif item["kind"] == "quantity" and "meaning" not in (ref or {}):
            _check_flag(item, add)

        if item["code"]:
            ident = item["code"]["loinc"]
            if ident in seen:
                add(ERROR, "E-DUPLICATE", "analyte",
                    f"same test as {root}[{seen[ident]}] ({item['code']['display']}): one of them is mis-assigned")
            seen[ident] = i
        base = key if entry else catalog.slug(row["name"]) or "result"
        slugs[base] = slugs.get(base, 0) + 1
        item["slug"] = base if slugs[base] == 1 else f"{base}-{slugs[base]}"
    return out


def _check_flag(item: dict, add) -> None:
    """The printed mark against the printed range. 'marked' takes its direction from the range."""
    ref, flag = item["reference"], item["flag"]
    has_range = bool(ref) and ("low" in ref or "high" in ref) and not item.get("comparator")
    if not has_range:
        if flag == MARKED:
            item["flag"] = "A"
        return
    value = item["number"]
    above, below = value > ref.get("high", math.inf), value < ref.get("low", -math.inf)
    if flag == MARKED:
        item["flag"] = "H" if above else "L" if below else "A"
        if not above and not below:
            add(WARNING, "W-FLAG-CONFLICT", "flag",
                f"marked on the paper, but {item['value_text']} is inside {item['reference_text']}: "
                "one of value, range or mark is mis-read")
    elif (above and flag in ("L", "LL", "N")) or (below and flag in ("H", "HH", "N")) or \
            (not above and not below and flag in ("H", "HH", "L", "LL")):
        add(WARNING, "W-FLAG-CONFLICT", "flag",
            f"flag {flag} does not fit {item['value_text']} against {item['reference_text']}: "
            "one of value, range or flag is mis-read")
    elif (above or below) and not flag:
        add(WARNING, "W-FLAG-MISSING", "flag",
            f"{item['value_text']} is outside {item['reference_text']} but no flag was transcribed: "
            "check the value, and whether the paper marks it (flag 'marked' for colour or bold)")


def _cross_checks(rows: list[dict], issues: list) -> None:
    """Arithmetic a blood count must satisfy. A miss means one of the numbers was mis-read."""
    by = {r["analyte"]: r for r in rows if r["analyte"] and r["kind"] == "quantity" and r["code"]
          and not r.get("comparator")}

    def val(key, to=None):
        row = by.get(key)
        if not row:
            return None
        return row["number"] * (to or {}).get(row["ucum"], 1)

    def near(name, printed, expected, rel, absolute=0.0):
        if printed is None or expected is None:
            return
        if abs(printed - expected) > max(absolute, rel * abs(expected)):
            issues.append(issue(WARNING, "W-CROSS-CHECK", f"results[{by[name]['index']}].value",
                                f"{by[name]['name']}: printed {printed:g} but the other results give about "
                                f"{expected:.3g}: re-read these values from the paper"))

    parts = [val(f"{c}-pct") for c in ("neut", "lymph", "mono", "eos", "baso")]
    if all(p is not None for p in parts) and abs(sum(parts) - 100) > 2:
        issues.append(issue(WARNING, "W-CROSS-CHECK", f"results[{by['neut-pct']['index']}].value",
                            f"the five differential percentages add up to {sum(parts):g}, not 100"))
    wbc = val("wbc")
    for cell in ("neut", "lymph", "mono", "eos", "baso"):
        pct, absolute = val(f"{cell}-pct"), val(f"{cell}-abs")
        if wbc is not None and pct is not None:
            near(f"{cell}-abs", absolute, wbc * pct / 100, 0.12, 0.15)
    g_dl, pct = {"g/L": 0.1, "g/dL": 1}, {"L/L": 100, "%": 1}
    hgb, hct, rbc = val("hgb", g_dl), val("hct", pct), val("rbc")
    if rbc:
        near("mcv", val("mcv"), hct / rbc * 10 if hct is not None else None, 0.06)
        near("mch", val("mch"), hgb / rbc * 10 if hgb is not None else None, 0.06)
    if hct:
        near("mchc", val("mchc", g_dl), hgb / hct * 100 if hgb is not None else None, 0.06)


def _check_ecg(ecg: dict, issues: list) -> dict:
    spec = catalog.codes()["ecg"]
    values = {}
    for key, text in ecg.get("measurements", {}).items():
        path = f"ecg.measurements.{key}"
        number = parse_number(text)
        if not number or number["comparator"]:
            issues.append(issue(ERROR, "E-VALUE-NOT-NUMBER", path, f"{text!r} is not a number (no unit here: "
                                f"{key} is in {spec[key]['unit']})"))
            continue
        low, high = spec[key]["range"]
        if not low <= number["number"] <= high:
            issues.append(issue(ERROR, "E-IMPLAUSIBLE", path,
                                f"{number['number']:g} {spec[key]['unit']} is outside {low} to {high}"))
            continue
        values[key] = {"number": number["number"], "decimals": number["decimals"], "text": text}
    if not values and not any(k in ecg for k in ("rhythm", "conclusion", "machine_interpretation")):
        issues.append(issue(ERROR, "E-EMPTY", "ecg", "an ECG has measurements, a rhythm or a conclusion"))
    if "conclusion" not in ecg and "machine_interpretation" not in ecg:
        issues.append(issue(WARNING, "W-NO-CONCLUSION", "ecg.conclusion",
                            "no conclusion or machine interpretation transcribed: confirm the paper has none"))
    hr, rr = values.get("heart_rate"), values.get("rr_interval")
    if hr and rr and abs(60000 / hr["number"] - rr["number"]) > 0.1 * rr["number"]:
        issues.append(issue(WARNING, "W-CROSS-CHECK", "ecg.measurements.rr_interval",
                            f"RR {rr['number']:g} ms does not fit a heart rate of {hr['number']:g}/min "
                            f"(about {60000 / hr['number']:.0f} ms)"))
    qt, qtc = values.get("qt_interval"), values.get("qtc_interval")
    seconds = rr["number"] / 1000 if rr else 60 / hr["number"] if hr else None
    if qt and qtc and seconds:
        formulas = {"Bazett": qt["number"] / math.sqrt(seconds), "Fridericia": qt["number"] / seconds ** (1 / 3),
                    "Framingham": qt["number"] + 154 * (1 - seconds), "Hodges": qt["number"] + 1.75 * (60 / seconds - 60)}
        if min(abs(qtc["number"] - v) for v in formulas.values()) > 25:
            issues.append(issue(WARNING, "W-CROSS-CHECK", "ecg.measurements.qtc_interval",
                                f"QTc {qtc['number']:g} ms fits no usual correction of QT {qt['number']:g} ms at this "
                                f"rate (Bazett gives {formulas['Bazett']:.0f}): re-read QT, QTc and the heart rate"))
    return values


def _check_study(study: dict, dtype: str, issues: list) -> dict | None:
    key = study.get("key")
    entry = catalog.studies().get(key) if key else None
    if key and not entry:
        issues.append(issue(ERROR, "E-STUDY-UNKNOWN", "study.key", f"{key!r} is not a catalog key "
                            "(see: catalog find --study)"))
    elif entry and entry["type"] != dtype:
        issues.append(issue(ERROR, "E-STUDY-TYPE", "study.key", f"{key!r} is a {entry['type']} study, "
                            f"the document is a {dtype}"))
        entry = None
    elif not key and "procedure" in study:
        hits = [s["key"] for s in catalog.find(study["procedure"], "study") if s["type"] == dtype]
        level, code = (WARNING, "W-STUDY-AVAILABLE") if hits else (INFO, "I-UNCODED")
        issues.append(issue(level, code, "study.key",
                            f"no study key given but the catalog has {', '.join(hits[:4])}" if hits else
                            f"procedure is not in the catalog: stored as a generic {dtype} study with its printed name"))
    if "impression" not in study:
        issues.append(issue(WARNING, "W-NO-CONCLUSION", "study.impression",
                            "no impression/conclusion transcribed: confirm the paper has none"))
    return entry


def summary(issues: list[dict]) -> dict:
    return {level: sum(1 for i in issues if i["level"] == level) for level in (ERROR, WARNING, INFO)}
