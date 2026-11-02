# Lending MCP v2 (four documents, one Excel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bank statement, ITR and GST tools beside the four bureau tools, plus one
`export_excel` tool that builds one workbook with one sheet per loaded document.

**Architecture:** Each engine is called once at load time; its result is normalised into a
JSON-safe dict, cached unmasked, and queried by pure functions that mask at response time.
`server.py` only validates arguments. The Excel copies the CIBIL and ITR first sheets from the
engines' own workbooks and builds the bank and GST sheets in the same style.

**Tech Stack:** Python 3.12, uv, `mcp==2.3.0` (`MCPServer`), openpyxl 3.1.5, pytest; engines
`bsa_engine`, `itr_engine`, `gst_engine` as editable path installs; AutoCAM via `sys.path`.

**Spec:** `docs/superpowers/specs/2026-10-31-four-document-mcp-design.md` (frozen). Read it
with this plan; section numbers below refer to it.

## Global Constraints

- The spec is frozen. A change only when an acceptance criterion (spec section 9) forces it,
  reported to the user.
- Never call an LLM: bank `use_vlm_refinement=False`; ITR `api_key=None, use_vision=False`;
  never read or pass `GEMINI_API_KEY`; never load an engine repo's `.env`.
- The bank engine's `score` (and `score.foir_dscr`) never appears in any result, sheet or text.
  No tool returns ITR DSCR.
- Unknown is never zero: `None` stays `null` in results and "Not read" in new sheets.
- Cache holds normalised, unmasked data; masking is applied at response time from
  `settings.unmasked`.
- Every result carries `document_type`, `engine`, `limitations`, `masking` (bureau keeps its
  v1 envelope).
- Only files inside `allowed_dirs` load, every path in a list, every file found in a folder.
- Logs carry no extracted values; stdout is the MCP protocol.
- Preserve engine business logic: call the engines, never patch or reimplement them.
- No new frameworks, services, databases, agents or abstractions beyond the files listed here.
- **The user makes every git write.** Never run `git add`, `commit` or `push`. Each task ends by
  handing the user a commit message under 13 words, subject only, no Co-Authored-By.
- No hyphen used as a joining dash, no em dash, no en dash in docs, comments or messages.
- No real borrower data in the repo. Fixtures are synthetic. Real file runs print counts only,
  never names, PANs, GSTINs or account numbers.
- `pymupdf==1.24.11` stays pinned; if the resolver disagrees, stop and report.

## Review Focus

1. **A GST finding or explanation string that quotes a full GSTIN or PAN in its text.** A
   person expects masked output to hide it everywhere. Pinned by
   `test_gst_full_identifiers_never_appear_masked` (Task 7), which plants the full GSTIN inside
   finding text.
2. **A bank statement with no complete month** (a 3 week statement). A person expects no
   average, with the reason, not an average over a partial month. Pinned by
   `test_no_complete_month_gives_no_average` (Task 3).
3. **The same cached document asked for masked, then unmasked** after the user flips
   `LENDING_MCP_UNMASKED`. A person expects the right form both times. Pinned by
   `test_masking_follows_setting_on_cache_hit` (Task 5).
4. **An ITR year where only the balance sheet was read.** A person expects P&L ratios
   withheld with the reason, not 0.0. Pinned by `test_ratio_withheld_when_pl_not_read`
   (Task 6).
5. **`export_excel` given an id that was never loaded, or an empty list.** A person expects a
   clear error naming the id. Pinned by `test_export_refuses_unknown_or_empty_ids` (Task 9).

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | three engines as editable path sources, their `ocr` extras |
| `lending_mcp/masking.py` (create) | `mask_tail`, `mask_dob`, `mask_gstin`, `scrub`; lifted from `bureau.py` |
| `lending_mcp/loader.py` (modify) | shared: path checks for lists and folders, `fingerprint`, `to_jsonable`, generic cache `store`/`get`/`require`; `load` (bureau, unchanged API), `load_bank`, `load_itr`, `load_gst` |
| `lending_mcp/bank.py` (create) | `normalise`, `summary`, `find_transactions` (pure) |
| `lending_mcp/itr.py` (create) | `normalise`, `financials` (pure) |
| `lending_mcp/gst.py` (create) | `normalise`, `summary` (pure) |
| `lending_mcp/workbook.py` (create) | `copy_sheet`, `replace_dscr`, `bank_sheet`, `gst_sheet`, `export` |
| `lending_mcp/server.py` (modify) | 8 new tools |
| `lending_mcp/bureau.py` (modify) | import masking helpers from `masking.py`; behaviour unchanged |
| `tests/fixtures_bank.py`, `fixtures_itr.py`, `fixtures_gst.py` (create) | synthetic engine shaped data |
| `tests/test_infra.py`, `test_masking.py`, `test_bank.py`, `test_itr.py`, `test_gst.py`, `test_workbook.py` (create) | |
| `tests/test_loader.py`, `test_server.py` (modify) | |
| `scripts/real_run.py` (create) | local only demo on real files, counts only |

