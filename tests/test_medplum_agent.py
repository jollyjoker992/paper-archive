"""Offline tests of tools/medplum_agent.py's helpers (the queries themselves run against a real Medplum
in agentic-assistant's test stack)."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import medplum_agent as agent  # noqa: E402


class Helpers(unittest.TestCase):
    def test_query_to_loinc_codes(self):
        self.assertEqual(agent.loinc_codes("ldl"), ["22748-8", "2089-1"])        # both units of the catalog entry
        self.assertEqual(agent.loinc_codes("2093-3"), ["2093-3"])
        self.assertEqual(agent.loinc_codes("RDW-SD"), [])                        # not coded: searched by name
        self.assertEqual(agent.loinc_codes("Random Glucose"), [])

    def test_values_as_printed(self):
        obs = {"code": {"text": "CRP định lượng", "coding": [{"system": agent.LOINC, "code": "1988-5", "display": "C reactive protein"}]},
               "valueQuantity": {"value": 0.5, "comparator": "<", "unit": "mg/L"},
               "referenceRange": [{"text": "< 5"}],
               "extension": [{"url": f"{agent.BASE}/StructureDefinition/printed-value", "valueString": "< 0,5"}],
               "category": [{"coding": [{"code": "laboratory"}]}]}
        self.assertEqual(agent.result_row(obs), {"name": "CRP định lượng", "loinc": "1988-5", "value": 0.5,
                                                  "comparator": "<", "unit": "mg/L", "printed": "< 0,5",
                                                  "reference": "< 5", "category": "laboratory"})
        bp = {"code": {"text": None}, "component": [{"valueQuantity": {"value": 118, "unit": "mmHg"}},
                                                     {"valueQuantity": {"value": 76, "unit": "mmHg"}}]}
        self.assertEqual(agent.value_of(bp), {"value": "118/76", "unit": "mmHg"})

    def test_how_an_ecg_was_recorded(self):
        obs = {"code": {"text": None, "coding": [{"system": agent.LOINC, "code": "8867-4", "display": "Heart rate"}]},
               "valueQuantity": {"value": 68, "unit": "/min"}, "method": {"text": "ECG, 1 lead"},
               "device": {"display": "Lead I, 25 mm/s"}, "category": [{"coding": [{"code": "procedure"}]}]}
        self.assertEqual(agent.result_row(obs), {"name": "Heart rate", "loinc": "8867-4", "value": 68, "unit": "/min",
                                                  "method": "ECG, 1 lead", "device": "Lead I, 25 mm/s",
                                                  "category": "procedure"})

    def test_scan_link(self):
        bid = "58aef3f5-42c1-4b9f-9c39-c5dff2bb07d8"
        self.assertEqual(agent.binary_id(f"Binary/{bid}"), bid)
        self.assertEqual(agent.binary_id(f"http://localhost:8303/storage/{bid}/39ab3129-f042-4692-9514-f133a551cdf3"
                                         "?Expires=1&Signature=x"), bid)
        self.assertIsNone(agent.binary_id(""))
        self.assertIsNone(agent.binary_id("https://example.org/file.pdf"))

    def test_identifiers_and_ranges(self):
        composition = {"identifier": {"system": agent.system("paper-composition"), "value": "d_1"}}
        self.assertEqual(agent.identifier(composition, "paper-composition"), "d_1")
        self.assertTrue(agent.in_range("2023-02-13T14:04:00+07:00", "2023-02-13", "2023-02-13"))
        self.assertFalse(agent.in_range("2023-02-13", "2023-02-14", None))

    def test_vaccination_rows(self):
        ident = lambda kind, value: [{"system": agent.system(kind), "value": value}]  # noqa: E731
        hpv = {"coding": [{"system": agent.CVX, "code": "165", "display": "HPV9"}], "text": "Gardasil 9"}
        given = {"resourceType": "Immunization", "identifier": ident("paper-immunization", "d_1/hpv9"),
                 "status": "completed", "vaccineCode": hpv, "occurrenceDateTime": "2026-10-02", "lotNumber": "X1",
                 "performer": [{"actor": {"reference": "Organization/o"}},
                               {"function": {"coding": [{"code": "AP"}]}, "actor": {"display": "ĐD. A"}}],
                 "protocolApplied": [{"doseNumberString": "Mũi 2"}]}
        self.assertEqual(agent.dose_row(given), {"date": "2026-10-02", "vaccine": "Gardasil 9", "cvx": "165",
                                                 "status": "given", "dose": "Mũi 2", "lot": "X1", "given_by": "ĐD. A",
                                                 "document": "d_1"})
        order = {"resourceType": "MedicationRequest", "identifier": ident("paper-medication-request", "d_1/flu"),
                 "medicationCodeableConcept": {"text": "Vaxigrip Tetra"}, "authoredOn": "2026-10-02"}
        self.assertEqual(agent.dose_row(order)["status"], "ordered, not known whether given")
        due = {"resourceType": "ImmunizationRecommendation", "identifier": ident("paper-recommendation", "d_1/hpv9"),
               "date": "2026-10-02", "recommendation": [{"vaccineCode": [hpv], "dateCriterion": [{"value": "2027-04-02"}]}]}
        self.assertEqual(agent.dose_row(due), {"date": "2027-04-02", "vaccine": "Gardasil 9", "cvx": "165",
                                               "status": "next dose due (printed)", "printed_on": "2026-10-02",
                                               "document": "d_1"})


if __name__ == "__main__":
    unittest.main()
