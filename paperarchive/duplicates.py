"""The same paper taken in twice.

`store.intake` refuses a file that is already in the archive, alone or as a page of another document. This
module catches what has different bytes: the same paper photographed again, scanned instead of
photographed, or a report taken in once in part and once whole. It compares what the transcriptions say.

Only the document taken in later is flagged, so the first one keeps its approval and stays on the server.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import catalog, extraction, store
from .extraction import ERROR, WARNING, issue


def _norm(text) -> str:
    """Comparison form of printed text: no case, diacritics, spaces; decimal comma as point."""
    return re.sub(r"\s+", "", catalog.fold(str(text))).replace(",", ".")


def summary(parsed: dict) -> tuple[dict, tuple]:
    """(header, body) of a validated transcription."""
    x, dtype, dates = parsed["doc"], parsed["type"], parsed["dates"]
    when = dates.get(dates.get("effective") or "")
    head = {"type": dtype, "when": when, "day": when[:10] if when else None,
            "facility": catalog.slug((x.get("facility") or {}).get("name", "")),
            "number": _norm((x.get("document") or {}).get("document_number", "")) or None,
            "title": _norm((x.get("study") or {}).get("procedure") or (x.get("procedure") or {}).get("name")
                           or (x.get("document") or {}).get("title") or "")}
    rows = {((r["code"] or {}).get("loinc") or _norm(r["name"]), _norm(r["value_text"]))
            for r in parsed["results"] + parsed["measurements"]}
    rows |= {(key, _norm(value["text"])) for key, value in parsed["ecg"].items()}
    rows |= {((d["code"] or {}).get("cvx") or _norm(d["vaccine"]), d["date"] or "", _norm(d["lot"] or ""))
             for d in parsed["doses"]}
    rows |= {("due", (d["code"] or {}).get("cvx") or _norm(d["vaccine"]), d["date"]) for d in parsed["due"]}
    blocks = {"ecg": ("ecg", ("rhythm", "machine_interpretation", "conclusion")),
              "visit": ("visit", extraction.VISIT_SECTIONS),
              "procedure": ("procedure", ("diagnosis", "postoperative_diagnosis", "description")),
              "vaccination": ("vaccination", ("screening",))}.get(dtype, ("study", ("findings", "impression")))
    section = x.get(blocks[0]) or {}
    texts = tuple(_norm(section[k]) for k in blocks[1] if section.get(k))
    return head, (frozenset(rows), texts)


def shots(parsed: dict) -> dict:
    """(vaccine, day) -> dose index, for the doses a document records (an order is not a shot)."""
    return {((d["code"] or {}).get("cvx") or _norm(d["vaccine"]), d["date"][:10]): d["index"]
            for d in reversed(parsed["doses"]) if d["date"] and d["given"] != "unknown"}


class Index:
    """Summaries of every transcribed document, built once per command."""

    def __init__(self, root: Path, people: dict | None = None, today: dt.date | None = None):
        people = people if people is not None else store.load_people(root)
        self.entries: list[tuple[store.Document, tuple]] = []
        self.shots: list[tuple[store.Document, dict]] = []
        for doc in store.documents(root):
            try:
                raw = doc.extraction()
            except store.ArchiveError:
                continue                                    # not transcribed yet, or not valid JSON
            _, parsed = extraction.validate(raw, people.get(doc.meta["person"]), today)
            if parsed["dates"]:
                self.entries.append((doc, summary(parsed)))
            if parsed["doses"]:
                self.shots.append((doc, shots(parsed)))

    def issues(self, doc: store.Document, parsed: dict) -> list[dict]:
        if not parsed["dates"]:
            return []
        same_paper = {other for other, _ in self._documents(doc, parsed, either_order=True)}
        return [i for _, i in self._documents(doc, parsed)] + self._shots(doc, parsed, same_paper)

    def _documents(self, doc: store.Document, parsed: dict, either_order=False) -> list[tuple[str, dict]]:
        """(other document id, issue) for each earlier document (or any, `either_order`) that looks like the same
        paper."""
        head, body = summary(parsed)
        if not head["day"]:
            return []
        out = []
        for other, (other_head, other_body) in self.entries:
            if other.id == doc.id or other.meta["person"] != doc.meta["person"]:
                continue
            if other.order > doc.order and not either_order:
                continue                                    # the later of the two carries the issue
            if any(other_head[k] != head[k] for k in ("type", "day", "facility")):
                continue
            if other_head["number"] and head["number"] and other_head["number"] != head["number"]:
                continue                                    # two numbered reports with different numbers
            if other_body == body and other_head["when"] == head["when"]:
                out.append((other.id, issue(ERROR, "E-DUPLICATE-DOCUMENT", "document",
                                 f"the same paper as {other.id} (taken in {other.meta['intake_at']}): same "
                                 f"{head['type']}, date, facility and content. Ask the owner, then remove one of "
                                 f"them: paper_archive.py remove <id>")))
                continue
            rows, other_rows = body[0], other_body[0]
            overlap = len(rows & other_rows) / min(len(rows), len(other_rows)) if rows and other_rows else 0
            if other_head["title"] == head["title"] or overlap >= 0.5:
                why = f"{overlap:.0%} of the results are identical" if overlap >= 0.5 else "the same title"
                out.append((other.id, issue(WARNING, "W-POSSIBLE-DUPLICATE", "document",
                                 f"possibly the same paper as {other.id}: same {head['type']}, date and facility, "
                                 f"{why}, but the transcriptions differ. If it is the same paper, compare both with "
                                 "it and remove the wrong one; if it is a reissued report, the owner decides which "
                                 "stays; if they are different papers, accept the warning")))
        return out

    def _shots(self, doc: store.Document, parsed: dict, flagged: set) -> list[dict]:
        """A shot on a loose card and again in the booklet: two documents, one dose. Any order: either may be
        approved first. Not repeated for a document already reported as the same paper."""
        mine, out = shots(parsed), []
        for other, theirs in self.shots:
            if other.id == doc.id or other.id in flagged or other.meta["person"] != doc.meta["person"]:
                continue
            for key in sorted(mine.keys() & theirs.keys(), key=lambda k: mine[k]):
                out.append(issue(WARNING, "W-DOSE-ELSEWHERE", f"vaccination.doses[{mine[key]}]",
                                 f"the same vaccine on {key[1]} is also doses[{theirs[key]}] of {other.id}: one "
                                 "shot written on two papers would be in the history twice. Tell the owner; if it "
                                 "is the same shot, they decide whether both papers stay"))
        return out
