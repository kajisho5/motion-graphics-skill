"""One lower third per CSV row (docs/decisions.md ADR-27): `motion-graphics lower-thirds <file.csv>` prints a request document.

Columns (a header row is required; names are case-insensitive): `name` (required), `title` (the affiliation or role line),
`start` and `end` (seconds; when absent the rows are laid one after another from --start, each --duration long, --gap apart),
`id`, `lang` and `profile` (a text-fit profile, e.g. ja-kinsoku). Every element gets a text fit with the box given on the
command line. The document is validated (model.parse_request) before it is printed; nothing is rendered or read but the CSV."""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from . import REQUEST_SCHEMA_VERSION, SKILL_ID
from .errors import MotionGraphicsError
from .model import MAX_ELEMENTS, parse_request

COLUMNS = ("id", "name", "title", "start", "end", "lang", "profile")


def _seconds(value: str, where: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a number of seconds: {value!r}", {"field": where})


def lower_thirds_document(text: str, video: str, output: str, *, fit_width: int, max_lines: int = 2, min_scale: float = 0.6,
                          start: float = 0.0, duration: float = 4.0, gap: float = 0.5, style: Optional[str] = None,
                          overwrite: bool = False) -> Dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise MotionGraphicsError("INVALID_REQUEST", "the CSV has no header row", {"field": "csv"})
    header = {h.strip().lower(): h for h in reader.fieldnames if h is not None}
    unknown = sorted(h for h in header if h not in COLUMNS)
    if unknown or "name" not in header:
        raise MotionGraphicsError("INVALID_REQUEST", f"CSV columns must include name and be among {list(COLUMNS)}: unknown {unknown}",
                                  {"field": "csv", "unknown": unknown, "allowed": list(COLUMNS)})
    elements: List[Dict[str, Any]] = []
    t = start
    for n, row in enumerate(reader, start=1):
        get = {k: (row.get(orig) or "").strip() for k, orig in header.items()}
        if not any(get.values()):
            continue
        where = f"csv row {n}"
        if len(elements) >= MAX_ELEMENTS:
            raise MotionGraphicsError("INVALID_REQUEST", f"the CSV has more than {MAX_ELEMENTS} rows", {"field": "csv"})
        s = _seconds(get["start"], f"{where}.start") if get.get("start") else t
        e = _seconds(get["end"], f"{where}.end") if get.get("end") else s + duration
        fit: Dict[str, Any] = {"width": fit_width, "max_lines": max_lines, "min_scale": min_scale}
        if get.get("lang"):
            fit["lang"] = get["lang"]
        if get.get("profile"):
            fit["profile"] = get["profile"]
        params: Dict[str, Any] = {"name": get.get("name", "")}
        if get.get("title"):
            params["title"] = get["title"]
        el: Dict[str, Any] = {"id": get.get("id") or f"lt{n:02d}", "type": "lower_third", "start": s, "end": e, "parameters": params, "fit": fit}
        elements.append(el)
        t = e + gap
    if not elements:
        raise MotionGraphicsError("INVALID_REQUEST", "the CSV has no rows", {"field": "csv"})
    doc: Dict[str, Any] = {"schema": f"{SKILL_ID}/request@{REQUEST_SCHEMA_VERSION}", "video": {"path": video},
                           "output": {"path": output, "overwrite": overwrite}, "elements": elements}
    if style:
        doc["options"] = {"style": style}
    parse_request(doc)
    return doc
