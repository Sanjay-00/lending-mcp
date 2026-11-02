"""
Local check on real documents, through the same loader the tools use. Prints counts and
yes or no answers only: never a name, PAN, GSTIN, account number or amount, so its output
can be shared. Real documents never go in this repo.

    uv run python scripts/real_run.py bank <pdf> [<pdf> ...]

The folder of the first file is allowed for this run only (LENDING_MCP_ALLOWED_DIRS), and
the cache goes wherever LENDING_MCP_HOME points (default ~/.lending-mcp).
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path


def _allow(paths):
    os.environ["LENDING_MCP_ALLOWED_DIRS"] = os.pathsep.join(sorted({str(Path(p).resolve().parent) for p in paths}))


def bank_run(paths):
    from lending_mcp import bank, loader
    from lending_mcp.config import load_settings

    settings = load_settings()
    started = time.perf_counter()
    doc = loader.load_bank(paths, settings)
    seconds = round(time.perf_counter() - started, 1)
    summary = bank.summary(doc, unmasked=False)
    text = json.dumps(summary).lower()
    number = str(doc.get("account_number") or "")
    print(f"files                 {len(paths)} (merged: {'yes' if doc.get('merge') else 'no'})")
    print(f"load seconds          {seconds} (from cache: {doc['from_cache']})")
    print(f"transactions          {summary['totals']['transaction_count']}")
    print(f"rows by status        {summary['reading']['rows_by_status']}")
    print(f"months by status      {dict(Counter(m['status'] for m in summary['months']))}")
    print(f"months not complete   {[(m['month'], m['reasons']) for m in summary['months'] if m['status'] != 'complete']}")
    print(f"average given         {'yes' if summary['averages']['monthly_credit'] is not None else 'no'}"
          f" (months used: {len(summary['averages']['months_used'])})")
    print(f"printed period        {'yes' if summary['statement']['printed_period'] else 'no'}")
    print(f"signal codes          {sorted(s['code'] for s in summary['signals'])}")
    print(f"limitations           {len(summary['limitations'])}")
    print(f"emi series seen       {len(summary['emi_debits_seen'] or [])}")
    print(f"foir/dscr/score text  {'PRESENT' if any(w in text for w in ('foir', 'dscr', 'score')) else 'absent'}")
    print(f"account no. in output {'PRESENT' if number and number in json.dumps(summary) else 'absent'} (masked mode)")


def itr_run(paths):
    from lending_mcp import itr, loader
    from lending_mcp.config import load_settings

    doc = loader.load_itr(paths, load_settings())
    out = itr.financials(doc, unmasked=False)
    pans = {(c.get("identity") or {}).get("pan") for c in doc["columns"]} - {None}
    print(f"files                 {len(paths)}")
    print(f"load seconds          {doc.get('load_seconds')} (from cache: {doc['from_cache']})")
    print(f"scanned columns       {sum(1 for c in doc['columns'] if c.get('scanned'))} of {len(doc['columns'])}")
    for y in out["years"]:
        print(f"FY{y['year']}  statements read {y['statements_read']}  values read "
              f"{sum(v['read'] for v in y['values'].values())}/{len(y['values'])}  ratios published "
              f"{len(y['ratios'])} withheld {len(y['ratios_withheld'])}  DSCR published: {'DSCR' in y['ratios']}")
    print(f"flags                 {len(out['flags'])}   attention rows {len(out['attention'])}")
    print(f"workbook written      {'yes' if Path(doc['workbook']).is_file() else 'no'}")
    print(f"PAN in output         {'PRESENT' if any(p in json.dumps(out) for p in pans) else 'absent'} (masked mode)")


def gst_run(paths):
    from lending_mcp import gst, loader
    from lending_mcp.config import load_settings

    loader.BACKGROUND_WAIT = 3600  # a script can wait
    started = time.perf_counter()
    doc = loader.load_gst(paths, load_settings())
    seconds = round(time.perf_counter() - started, 1)
    out = gst.summary(doc, unmasked=False)
    full = {str(e.get(k)) for e in doc["entities"] for k in ("gstin", "pan") if e.get(k)}
    print(f"files                 {len(doc['source_files'])}")
    print(f"load seconds          {seconds} (from cache: {doc['from_cache']})")
    print(f"file types            {dict(Counter(f['doc_type'] for f in out['files']))}")
    print(f"file statuses         {dict(Counter(f['status'] for f in out['files']))}")
    for e in out["entities"]:
        print(f"{e['entity_id']}  pattern {e['filing_pattern']}  periods {len(e['periods'])}  "
              f"fy totals {len(e['fy_totals'] or {})}  late filings {sum(1 for f in e['filing'] if f['late'])}")
    print(f"findings              {[(f['finding_id'], f['severity']) for f in out['findings']]}")
    print(f"validation counts     {out['validation']['counts']}")
    print(f"limitations           {len(out['limitations'])}")
    print(f"full GSTIN/PAN shown  {'PRESENT' if any(x in json.dumps(out) for x in full) else 'absent'} (masked mode)")


def excel_run(args):
    """excel bureau=<pdf> bank=<pdf> itr=<pdf>[,<pdf>] gst=<pdf>[,<pdf>]: load each, then export
    with 1, 2 and all documents."""
    from openpyxl import load_workbook

    from lending_mcp import loader, workbook
    from lending_mcp.config import load_settings

    loader.BACKGROUND_WAIT = 3600
    settings = load_settings()
    given = dict(a.split("=", 1) for a in args)
    ids = []
    for kind in ("bureau", "bank", "itr", "gst"):
        if kind not in given:
            continue
        paths = given[kind].split(",")
        if kind == "bureau":
            ids.append(loader.load(paths[0], settings)["report_id"])
        else:
            ids.append(getattr(loader, f"load_{kind}")(paths, settings)["document_id"])
    for n in sorted({1, min(2, len(ids)), len(ids)}):
        out = workbook.export(ids[:n], settings)
        wb = load_workbook(out["workbook"])
        print(f"{n} document(s): sheets {wb.sheetnames}  rows {[ws.max_row for ws in wb.worksheets]}")
    print(f"last workbook         {out['workbook']}")


RUNS = {"bank": bank_run, "itr": itr_run, "gst": gst_run, "excel": excel_run}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in RUNS:
        sys.exit(__doc__)
    files = [a.split("=", 1)[-1] for a in sys.argv[2:]]
    _allow([f for group in files for f in group.split(",")])
    RUNS[sys.argv[1]](sys.argv[2:])
