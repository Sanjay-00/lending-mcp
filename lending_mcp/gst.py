"""
Questions over a set of GSTR-3B returns that GST Analyser read: pure functions, no I/O.

The engine keys its output by PAN and GSTIN (canonical.borrowers[PAN].gstins[GSTIN]).
Masking those keys in place would let two GSTINs of one PAN collide, so normalise turns
each GSTIN into a record with an opaque entity_id ("gstin-1", "gstin-2", in the engine's
order) and rewrites every place keyed by GSTIN to that id. The cache keeps the full
identifiers as fields; masked forms are made per answer, and `scrub` then removes any full
identifier still quoted in text, a finding's explanation say.

Findings are the engine's attention signals, passed on as worded.
"""

from __future__ import annotations

from typing import Any, Dict, List

from lending_mcp.masking import mask_gstin, mask_tail, scrub

ENGINE = "GST Analyser"
# The per period figures the summary gives (spec 5.4), named as the engine names them.
FIGURES = ("aggregate_turnover_3b", "taxable_outward", "output_tax", "net_itc", "tax_paid_itc",
           "tax_paid_cash", "interest_paid", "late_fee_paid")
# A return's tables and per cell sources are large and only the engine's own workbook needs
# them; the summary is built from the series points.
_RETURN_KEYS = ("doc_id", "file_name", "period", "arn_date", "in_window", "filing", "extraction")


def normalise(report: dict) -> dict:
    entities: List[dict] = []
    by_gstin: Dict[str, str] = {}
    for _pan, borrower in ((report.get("canonical") or {}).get("borrowers") or {}).items():
        for gstin, g in (borrower.get("gstins") or {}).items():
            entity_id = f"gstin-{len(entities) + 1}"
            by_gstin[gstin] = entity_id
            record = {"entity_id": entity_id, **{k: v for k, v in g.items() if k != "returns"}}
            record["gstin"], record["pan"] = g.get("gstin") or gstin, g.get("pan") or _pan
            record["returns"] = [{k: r.get(k) for k in _RETURN_KEYS} for r in g.get("returns") or []]
            entities.append(record)

    files = []
    for f in report.get("files") or []:
        f = dict(f)
        f["entity_id"] = by_gstin.get(f.pop("gstin", None))
        files.append(f)

    confidence = dict(report.get("confidence") or {})
    confidence["data_coverage"] = {by_gstin.get(k, k): v for k, v in (confidence.get("data_coverage") or {}).items()}
    validation = report.get("validation") or {}

    return {
        "meta": report.get("meta") or {},
        "entities": entities,
        "consolidated": (report.get("canonical") or {}).get("consolidated"),
        "files": files,
        "findings": report.get("findings") or [],
        "validation": {"counts": validation.get("counts") or {},
                       "not_passed": [c for c in validation.get("checks") or [] if c.get("status") != "PASS"]},
        "confidence": confidence,
    }


def _identifiers(doc: dict) -> Dict[str, str]:
    out = {}
    for e in doc.get("entities") or []:
        if e.get("gstin"):
            out[str(e["gstin"])] = mask_gstin(e["gstin"])
        if e.get("pan"):
            out[str(e["pan"])] = mask_tail(e["pan"])
    return out


def limitations(doc: dict) -> List[str]:
    out = []
    files = doc.get("files") or []
    duplicates = [f for f in files if f.get("status") == "DUPLICATE_FILE"]
    skipped = [f for f in files if f.get("doc_type") != "GSTR3B" and f.get("status") != "DUPLICATE_FILE"]
    if skipped:
        out.append(f"{len(skipped)} file(s) were not GST returns and were skipped (listed in files)")
    if duplicates:
        out.append(f"{len(duplicates)} file(s) were byte-identical copies of another file and were counted once")
    failed = [f for f in doc.get("files") or [] if f.get("doc_type") == "GSTR3B" and f.get("status") not in
              ("PARSED", "DUPLICATE_FILE")]
    if failed:
        out.append(f"{len(failed)} GST return(s) could not be read (listed in files)")
    low = (doc.get("confidence") or {}).get("extraction_summary", {}).get("LOW")
    if low:
        out.append(f"{low} return(s) read with LOW extraction confidence: a printed total did not add up")
    for e in doc.get("entities") or []:
        cov = (e.get("series") or {}).get("coverage") or {}
        if cov.get("unavailable"):
            out.append(f"{e['entity_id']}: {len(cov['unavailable'])} period(s) of the window have no return")
    out.append("GSTR-3B turnover is self declared and excludes tax; findings are attention signals, not conclusions")
    return out


