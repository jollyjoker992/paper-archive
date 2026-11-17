#!/usr/bin/env python3
"""Read-only access to the paper records in Medplum, for agents and scripts. Standard library only.

Always uses the *agent* client (PAPER_FHIR_AGENT_CLIENT_ID / _SECRET, from the environment or the
repository's .env), whose access policy allows reading and searching only.

    python3 tools/medplum_agent.py token                 print an access token
    python3 tools/medplum_agent.py headers               print {"Authorization": "Bearer ..."} (MCP headersHelper)
    python3 tools/medplum_agent.py people                each person: Patient id, paper user id, name, sex, birth date
    python3 tools/medplum_agent.py documents [--person P] [--type T] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                                         documents, newest first: id, type, title, date, facility, conclusion
    python3 tools/medplum_agent.py document DOC          one document: report, conclusion, every result, vital signs
    python3 tools/medplum_agent.py results QUERY [--person P] [--from ...] [--to ...]
                                                         one test over time; QUERY is a catalog key (ldl, hba1c),
                                                         a LOINC code, or words of the printed name
    python3 tools/medplum_agent.py pdf DOC [--out FILE]  save the scan of a document (default .cache/pdf/DOC.pdf)

PERSON is a Patient id, a paper user id or a name; it can be left out when there is only one person.
DOC is a document id (d_..., a unique prefix is enough). Output is JSON (pdf prints the file path).

Everything printed is what the paper said: the value and unit as printed, the printed reference range,
and an interpretation only when the paper carried one. Results of the same test in different units
(mmol/L and mg/dL) are listed as they are; they are not converted.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from paperarchive import DEFAULT_BASE, catalog, load  # noqa: E402

CACHE_DIR = REPO / ".cache"
LOINC = "http://loinc.org"


def read_env() -> dict[str, str]:
    env = {}
    path = REPO / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.removeprefix("export ").partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("PAPER_")})
    return env


ENV = read_env()
SERVER = ENV.get("PAPER_FHIR_URL", "http://localhost:8303").rstrip("/")
BASE = ENV.get("PAPER_FHIR_BASE", DEFAULT_BASE).rstrip("/")


def system(kind: str) -> str:
    return f"{BASE}/identifier/{kind}"


def fail(message: str) -> None:
    print(f"medplum_agent: {message}", file=sys.stderr)
    sys.exit(2)


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


class AgentClient(load.FhirClient):
    """The loader's HTTP client (retries, backoff) with the agent's credentials and a token cache, so frequent
    calls do not hit Medplum's sign-in rate limit."""

    def _token_request(self) -> str:
        cache = CACHE_DIR / "medplum-agent-token.json"
        try:
            cached = json.loads(cache.read_text())
            if cached.get("client_id") == self.client_id and cached.get("server") == self.url \
                    and cached.get("expires_at", 0) - time.time() > 300:
                return cached["access_token"]
        except (OSError, ValueError):
            pass
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self.secret}).encode()
        status, body = self._send("POST", "/oauth2/token", form, "application/x-www-form-urlencoded",
                                  "application/json", auth=False)
        if status != 200:
            fail(f"token request failed: HTTP {status}")
        write_private(cache, json.dumps({"client_id": self.client_id, "server": self.url,
                                         "access_token": body["access_token"],
                                         "expires_at": time.time() + int(body.get("expires_in", 3600))}).encode())
        return body["access_token"]


_client: AgentClient | None = None


def client() -> AgentClient:
    global _client
    if _client is None:
        cid, secret = ENV.get("PAPER_FHIR_AGENT_CLIENT_ID"), ENV.get("PAPER_FHIR_AGENT_CLIENT_SECRET")
        if not cid or not secret:
            fail("PAPER_FHIR_AGENT_CLIENT_ID / _SECRET are not set")
        _client = AgentClient(SERVER, cid, secret, max_attempts=3, timeout=60)
    return _client


def get(path: str):
    try:
        return client().request("GET", path)[1]
    except load.LoadError as exc:
        fail(f"{exc} (is Medplum at {SERVER} running?)")


def search(rtype: str, **params) -> list[dict]:
    """Every resource of a search, following the pages."""
    query = urllib.parse.urlencode({"_count": "1000", **{k.replace("_", "-") if not k.startswith("_") else k: v
                                                         for k, v in params.items()}}, safe=":/|,")
    path, out = f"/fhir/R4/{rtype}?{query}", []
    while path:
        bundle = get(path) or {}
        out += [e["resource"] for e in bundle.get("entry") or [] if e.get("search", {}).get("mode") != "include"
                and e["resource"].get("resourceType") == rtype]
        nxt = next((link["url"] for link in bundle.get("link") or [] if link.get("relation") == "next"), None)
        path = nxt[len(SERVER):] if nxt and nxt.startswith(SERVER) else None
    return out


def identifier(resource: dict, kind: str) -> str | None:
    idents = resource.get("identifier")
    idents = idents if isinstance(idents, list) else [idents] if idents else []
    return next((i["value"] for i in idents if i.get("system") == system(kind)), None)


# --- people ------------------------------------------------------------------------------------------

