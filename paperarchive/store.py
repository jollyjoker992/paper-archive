"""The data directory: people, documents, intake and the owner's approval.

<data dir>/
  people.toml                      who the archive holds documents for
  documents/<doc id>/
    original/NN-<name>             the scans as given, never changed
    document.pdf                   the digitized copy (loaded as a FHIR Binary)
    meta.json                      person, intake time, hashes
    extraction.json                the agent's transcription
    review.json                    the owner's approval of exactly that transcription
    fhir.ndjson                    the mapped resources
  fhir/shared.ndjson               Patients and Organizations
  fhir/manifest.json, fhir/load-manifest.json
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tomllib
from pathlib import Path

from . import catalog, pdf

REPO_DIR = Path(__file__).resolve().parent.parent


class ArchiveError(Exception):
    """Something the caller must fix (exit code 2)."""


def env(name: str, default: str = "") -> str:
    """Real environment first, then <repo>/.env."""
    if name in os.environ:
        return os.environ[name]
    path = REPO_DIR / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip('"').strip("'")
    return default


def data_dir() -> Path:
    return Path(env("PAPER_ARCHIVE_DIR", str(REPO_DIR / "data"))).expanduser()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- people


def load_people(root: Path) -> dict[str, dict]:
    path = root / "people.toml"
    if not path.is_file():
        raise ArchiveError(f"{path} not found: copy people.example.toml there and fill it in")
    people = {}
    for person in tomllib.loads(path.read_text(encoding="utf-8")).get("person", []):
        pid = person.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", pid) or not person.get("name"):
            raise ArchiveError(f"{path}: every person needs an id (a-z, 0-9, - and _) and a name")
        if person.get("sex") not in (None, "male", "female"):
            raise ArchiveError(f"{path}: {pid}: sex is male or female")
        if "birth_date" in person:
            person["birth_date"] = str(person["birth_date"])
            dt.date.fromisoformat(person["birth_date"])
        person.setdefault("timezone", "Asia/Ho_Chi_Minh")
        people[pid] = person
    if not people:
        raise ArchiveError(f"{path} lists no [[person]]")
    return people


# ---------------------------------------------------------------- documents


class Document:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name
        self.meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))

    @property
    def order(self) -> tuple:
        """Intake order: time, then id (documents taken in before microsecond times existed)."""
        return dt.datetime.fromisoformat(self.meta["intake_at"]), self.id

    @property
    def extraction_path(self) -> Path:
        return self.path / "extraction.json"

    def extraction(self) -> dict:
        if not self.extraction_path.is_file():
            raise ArchiveError(f"{self.id}: no extraction.json yet")
        try:
            return json.loads(self.extraction_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ArchiveError(f"{self.id}: extraction.json is not valid JSON: {exc}") from None

    def review(self) -> dict | None:
        path = self.path / "review.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def approved(self) -> bool:
        review = self.review()
        return bool(review and self.extraction_path.is_file()
                    and review["extraction_sha256"] == sha256(self.extraction_path))

    def state(self) -> str:
        if not self.extraction_path.is_file():
            return "intake"
        if not self.approved():
            return "changed-after-approval" if self.review() else "extracted"
        return "mapped" if (self.path / "fhir.ndjson").is_file() else "approved"


def documents(root: Path) -> list[Document]:
    folder = root / "documents"
    if not folder.is_dir():
        return []
    return [Document(p) for p in sorted(folder.iterdir()) if p.name.startswith("d_") and (p / "meta.json").is_file()]


def document(root: Path, doc_id: str) -> Document:
    matches = [d for d in documents(root) if d.id == doc_id or d.id.startswith(doc_id)]
    if len(matches) != 1:
        raise ArchiveError(f"{doc_id!r} matches {len(matches)} documents (see: status)")
    return matches[0]


def intake(root: Path, person_id: str, files: list[Path]) -> Document:
    """Copy the scans into the archive and build document.pdf. The same scans always get the same id."""
    people = load_people(root)
    if person_id not in people:
        raise ArchiveError(f"unknown person {person_id!r} (people.toml has: {', '.join(people)})")
    missing = [str(f) for f in files if not f.is_file()]
    if missing or not files:
        raise ArchiveError(f"not a file: {', '.join(missing) or '(no files given)'}")
    suffixes = {f.suffix.lower() for f in files}
    is_pdf = suffixes == {".pdf"}
    if is_pdf and len(files) != 1:
        raise ArchiveError("give one PDF, or the page images in reading order")
    if not is_pdf and not suffixes <= pdf.IMAGE_SUFFIXES:
        raise ArchiveError(f"unsupported file type: {', '.join(sorted(suffixes - pdf.IMAGE_SUFFIXES))}")

    digests = [sha256(f) for f in files]
    if len(set(digests)) != len(digests):
        raise ArchiveError("the same file is given twice")
    known = {}
    for other in documents(root):
        for original in other.meta["originals"]:
            known.setdefault(original["sha256"], (other, original["file"]))
        known.setdefault(other.meta["pdf"]["sha256"], (other, other.meta["pdf"]["file"]))
    for src, digest in zip(files, digests):
        if digest in known:
            other, where = known[digest]
            raise ArchiveError(f"{src.name} is already in the archive: {other.id}/{where} "
                               f"({other.meta['person']}, state: {other.state()}). Take in only the pages that "
                               "are not there yet, or remove that document first")
    doc_id = "d_" + hashlib.sha256("\n".join(digests).encode()).hexdigest()[:16]
    folder = root / "documents" / doc_id
    if folder.exists():
        raise ArchiveError(f"these scans are already in the archive as {doc_id} "
                           f"({Document(folder).meta['person']}, state: {Document(folder).state()})")
    work = root / "documents" / f".{doc_id}.tmp"
    shutil.rmtree(work, ignore_errors=True)
    (work / "original").mkdir(parents=True)
    try:
        originals = []
        for n, (src, digest) in enumerate(zip(files, digests), 1):
            name = f"{n:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '_', src.name)}"
            shutil.copyfile(src, work / "original" / name)
            originals.append({"file": f"original/{name}", "sha256": digest, "bytes": src.stat().st_size})
        target = work / "document.pdf"
        if is_pdf:
            shutil.copyfile(files[0], target)
            pages = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", target.read_bytes())) or None
        else:
            pages = pdf.images_to_pdf([work / o["file"] for o in originals], target)
        raw = target.read_bytes()
        write_json(work / "meta.json", {
            "id": doc_id, "person": person_id,
            "intake_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"), "originals": originals,
            "pdf": {"file": "document.pdf", "sha256": hashlib.sha256(raw).hexdigest(),
                    "sha1_base64": base64.b64encode(hashlib.sha1(raw).digest()).decode(),
                    "bytes": len(raw), "pages": pages}})
        os.replace(work, folder)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return Document(folder)


def rebuild_pdf(doc: Document) -> bool:
    """Build document.pdf again from the original scans (after a fix in the PDF builder). Returns True when
    it changed. Not for a document made from a PDF: that PDF is the original."""
    originals = [doc.path / o["file"] for o in doc.meta["originals"]]
    if [p.suffix.lower() for p in originals] == [".pdf"]:
        return False
    work = doc.path / ".document.pdf.tmp"
    pages = pdf.images_to_pdf(originals, work)
    raw = work.read_bytes()
    if hashlib.sha256(raw).hexdigest() == doc.meta["pdf"]["sha256"]:
        work.unlink()
        return False
    os.replace(work, doc.path / "document.pdf")
    doc.meta["pdf"] = {"file": "document.pdf", "sha256": hashlib.sha256(raw).hexdigest(),
                       "sha1_base64": base64.b64encode(hashlib.sha1(raw).digest()).decode(),
                       "bytes": len(raw), "pages": pages}
    write_json(doc.path / "meta.json", doc.meta)
    return True


def remove(doc: Document) -> None:
    """Delete a document: scans, PDF, transcription, approval. The next map and load take it off the server."""
    shutil.rmtree(doc.path)


def approve(doc: Document, by: str, issues: list[dict], accept_warnings: bool) -> dict:
    """Record the owner's approval of the transcription as it is now."""
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    if errors:
        raise ArchiveError(f"{doc.id}: {len(errors)} error(s); a transcription with errors cannot be approved")
    if warnings and not accept_warnings:
        raise ArchiveError(f"{doc.id}: {len(warnings)} warning(s) to settle against the paper first; "
                           "when the owner has checked each one, approve with --accept-warnings")
    review = {"approved_by": by, "approved_at": now_iso(), "extraction_sha256": sha256(doc.extraction_path),
              "catalog_version": catalog.catalog_version(),
              "accepted_warnings": [f"{w['code']} {w['path']}" for w in warnings]}
    write_json(doc.path / "review.json", review)
    return review