def envelope(doc: dict, unmasked: bool) -> Dict[str, Any]:
    return {
        "document_id": doc.get("document_id"),
        "document_type": "gst",
        "engine": ENGINE,
        "source_files": doc.get("source_files", []),
        "amounts_unit": "INR, rupees as printed",
        "limitations": limitations(doc),
        "masking": "off" if unmasked else "on",
    }


def _entity(e: dict, unmasked: bool) -> Dict[str, Any]:
    series = e.get("series") or {}
    out = {
        "entity_id": e["entity_id"],
        "gstin_masked": mask_gstin(e.get("gstin")),
        "pan_masked": mask_tail(e.get("pan")),
        "legal_names": e.get("legal_names"),
        "trade_names": e.get("trade_names"),
        "state_code": e.get("state_code"),
        "filing_pattern": e.get("filing_pattern"),
        "grain": series.get("grain"),
        "coverage": series.get("coverage"),
        "periods": [{
            "period": (p.get("period") or {}).get("label"),
            "key": (p.get("period") or {}).get("key"),
            "type": (p.get("period") or {}).get("type"),
            "financial_year": (p.get("period") or {}).get("fy"),
            "status": p.get("status"),
            "extraction_confidence": p.get("extraction_confidence"),
            "figures": {k: (p.get("base") or {}).get(k) for k in FIGURES},
            "ratios": p.get("ratios") or {},
            "note": p.get("note"),
        } for p in series.get("points") or []],
        "fy_totals": series.get("fy_totals"),
        "trends": series.get("trends"),
        "filing": [{
            "period": (r.get("period") or {}).get("label"),
            **{k: (r.get("filing") or {}).get(k) for k in ("due_date", "arn_date", "delay_days", "late")},
        } for r in e.get("returns") or []],
    }
    if unmasked:
        out["gstin"], out["pan"] = e.get("gstin"), e.get("pan")
    return out


def summary(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    meta = doc.get("meta") or {}
    out = {
        **envelope(doc, unmasked),
        "window": meta.get("window"),
        "interpretation": meta.get("interpretation"),
        "entities": [_entity(e, unmasked) for e in doc.get("entities") or []],
        "consolidated": doc.get("consolidated"),
        "findings": doc.get("findings"),
        "validation": doc.get("validation"),
        "confidence": doc.get("confidence"),
        "files": [{k: f.get(k) for k in ("file_name", "doc_type", "status", "reason", "entity_id")}
                  | {"period": f.get("period_label")} for f in doc.get("files") or []],
    }
    return out if unmasked else scrub(out, _identifiers(doc))


def loaded(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    """What load_gst_returns answers: which GSTINs and periods were found, and what was skipped."""
    if doc.get("status") == "reading":
        return doc
    out = {
        **envelope(doc, unmasked),
        "from_cache": doc.get("from_cache"),
        "window": (doc.get("meta") or {}).get("window"),
        "entities": [{
            "entity_id": e["entity_id"], "gstin_masked": mask_gstin(e.get("gstin")),
            **({"gstin": e.get("gstin")} if unmasked else {}),
            "legal_names": e.get("legal_names"), "filing_pattern": e.get("filing_pattern"),
            "periods_found": len((e.get("series") or {}).get("points") or []),
        } for e in doc.get("entities") or []],
        "files": [{k: f.get(k) for k in ("file_name", "doc_type", "status", "reason")} for f in doc.get("files") or []],
    }
    return out if unmasked else scrub(out, _identifiers(doc))