def people() -> list[dict]:
    return [{"patient": p["id"], "paper_user_id": identifier(p, "paper-user"),
             "name": (p.get("name") or [{}])[0].get("text"), "sex": p.get("gender"), "birth_date": p.get("birthDate")}
            for p in search("Patient", identifier=system("paper-user") + "|")]


def patient_of(person: str | None) -> dict:
    everyone = people()
    if not everyone:
        fail("no paper records loaded yet")
    if person is None:
        if len(everyone) > 1:
            fail("several people: pass --person " + "|".join(p["paper_user_id"] for p in everyone))
        return everyone[0]
    folded = catalog.fold(person)
    for p in everyone:
        if person in (p["patient"], p["paper_user_id"]) or folded == catalog.fold(p["name"] or ""):
            return p
    fail(f"no person {person!r} with paper records (see: people)")


# --- documents ---------------------------------------------------------------------------------------

def clinical_date(doc_ref: dict) -> str:
    return ((doc_ref.get("context") or {}).get("period") or {}).get("start", "")


def in_range(date: str, start: str | None, end: str | None) -> bool:
    day = date[:10]
    return (not start or day >= start) and (not end or day <= end)


def documents(person: str | None, dtype: str | None, start: str | None, end: str | None) -> list[dict]:
    patient = patient_of(person)
    refs = search("DocumentReference", subject=f"Patient/{patient['patient']}",
                  identifier=system("paper-document") + "|")
    orgs = {f"Organization/{o['id']}": o.get("name") for o in search("Organization", identifier=system("paper-organization") + "|")}
    reports = {identifier(r, "paper-report"): r for r in search("DiagnosticReport", subject=f"Patient/{patient['patient']}")}
    diagnoses = {(identifier(c, "paper-condition") or "").split("/")[0]: c
                 for c in search("Condition", subject=f"Patient/{patient['patient']}")}
    rows = []
    for ref in refs:
        doc_id = identifier(ref, "paper-document")
        kind = (ref.get("type") or {}).get("text")
        if dtype and catalog.fold(dtype) not in catalog.fold(kind or ""):
            continue
        if not in_range(clinical_date(ref), start, end):
            continue
        report = reports.get(doc_id) or {}
        conclusion = report.get("conclusion") or ((diagnoses.get(doc_id) or {}).get("code") or {}).get("text")
        rows.append({"document": doc_id, "type": kind, "title": ref.get("description"), "date": clinical_date(ref),
                     "facility": orgs.get((ref.get("custodian") or {}).get("reference")),
                     "conclusion": conclusion, "results": len(report.get("result") or [])})
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def find_document(doc: str) -> dict:
    refs = [r for r in search("DocumentReference", identifier=system("paper-document") + "|")
            if (identifier(r, "paper-document") or "").startswith(doc)]
    if len(refs) != 1:
        fail(f"{doc!r} matches {len(refs)} documents (see: documents)")
    return refs[0]


def value_of(obs: dict) -> dict:
    out: dict = {}
    if "valueQuantity" in obs:
        q = obs["valueQuantity"]
        out["value"] = q.get("value")
        if q.get("comparator"):
            out["comparator"] = q["comparator"]
        if q.get("unit"):
            out["unit"] = q["unit"]
    elif "valueString" in obs:
        out["value"] = obs["valueString"]
    elif obs.get("component"):
        out["value"] = "/".join(str(c["valueQuantity"]["value"]) for c in obs["component"])
        out["unit"] = obs["component"][0]["valueQuantity"].get("unit")
    printed = next((e["valueString"] for e in obs.get("extension") or [] if e["url"].endswith("/printed-value")), None)
    if printed is not None:
        out["printed"] = printed
    if obs.get("referenceRange"):
        out["reference"] = obs["referenceRange"][0].get("text")
    if obs.get("interpretation"):
        out["interpretation"] = obs["interpretation"][0]["coding"][0].get("display")
    return out


def result_row(obs: dict) -> dict:
    coding = next((c for c in (obs.get("code") or {}).get("coding") or [] if c.get("system") == LOINC), None)
    return {"name": (obs.get("code") or {}).get("text") or (coding or {}).get("display"),
            "loinc": (coding or {}).get("code"), **value_of(obs),
            "category": ((obs.get("category") or [{}])[0].get("coding") or [{}])[0].get("code")}


def document(doc: str) -> dict:
    ref = find_document(doc)
    doc_id = identifier(ref, "paper-document")
    observations = search("Observation", derived_from=f"DocumentReference/{ref['id']}")
    order = {}
    report = next(iter(search("DiagnosticReport", identifier=f"{system('paper-report')}|{doc_id}")), None)
    for i, item in enumerate((report or {}).get("result") or []):
        order[item["reference"]] = i
    observations.sort(key=lambda o: order.get(f"Observation/{o['id']}", len(order)))
    out = {"document": doc_id, "type": (ref.get("type") or {}).get("text"), "title": ref.get("description"),
           "date": clinical_date(ref),
           "transcription": {e["url"]: next(v for k, v in e.items() if k.startswith("value"))
                             for x in ref.get("extension") or [] if x["url"].endswith("/transcription")
                             for e in x.get("extension") or []}}
    if report:
        out["conclusion"] = report.get("conclusion")
        out["interpreter"] = ((report.get("resultsInterpreter") or [{}])[0]).get("display")
    composition = next(iter(search("Composition", identifier=f"{system('paper-composition')}|{doc_id}")), None)
    if composition:
        out["sections"] = {s["title"]: s["text"]["div"] for s in composition.get("section") or []}
    vitals = [o for o in observations if (identifier(o, "paper-observation") or "").startswith("vital/")]
    out["results"] = [result_row(o) for o in observations if o not in vitals]
    out["vital_signs"] = [result_row(o) for o in vitals]
    return out