Document ids: bureau keeps `<sha16>-<fp8>`; others are `bank-<sha16>-<fp8>`, `itr-…`,
`gst-…`. `KINDS = {"bank", "itr", "gst"}`; an id without one of those prefixes is bureau.

---

### Task 1: Engines installed, imports proven

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_infra.py`

**Interfaces:**
- Produces: importable `bsa_engine`, `itr_engine`, `gst_engine` in the project venv.

- [ ] **Step 1: Write the failing test** `tests/test_infra.py`

```python
import importlib, sys
from pathlib import Path
import pytest

PROJECTS = Path(__file__).resolve().parents[2]
REPOS = {"bsa_engine": "Bank Statemnt Analyser", "itr_engine": "ITR Extractor", "gst_engine": "GST Analyser"}

@pytest.mark.parametrize("pkg,repo", REPOS.items())
def test_engine_resolves_inside_its_own_repo(pkg, repo):
    mod = importlib.import_module(pkg)
    assert Path(mod.__file__).resolve().is_relative_to((PROJECTS / repo / "engine").resolve())

def test_no_bare_engine_module_loaded():
    for pkg in REPOS: importlib.import_module(pkg)
    assert "engine" not in sys.modules

def test_engine_resources_resolve():
    import gst_engine.pipeline, itr_engine.cache
    assert gst_engine.pipeline.DEFAULT_POLICY.is_file()
    assert Path(itr_engine.cache.CACHE_DIR).resolve().is_relative_to((PROJECTS / "ITR Extractor").resolve())

def test_engine_entry_points_this_build_relies_on_exist():
    from bsa_engine.statement import analyze
    from bsa_engine.multi_statement import analyze_multiple, MismatchedAccountError
    from bsa_engine.parser import LockedPDFError
    from bsa_engine.signals.loans import emi_series
    from bsa_engine.signals.scoring import _complete, MONTH_EDGE_TOLERANCE_DAYS
    from bsa_engine.excel_generator import generate_excel as _b
    from itr_engine.parser import spread
    from itr_engine import analysis, explain
    from itr_engine.excel_generator import generate_excel as _i
    from itr_engine.mapping.template_config import RATIOS, SNAP_RATIO_NAMES
    from gst_engine import analyse, generate_excel as _g
    assert MONTH_EDGE_TOLERANCE_DAYS == 3
