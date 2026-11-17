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
            "title": _norm((x.get("study") or {}).get("procedure") or (x.get("document") or {}).get("title") or "")}
    rows = {((r["code"] or {}).get("loinc") or _norm(r["name"]), _norm(r["value_text"]))
            for r in parsed["results"] + parsed["measurements"]}
    rows |= {(key, _norm(value["text"])) for key, value in parsed["ecg"].items()}
    blocks = {"ecg": ("ecg", ("rhythm", "machine_interpretation", "conclusion")),
              "visit": ("visit", extraction.VISIT_SECTIONS)}.get(dtype, ("study", ("findings", "impression")))
    section = x.get(blocks[0]) or {}
    texts = tuple(_norm(section[k]) for k in blocks[1] if section.get(k))
    return head, (frozenset(rows), texts)


class Index:
    """Summaries of every transcribed document, built once per command."""

    def __init__(self, root: Path, people: dict | None = None, today: dt.date | None = None):
        people = people if people is not None else store.load_people(root)
        self.entries: list[tuple[store.Document, tuple]] = []
        for doc in store.documents(root):
            try:
                raw = doc.extraction()
            except store.ArchiveError:
                continue                                    # not transcribed yet, or not valid JSON
            _, parsed = extraction.validate(raw, people.get(doc.meta["person"]), today)
            if parsed["dates"]:
                self.entries.append((doc, summary(parsed)))

    def issues(self, doc: store.Document, parsed: dict) -> list[dict]:
        if not parsed["dates"]:
            return []
        head, body = summary(parsed)
        if not head["day"]:
            return []
        out = []
        for other, (other_head, other_body) in self.entries:
            if other.id == doc.id or other.meta["person"] != doc.meta["person"]:
                continue
            if other.order > doc.order:
                continue                                    # the later of the two carries the issue
            if any(other_head[k] != head[k] for k in ("type", "day", "facility")):
                continue
            if other_head["number"] and head["number"] and other_head["number"] != head["number"]:
                continue                                    # two numbered reports with different numbers
            if other_body == body and other_head["when"] == head["when"]:
                out.append(issue(ERROR, "E-DUPLICATE-DOCUMENT", "document",
                                 f"the same paper as {other.id} (taken in {other.meta['intake_at']}): same "
                                 f"{head['type']}, date, facility and content. Ask the owner, then remove one of "
                                 f"them: paper_archive.py remove <id>"))
                continue
            rows, other_rows = body[0], other_body[0]
            overlap = len(rows & other_rows) / min(len(rows), len(other_rows)) if rows and other_rows else 0
            if other_head["title"] == head["title"] or overlap >= 0.5:
                why = f"{overlap:.0%} of the results are identical" if overlap >= 0.5 else "the same title"
                out.append(issue(WARNING, "W-POSSIBLE-DUPLICATE", "document",
                                 f"possibly the same paper as {other.id}: same {head['type']}, date and facility, "
                                 f"{why}, but the transcriptions differ. If it is the same paper, compare both with "
                                 "it and remove the wrong one; if it is a reissued report, the owner decides which "
                                 "stays; if they are different papers, accept the warning"))
        return out