# --- one test over time ------------------------------------------------------------------------------

def loinc_codes(query: str) -> list[str]:
    entry = catalog.analytes().get(query)
    if entry:
        return [c["loinc"] for c in entry["codes"]]
    if any(ch.isdigit() for ch in query) and "-" in query and " " not in query:
        return [query]
    return []


def results(query: str, person: str | None, start: str | None, end: str | None) -> dict:
    patient = patient_of(person)
    codes = loinc_codes(query)
    if codes:
        observations = search("Observation", subject=f"Patient/{patient['patient']}",
                              code=",".join(f"{LOINC}|{c}" for c in codes))
    else:
        needle = catalog.fold(query)
        observations = [o for o in search("Observation", subject=f"Patient/{patient['patient']}")
                        if needle in catalog.fold((o.get("code") or {}).get("text") or "")]
    rows = []
    for obs in observations:
        ident = identifier(obs, "paper-observation")
        if not ident or not in_range(obs.get("effectiveDateTime", ""), start, end):
            continue
        rows.append({"date": obs.get("effectiveDateTime"), **result_row(obs),
                     "document": None if ident.startswith("vital/") else ident.split("/")[0]})
    rows.sort(key=lambda r: r["date"] or "")
    match = {"catalog_key": query} if query in catalog.analytes() else {"loinc": codes} if codes else {"text": query}
    return {"person": patient["paper_user_id"], **match, "results": rows}


# --- the scan ----------------------------------------------------------------------------------------

def binary_id(url: str) -> str | None:
    """The Binary id of an attachment link. The loader writes `Binary/<id>`; Medplum returns it as a signed
    link to its storage (`<base>/storage/<id>/<version>?Expires=...&Signature=...`), whose host is the server's
    public address and may not be reachable from here. The Binary is fetched through the API instead."""
    match = re.match(r"(?:Binary/|.*/storage/)([0-9a-f-]{36})(?:[/?]|$)", url or "")
    return match.group(1) if match else None


def save_pdf(doc: str, out: str | None) -> Path:
    ref = find_document(doc)
    binary_ref = binary_id(ref["content"][0]["attachment"].get("url", ""))
    if not binary_ref:
        fail("this document has no stored scan")
    target = Path(out) if out else CACHE_DIR / "pdf" / f"{identifier(ref, 'paper-document')}.pdf"
    write_private(target, download(f"/fhir/R4/Binary/{binary_ref}"))
    return target


def download(path: str) -> bytes:
    """A Binary's content. Asked for as the file itself (not as FHIR JSON), Medplum returns the bytes."""
    agent = client()
    for attempt in (1, 2):
        agent._token = agent._token or agent._token_request()
        request = urllib.request.Request(agent.url + path, headers={
            "Authorization": f"Bearer {agent._token}", "Accept": "application/pdf, application/octet-stream, */*"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:                 # cached token revoked or expired early
                agent._token = ""
                (CACHE_DIR / "medplum-agent-token.json").unlink(missing_ok=True)
                continue
            fail(f"GET {path}: HTTP {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            fail(f"GET {path}: {exc} (is Medplum at {SERVER} running?)")
        if data.startswith(b"{"):                                 # FHIR JSON after all: inline base64 data
            data = base64.b64decode(json.loads(data).get("data") or b"")
        if not data:
            fail(f"GET {path}: empty")
        return data
    raise AssertionError("unreachable")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    opt = lambda name: rest[rest.index(name) + 1] if name in rest else None  # noqa: E731
    positional = rest[0] if rest and not rest[0].startswith("--") else None
    if cmd == "token":
        print(client()._token_request())
        return 0
    if cmd == "headers":
        print(json.dumps({"Authorization": f"Bearer {client()._token_request()}"}))
        return 0
    if cmd == "people":
        result = people()
    elif cmd == "documents":
        result = documents(opt("--person"), opt("--type"), opt("--from"), opt("--to"))
    elif cmd in ("document", "pdf", "results") and not positional:
        fail(f"{cmd} needs an argument; see --help")
    elif cmd == "document":
        result = document(positional)
    elif cmd == "results":
        result = results(positional, opt("--person"), opt("--from"), opt("--to"))
    elif cmd == "pdf":
        print(save_pdf(positional, opt("--out")))
        return 0
    else:
        fail(f"unknown command {cmd!r}; see --help")
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        sys.exit(0)