```

- [ ] **Step 2: Run it, expect FAIL** `uv run pytest tests/test_infra.py -v` → `ModuleNotFoundError: bsa_engine`.

- [ ] **Step 3: Add the engines to `pyproject.toml`.** Add `"bsa-engine[ocr]"`, `"itr-engine[ocr]"`,
  `"gst-engine"` to `dependencies`, and:

```toml
[tool.uv.sources]
bsa-engine = { path = "../Bank Statemnt Analyser", editable = true }
itr-engine = { path = "../ITR Extractor", editable = true }
gst-engine = { path = "../GST Analyser", editable = true }
```

  Run `uv sync`. If it fails on a version conflict, stop and report the resolver output.

- [ ] **Step 4: Run all tests** `uv run pytest` → 67 v1 tests plus the new ones PASS.

- [ ] **Step 5: Hand off.** Suggested message: `Add bank, ITR and GST engines as editable installs`.

---

### Task 2: Shared core: masking, fingerprint, cache, path checks

**Files:**
- Create: `lending_mcp/masking.py`, `tests/test_masking.py`
- Modify: `lending_mcp/loader.py`, `lending_mcp/bureau.py:250-270`, `tests/test_loader.py`

**Interfaces:**
- Produces (`masking.py`):
  - `mask_tail(value, keep: int = 4) -> Optional[str]` (moved from `bureau._mask_tail`, same behaviour)
  - `mask_dob(value) -> Optional[str]` (moved from `bureau._mask_dob`)
  - `mask_gstin(value) -> Optional[str]`: first 2 and last 3 kept, 10 `*` between (`29**********1Z5`)
  - `scrub(obj, replacements: Dict[str, str])`: returns a deep copy with every exact occurrence
    of each key replaced by its value, inside dict keys, string values and nested strings.
    Longest keys replaced first.
- Produces (`loader.py`):
  - `NORMALISE_VERSION = 1`
  - `KINDS = ("bank", "itr", "gst")`
  - `checked_paths(raws: List[str], settings, extensions: Set[str], allow_folders: bool = False) -> List[Path]`:
    each via `checked_path`; a folder (only if `allow_folders`) expands recursively to its files
    with those extensions, each checked; empty result raises `LoadError("No PDF found in …")`;
    result sorted and de-duplicated.
  - `fingerprint(engine_files: List[Path], packages: List[str], extra: List[Path] = ()) -> str`:
    sha256 over each file's name and bytes, each package's `importlib.metadata.version` (or
    `"absent"`), each extra file's bytes, and `NORMALISE_VERSION`.
  - `package_dir(pkg: str) -> Path` via `importlib.util.find_spec(pkg).origin` (no import);
    `engine_files(pkg) -> List[Path]`: all `*.py` under it, sorted.
  - `document_id(kind: str, datas: List[bytes], fp: str) -> str`: `f"{kind}-{sha16}-{fp[:8]}"`
    where sha16 is over the sorted file hashes.
  - `to_jsonable(obj)`: dataclass → dict, `date`/`datetime` → ISO, dict key `(int, int)` →
    `"YYYY-MM"`, other non str keys → `str`, set/tuple → list, `Decimal` → float, bytes dropped
    to `None`. Recursive.
  - `store(doc: dict, settings) -> None` (atomic JSON write + memory), `get(document_id, settings) -> Optional[dict]`,
    `require(document_id, settings, kind: Optional[str] = None) -> dict`: raises
    `LoadError` for unknown id, or for a kind mismatch: `"… is a bank document, not itr"`.
  - `kind_of(document_id) -> str`: the prefix if in `KINDS`, else `"bureau"`.
- Bureau: `report_id` becomes `f"{sha16}-{fingerprint(autocam engine files, ['pymupdf','openpyxl','pytesseract','pillow'])[:8]}"`.
  `load()`'s signature and return are unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_masking.py` and `tests/test_loader.py`:
  - `test_mask_tail_and_dob_match_v1` (same outputs as v1 for `"ABCDE1234F"` → `"******234F"`, `"12-05-1990"` → `"**-**-1990"`)
  - `test_mask_gstin` (`"29ABCDE1234F1Z5"` → `"29**********1Z5"`; `None` → `None`)
  - `test_scrub_replaces_in_keys_values_and_nested_text`
  - `test_checked_paths_expands_folder_and_checks_each_file` (a folder inside allowed with 2 PDFs and a .txt → 2 paths)
  - `test_checked_paths_refuses_folder_outside_allowed` and `test_checked_paths_refuses_folder_when_not_allowed`
  - `test_fingerprint_changes_with_package_version` (monkeypatch `importlib.metadata.version`), `…_with_extra_file`, `…_with_normalise_version`
  - `test_to_jsonable_converts_dates_month_tuples_and_dataclasses`
  - `test_require_refuses_kind_mismatch`
  - `test_bureau_id_changes_with_package_version`

- [ ] **Step 2: Run, expect FAIL** `uv run pytest tests/test_masking.py tests/test_loader.py -v`.

- [ ] **Step 3: Implement** as in Interfaces. `bureau.py` imports `mask_tail`, `mask_dob` from
  `masking` and drops its own copies.

- [ ] **Step 4: Run all tests** `uv run pytest` → all PASS (the 67 v1 tests unchanged).

- [ ] **Step 5: Checkpoint A, show the user**: test count and `test_infra.py` output. Wait for
  approval. Suggested message: `Shared masking, fingerprint and cache for all document types`.

---

### Task 3: Bank normalise and summary (pure)

**Files:**
- Create: `lending_mcp/bank.py`, `tests/fixtures_bank.py`, `tests/test_bank.py`

**Interfaces:**
- Consumes: `masking.mask_tail`, `masking.scrub`.
- Produces:
  - `normalise(result: dict, emi: list, merge: Optional[dict], engine_month_complete: Dict[str, bool]) -> dict`.
    `result` is `to_jsonable(dataclasses.asdict(AnalysisResult))`; `emi` is
    `to_jsonable(emi_series(result.transactions))`; `merge` is
    `{"files_merged", "duplicates_removed", "merge_conflicts"}` or `None`;
    `engine_month_complete` maps `"YYYY-MM"` to the engine's own `_complete(...)` answer,
    computed by the loader (Task 5). Returns the stored form: everything from `result` **except
    `score`** (dropped here, so it never reaches the cache), plus `emi`, `merge`, and `months`
    (below).
  - `summary(doc: dict, unmasked: bool) -> dict` (spec 5.1).
- `months`: a list, one per month in `result["monthly"]`, oldest first:
  `{"month", "credits", "debits", "credit_count", "debit_count", "net", "status", "reasons"}`,
  `status` one of `"complete"`, `"partial"`, `"doubtful"`.

The month rule (spec 5.1), in this order; every failing reason is listed:

```
partial  if not engine_month_complete[month]                        "transactions do not span the month"
partial  if statement_check.period exists and does not cover the     "the statement's printed period does
         whole month                                                  not cover the whole month"
doubtful if a continuity signal touches the month:
           PAGE_BOUNDARY_BREAK: breaks[].date_before .. date_after   "page boundary break: a page may be missing"
           DATE_GAP:            gaps[].from .. to                    "no transactions for N days"
           STATEMENT_CHAIN_BREAK: instances[]["Previous file ends"]  "gap between merged statements"
                                  .. ["Next file starts"]
           PERIOD_NOT_COVERED:  the first and the last month          "transactions do not cover the stated period"
         (a month is touched when any day of it lies in from..to inclusive)
doubtful if any transaction in the month has unresolved_amount       "rows whose debit or credit could not be decided"
         not None
```

A continuity signal whose dates cannot be read marks **every** month doubtful, with reason
"a continuity check could not be placed in time". When `statement_check` or its `period` is
absent, add to `limitations`: "statement period not printed; completeness judged from
transaction dates and continuity checks only".

`summary` returns (spec 5.1): envelope (`document_type: "bank"`, `engine: "Bank Statement
Analyser"`, `limitations`, `masking`), statement block (account number via `mask_tail` unless
unmasked; `account_holder` as is), reading quality, totals, `months`, `averages` =
`{"monthly_credit", "monthly_debit", "months_used": [...]}` over complete months only or
`{"monthly_credit": None, "monthly_debit": None, "months_used": [], "reason": "no complete month"}`,
`abb`, `credits` (gross and net, never the word income), `bounces`, `emi`, `salary_like_credits`
(the `salary` dict under that name plus `"note": "salary like credits detected by the engine
(recurring credits matching salary patterns); not verified income"`), `cashflow`,
`category_summary`, `overdraft`, `signals` = `red_flags + fraud_signals`, each unchanged plus
`"note": "an attention signal from the engine's checks; not a conclusion"`, and `merge`.
Masked mode passes the whole result through `scrub({account_number: masked})`.

`limitations` always includes, when they apply: `"<n> scanned page(s) were not read: Vision
is switched off in this server"` (when `scanned_pages > 0`); `"the engine suggests a closer
read of pages where OCR was thin; Vision is switched off in this server"` (when
`vlm_recommended`); `"<n> row(s) worth Rs.<x> could not be read as debit or credit; they are
in neither total"` (when `unresolved_count > 0`); and the missing period line above.

- [ ] **Step 1: Fixtures** `tests/fixtures_bank.py`: `STATEMENT` (a dict shaped like
  `to_jsonable(asdict(AnalysisResult))`: 3 months 2025-01..03, full Jan and Feb, March ending
  on the 20th; a `statement_check.period` of 2025-01-01..2025-03-31; `score` present with a
  `foir_dscr` block), `EMI` (one series), helpers to add a signal or an unresolved row.

- [ ] **Step 2: Write the failing tests** `tests/test_bank.py`:
  - `test_score_is_dropped_at_normalise` (`"score"` not in the stored doc; `"foir"`, `"dscr"`, `"score"` absent from `json.dumps(summary)` lowercased)
  - `test_month_partial_when_transactions_do_not_span` (March partial, reason as above)
  - `test_month_partial_when_printed_period_short`
  - `test_month_doubtful_for_each_signal` (parametrised over the four codes)
  - `test_month_doubtful_for_unresolved_row`
  - `test_unplaceable_signal_marks_every_month_doubtful`
  - `test_average_over_complete_months_only` (Jan and Feb used, March named as partial)
  - `test_no_complete_month_gives_no_average`
  - `test_missing_period_adds_limitation`
  - `test_salary_named_salary_like_credits_with_note`
  - `test_limitations_name_scanned_pages_vlm_and_unresolved`
  - `test_signals_keep_full_evidence_and_note`
  - `test_account_number_masked_everywhere_including_narration` (account number planted in a narration)
  - `test_unmasked_shows_account_number`

- [ ] **Step 3: Run, expect FAIL** `uv run pytest tests/test_bank.py -v`.
- [ ] **Step 4: Implement** `bank.normalise` and `bank.summary`.
- [ ] **Step 5: Run, expect PASS**; then `uv run pytest`.
- [ ] **Step 6: Hand off.** Suggested message: `Bank statement normalising, month completeness and summary`.

---

### Task 4: Bank transaction finder (pure)

**Files:**
- Modify: `lending_mcp/bank.py`, `tests/test_bank.py`

**Interfaces:**
- Produces: `find_transactions(doc, unmasked, *, date_from: Optional[str] = None,
  date_to: Optional[str] = None, direction: str = "any", min_amount: Optional[float] = None,
  max_amount: Optional[float] = None, narration_contains: Optional[str] = None,
  category: Optional[str] = None, status: Optional[str] = None, sort_by: str = "date",
  descending: bool = False, limit: int = 50, offset: int = 0) -> dict`
  returning envelope plus `total_matches`, `has_more`, `transactions` (one page),
  `totals = {"credit_count", "credit_sum", "debit_count", "debit_sum"}` over all matches,
  `excluded_because_value_unknown` (count and the rows' dates). Raises `bank.QueryError`
  (a `ValueError`) for a bad date, `limit` outside 1..200, unknown `direction`, `sort_by`
  or `status`.
- A row's amount is its `credit` or `debit`; a row with both `None` (direction unresolved) is
  unknown: never matched by amount or direction filters, never summed, counted in
  `excluded_because_value_unknown` when an amount or direction filter is set. `OPENING` rows
  never match.

- [ ] **Step 1: Write the failing tests**: `test_find_each_filter` (parametrised), `test_find_combined`,
  `test_find_totals_cover_all_matches_not_page`, `test_find_never_sums_unknown`,
  `test_find_sort_and_paging`, `test_find_refuses_bad_arguments` (parametrised),
  `test_find_masks_account_number_in_narration`.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Hand off.** Suggested message: `Bank transaction finder with totals over all matches`.

---

### Task 5: Bank loading and the three bank tools

**Files:**
- Modify: `lending_mcp/loader.py`, `lending_mcp/server.py`, `tests/test_loader.py`, `tests/test_server.py`
- Create: `scripts/real_run.py`

**Interfaces:**
- Consumes: Task 2 shared functions, `bank.normalise`, `bank.summary`, `bank.find_transactions`.
- Produces:
  - `loader.load_bank(paths: List[str], settings, password: Optional[str] = None, engine=None) -> dict`.
    `engine` (for tests) is an object with `analyze(bytes, password)`, `analyze_multiple(list, passwords)`,
    `emi_series(transactions)`, `complete(year, month, first, last)`; default wraps
    `bsa_engine` (`use_vlm_refinement=False`; `complete` calls `_complete(..., MONTH_EDGE_TOLERANCE_DAYS)`).
    PDFs only (`checked_paths(..., {".pdf"})`), `%PDF-` magic checked as v1. One path →
    `analyze`; two or more → `analyze_multiple(datas, [password] * n)`. Fingerprint packages:
    `pymupdf`, `openpyxl`, `pytesseract`, `pillow`, `rapidocr-onnxruntime`. Returns the stored
    doc plus `from_cache`. Errors: `LockedPDFError` → `"The PDF is password protected: pass
    password (or the right one)."`; `MismatchedAccountError` → `"These statements are not of
    one account; load them separately."`; any other exception → type name only, as v1.
  - Tools: `load_bank_statement(paths: list[str], password: Optional[str] = None) -> dict`
    (id, bank, masked account number, period, transaction count, rows VERIFIED of total,
    limitations), `get_bank_summary(document_id: str)`, `find_bank_transactions(document_id, …)`
    with Task 4's parameters, `Literal` types for `direction`, `sort_by`, `status`. Annotations
    as v1 (`_LOAD`, `_READ_ONLY`).
  - The doc stored also keeps `source_files` (names only) and `loaded_at`.

- [ ] **Step 1: Write the failing tests**: `test_load_bank_single_and_multi` (fake engine records
  which entry was called), `test_load_bank_cache_hit`, `test_masking_follows_setting_on_cache_hit`
  (load, summary masked, set `LENDING_MCP_UNMASKED=1`, summary unmasked, engine called once),
  `test_load_bank_locked_pdf_message`, `test_load_bank_mismatched_accounts_message`,
  `test_load_bank_refuses_path_outside_allowed`, `test_bank_tools_end_to_end` (in process
  client), `test_default_bank_engine_never_uses_vision` (with `GEMINI_API_KEY` set in the
  environment, monkeypatch `bsa_engine.statement.analyze` to record its kwargs; the default
  engine wrapper passes `use_vlm_refinement=False`), and update `TOOLS` in `test_server.py`
  to the 7 tools now present.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS**, then `uv run pytest`.
- [ ] **Step 5: Real run.** `scripts/real_run.py bank <pdf>…` loads through `loader.load_bank`
  with an allowed dir set to the file's folder via `LENDING_MCP_ALLOWED_DIRS`, and prints
  only: transactions, months by status, rows by status, averages present or not, signal codes,
  and whether `score`/`foir` appear anywhere (must be no). Run on one statement from
  `Bank Statemnt Analyser/Sample Data` and on two files of one account.
- [ ] **Step 6: Checkpoint B, show the user** the real run output. Wait for approval.
  Suggested message: `Bank statement tools: load, summary and transaction finder`.

---

### Task 6: ITR: normalise, financials, loading, two tools

**Files:**
- Create: `lending_mcp/itr.py`, `tests/fixtures_itr.py`, `tests/test_itr.py`
- Modify: `lending_mcp/loader.py`, `lending_mcp/server.py`, `tests/test_server.py`, `scripts/real_run.py`

**Interfaces:**
- Produces:
  - `itr.RATIO_INPUTS: Dict[str, Set[str]]`: copied from
    `FinSight/backend/business/ratios.py` (11 names, `"pl"`/`"bs"`), with a comment citing it.
  - `itr.normalise(columns: list, flags: list, summary: dict, attention: list, formulas: Dict[str, str]) -> dict`
    (`formulas` = name → formula string from `template_config.RATIOS`, passed by the loader).
  - `itr.financials(doc, unmasked) -> dict`: envelope (`document_type: "itr"`, `engine: "ITR Extractor"`),
    `years`: per column oldest first: `year`, `provisional`, `entity`, `form`, `ay`,
    `filing_date`, `pan` (masked via `mask_tail`), check status per block, `mapping_check`,
    `values` as `{key: {"label", "value"}}` (labels from `itr_engine.mapping.taxonomy.LABELS`,
    stored at normalise), `ratios` published and `ratios_withheld` with reasons; plus `flags`,
    `revenue_cagr`, `attention`.
  - Ratio rule: sections read = `{"pl"}` if `values["sales_other_income"] is not None`, `{"bs"}`
    if `values["total_assets"] is not None`. A ratio is published only if it is in
    `RATIO_INPUTS`, its value is not `None`, its sections are all read, and its name is not
    `"DSCR"`. Withheld reasons: `"needs the <profit and loss statement|balance sheet>, which
    was not read this year"`, `"not in the MCP's ratio table"`, and for DSCR `"DSCR is not
    shown: the formula assumes asset financed loans are repaid over four years"`. Published
    entries: `{"value", "formula", "note": "a standard ratio derived from this year's statements"}`.
  - `loader.load_itr(paths, settings, engine=None) -> dict`: PDFs only; `spread(paths,
    api_key=None, use_vision=False)`; the engine's workbook bytes
    (`itr_engine.excel_generator.generate_excel(columns)`) written to
    `<home>/workbooks/<document_id>.xlsx` and its path stored as `workbook`. Fingerprint
    packages: `pymupdf`, `openpyxl`, `pytesseract`, `pillow`, `rapidocr`, `onnxruntime`;
    extra: `ITR Extractor/data/label_map.json`.
  - Tools: `load_itr(paths: list[str])` (id, entity, years, reconciled per year, limitations,
    and `load_seconds`), `get_itr_financials(document_id)`.

- [ ] **Step 1: Write the failing tests**: `test_ratio_withheld_when_pl_not_read`,
  `test_ratio_withheld_when_bs_not_read`, `test_dscr_never_published` (also absent from
  `json.dumps` of every ratio key outside `ratios_withheld`), `test_unknown_ratio_withheld`,
  `test_none_value_stated_as_unknown`, `test_pan_masked_and_unmasked`, `test_load_itr_writes_workbook_and_caches`,
  `test_default_itr_engine_never_uses_gemini` (with `GEMINI_API_KEY` set, monkeypatch
  `itr_engine.parser.spread` to record its kwargs: `api_key=None`, `use_vision=False`),
  `test_itr_tools_end_to_end`; `TOOLS` now 9.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS**, then `uv run pytest`.
- [ ] **Step 5: Real run** on one stored ITR bundle (FinSight `document` rows of type `ITR`
  for one application): years, ratios published and withheld counts, `load_seconds`. Then on a
  **scanned** ITR bundle, report `load_seconds` (spec risk 1). If it exceeds 60 seconds, tell
  the user before going on.
- [ ] **Step 6: Checkpoint C, show the user.** Suggested message: `ITR tools: load and financials with withheld ratios`.

---

### Task 7: GST: normalise into records, summary, loading, two tools

**Files:**
- Create: `lending_mcp/gst.py`, `tests/fixtures_gst.py`, `tests/test_gst.py`
- Modify: `lending_mcp/loader.py`, `lending_mcp/server.py`, `tests/test_server.py`, `scripts/real_run.py`

**Interfaces:**
- Produces:
  - `gst.normalise(report: dict) -> dict`: `report` is `res.report`. Replaces
    `canonical.borrowers[PAN].gstins[GSTIN]` with `entities: [{"entity_id": "gstin-N", "gstin",
    "pan", …the gstin dict's other fields}]` in engine order, and rewrites every other place
    keyed by GSTIN (`confidence.data_coverage`) to `entity_id` keys. Keeps `identifiers:
    {"gstin-N": {"gstin", "pan"}}` for masking. Drops `vendor_compat` and `vendor_summary`
    (the Perfios view; not part of 5.4).
  - `gst.summary(doc, unmasked) -> dict` (spec 5.4): envelope (`document_type: "gst"`,
    `engine: "GST Analyser"`), per entity: `entity_id`, `gstin_masked`, `pan_masked` (and
    `gstin`, `pan` only when unmasked), names, filing pattern, coverage, `periods` (from
    `series.points`: period label, the base figures named in 5.4, extraction confidence),
    `fy_totals`, `trends`, filing per return, ratios per period; `findings` unchanged,
    validation counts, confidence, `files` (skipped ones with reason). Masked mode then
    passes the whole result through `scrub` with every full GSTIN and PAN → masked form.
  - `loader.load_gst(paths, settings, engine=None) -> dict`: `checked_paths(..., {".pdf"},
    allow_folders=True)`, then `gst_engine.analyse([str(p) for p in paths])`; the engine's
    workbook is not needed. Fingerprint packages `pymupdf`, `openpyxl`, `pyyaml`; extra
    `gst_engine.pipeline.DEFAULT_POLICY`. If no file is classified `GSTR3B`, `LoadError("No
    GSTR-3B return found in these files.")`.
  - Tools: `load_gst_returns(paths: list[str])`, `get_gst_summary(document_id)`.

- [ ] **Step 1: Fixtures** `tests/fixtures_gst.py`: a report with **two GSTINs under one PAN**
  (`29ABCDE1234F1Z5`, `29ABCDE1234F2Z4`, which mask to strings that would collide as keys
  in a naive scheme), a finding whose `explanation` quotes the first GSTIN in full, and a
  skipped bank statement file.
- [ ] **Step 2: Write the failing tests**: `test_entities_are_records_with_opaque_ids`,
  `test_two_gstins_one_pan_both_kept`, `test_gst_full_identifiers_never_appear_masked`
  (recursive scan of `json.dumps(summary)` for the full fixture GSTINs and PAN; masked forms
  allowed), `test_unmasked_adds_fields_not_keys`, `test_skipped_files_listed`,
  `test_load_gst_folder_inside_allowed`, `test_load_gst_folder_outside_refused`,
  `test_load_gst_no_return_found`, `test_gst_tools_end_to_end`; `TOOLS` now 11.
- [ ] **Step 3: Run, expect FAIL.** **Step 4: Implement.** **Step 5: Run, expect PASS**, then `uv run pytest`.
- [ ] **Step 6: Real run** on one FinSight case's GST files: entities, periods, findings
  count, and a scan that no full GSTIN or PAN appears in the masked JSON (prints yes or no).
- [ ] **Step 7: Checkpoint D, show the user.** Suggested message: `GST tools: load and summary with masked entity records`.

---

### Task 8: Sheet copy and the DSCR note

**Files:**
- Create: `lending_mcp/workbook.py`, `tests/test_workbook.py`

**Interfaces:**
- Produces:
  - `class WorkbookError(ValueError)`.
  - `check_supported(ws) -> None`: raises `WorkbookError("the <title> sheet uses <feature>,
    which the Excel export does not copy yet")` if the sheet has conditional formatting, data
    validations, images, charts, defined names scoped to it, or any formula containing `!`.
  - `copy_sheet(src_ws, dst_wb, title: str) -> Worksheet`: `check_supported` first; then per
    cell value (formula strings as is), `font`, `fill`, `border`, `alignment`,
    `number_format`, `protection`, comment (text and author); column widths, row heights,
    merged ranges, `freeze_panes`, `print_title_rows`, `print_title_cols`, page setup
    (`orientation`, `fitToPage`, `fitToWidth`, `fitToHeight`), sheet properties
    `pageSetUpPr`.
  - `replace_dscr(ws) -> None`: finds rows whose column A text, stripped, equals `"DSCR"`;
    requires exactly 2 (Ratios and Financial Snap) else `WorkbookError`; in each, every cell
    from column B to `ws.max_column` that is not empty becomes `"Not shown"` with a comment
    (author "Lending MCP"): `"DSCR is not shown: the formula assumes asset financed loans are
    repaid over four years (interest + loans / 4), which the ITR does not state."` Then
    asserts no formula on the sheet references any replaced cell, else `WorkbookError`.

- [ ] **Step 1: Write the failing tests**:
  - `test_copy_preserves_everything_used` (build a source sheet with every listed attribute,
    copy, compare attribute by attribute; any difference fails)
  - `test_copy_matches_real_autocam_sheet` (generate AutoCAM's workbook from
    `tests.fixtures.COMMERCIAL` through the real `excel_generator`, copy sheet 1, compare
    every cell and sheet attribute)
  - `test_copy_matches_real_itr_sheet` (generate from `tests.fixtures_itr` columns through the
    real `itr_engine.excel_generator`, copy, compare; formulas identical strings)
  - `test_unsupported_feature_fails_loudly` (a sheet with conditional formatting)
  - `test_cross_sheet_formula_fails_loudly`
  - `test_replace_dscr_both_rows_only` (on the real ITR sheet: both DSCR rows show "Not
    shown" with the comment; every other cell identical to the source)
  - `test_replace_dscr_fails_when_rows_missing`
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Hand off.** Suggested message: `Exact sheet copy and DSCR note for the combined workbook`.

---

### Task 9: Bank and GST sheets, and `export_excel`

**Files:**
- Modify: `lending_mcp/workbook.py`, `lending_mcp/server.py`, `tests/test_workbook.py`, `tests/test_server.py`, `scripts/real_run.py`

**Interfaces:**
- Consumes: `bank.summary`, `gst.summary` (their masked or unmasked output is what the sheets
  print), `copy_sheet`, `replace_dscr`, `loader.require`, `loader.kind_of`.
- Produces:
  - Style constants copied from the two generators: `NAVY = "1F3864"`, `WHITE`, `BORDER = "BFBFBF"`,
    `ALT = "F2F2F2"`, `TOTAL = "FFF2CC"`, `UNREAD = "FFC7CE"`; Arial throughout; Indian comma
    number format `[>=10000000]##\,##\,##\,##0;[>=100000]##\,##\,##0;##,##0` for rupees.
  - `bank_sheet(wb, summary: dict, title: str) -> Worksheet` and
    `gst_sheet(wb, summary: dict, title: str) -> Worksheet`, laid out as spec section 6.
    Any `None` writes `"Not read"` on `UNREAD` fill.
  - `export(document_ids: List[str], settings) -> dict`: refuses an empty list and any id
    `require` cannot find (`LoadError` names the id); one sheet per id in order (bureau: copy
    sheet 1 of the stored AutoCAM workbook; itr: copy sheet 1 of the stored ITR workbook, then
    `replace_dscr`; bank, gst: build); titles `"Bureau"`, `"Bank"`, `"ITR"`, `"GST"`, with
    `" (2)"` and so on for repeats; `wb.properties.title = "Lending MCP export"`,
    `wb.properties.description` = the disclaimer below plus one line per sheet (sheet title,
    document id, source files). Written atomically to
    `<home>/workbooks/export-<sha8 of ids joined by "," and the masking mode>.xlsx`. Returns
    `{"workbook": path, "sheets": [{"title", "document_id", "document_type", "source_files",
    "entity"}], "note": DISCLAIMER, "masking"}`.
  - `DISCLAIMER = "These documents were combined as requested. Combining them does not
    establish that they belong to the same borrower, and no reconciliation between them has
    been made."`
  - Tool `export_excel(document_ids: list[str]) -> dict`; `TOOLS` now 12.

- [ ] **Step 1: Write the failing tests**: `test_export_one_two_and_four_documents` (sheet
  titles and count), `test_export_refuses_unknown_or_empty_ids`, `test_repeated_type_titles`,
  `test_bank_sheet_has_no_scoring_text` (no cell contains FOIR, DSCR or score, case
  insensitive), `test_new_sheets_show_not_read_for_unknown`, `test_disclaimer_in_result_and_properties`,
  `test_new_sheets_follow_masking`, `test_twelve_tools_with_annotations`, and keep
  `test_stdio_process_speaks_only_protocol` passing.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS**, then `uv run pytest`.
- [ ] **Step 5: Real run**: load one real CRIF report, one bank statement, one ITR bundle and
  one GST case; export with 1, 2 and 4 ids; print sheet titles and counts only. Ask the user to
  open the 4 sheet workbook in Excel once and confirm the ITR totals and ratios recalculate and
  look like the engine's own sheet (LibreOffice is not installed, so this check is manual).
- [ ] **Step 6: Checkpoint E, show the user.** Suggested message: `Combined Excel export with one sheet per document`.

---

### Task 10: Docs and wiring

**Files:**
- Modify: `CLAUDE.md`, `docs/project.md`, `README.md`

- [ ] **Step 1:** Update the layout, the 12 tools, the commands (test count from the last
  `uv run pytest`), and the rules (bank scoring never exposed, DSCR not shown, masking at
  response time) in `CLAUDE.md`; add a v2 section to `docs/project.md` (decisions with
  reasons, verification done, known issues found in the real runs); update the README's tool
  list. No dash rule violations.
- [ ] **Step 2:** Restart check: `uv run lending-mcp` starts; the Claude Desktop config needs
  no change (same command). Ask the user to restart Claude Desktop and confirm 12 tools show.
- [ ] **Step 3: Hand off.** Suggested message: `Document v2: four document types and combined Excel`.

Then the spec's section 13 follow up (the Downloads batch run) is a separate request.
