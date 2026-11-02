# Lending MCP v2: four documents and one Excel. Design

Written 2026-10-31. Read `docs/project.md` and `CLAUDE.md` first: every v1 rule there still holds
unless this document says otherwise.

## 1. Intent

v1 answers questions about one bureau report. v2 does the same for the other three documents of
a loan file: **bank statements, ITR and GST returns**. Each one is read by the user's own engine.
v2 also adds **one Excel workbook for the loan file**, with one sheet for each document loaded.

The user's aim: anyone can connect this MCP to their own workflow and get an analysis of
financial documents. That means plain, universal facts, not the user's internal scoring.

The user's words that set the scope:
- "lets just focus on individual doc where anyone can connect this mcp to their workflow and get
  analysis of financial docs"
- "ignore bank statemnt foir and credit score, thats internal scoring system, show/expose those
  tool that are universal like avg monthly credit/debit etc like this for itr, liability and
  asset etc"
- "if i upload only cibil then i get cibil excel, if i upload cibil and bs then 1 excel 2 sheet"
- "cibil 1st sheet is very good formatted, simialr of itr too ... i love how cibil and itr are
  well presented for 1st sheet"
- "keep relevant ones only and make a simple lending mcp"

## 2. Goals, non goals, anti goals

**Goals**
1. Load a bank statement (one PDF, or several PDFs of the same account), an ITR bundle (one PDF
   per year) or a set of GSTR-3B returns, each through its own engine, cached the way v1 caches
   bureau reports.
2. Answer questions about each document with the facts its engine reads: totals, monthly
   figures, year columns, periods. Arithmetic happens in code.
3. Build one workbook from any set of loaded documents: one sheet per document.

**Non goals (later, not now)**
- Reconciliation between documents (GST vs bank vs ITR). FinSight's three checks
  (`FinSight/backend/reconciliation/rules.py`) are the model when it comes.
- A case or loan file store. Documents are tied together only by the ids Claude passes to the
  Excel tool.
- TransUnion, HTTP transport, hosting, login.

**Anti goals (failure even if it works)**
- Exposing the bank engine's **FOIR, DSCR or 0 to 1000 cash flow score** (`score` and
  `score.foir_dscr` in its result), anywhere: tools, Excel, limitations text. The user called
  these an internal scoring system.
- A tool per question. Tools stay few and composable.
- Any LLM call inside the server. No Gemini, no Vision, on any engine.
- Unknown shown or summed as zero, in a tool result or a sheet.
- Rebuilding the CIBIL or ITR first sheet by hand. They are copied (section 6).

## 3. The engines, as inspected 2026-10-31

All entry points below were run on real files (structure only printed).

| Engine | Folder | Import as | Entry point | Notes |
|---|---|---|---|---|
| AutoCAM (bureau) | `CIBIL_EXCEL/` | `parser`, `excel_generator` from its folder through `sys.path` (unchanged from v1) | `parser.parse(path, api_key=None)` | v1, already wired |
| Bank Statement Analyser | `Bank Statemnt Analyser/` | `bsa_engine` (editable install) | `bsa_engine.statement.analyze(bytes, password=None, use_vlm_refinement=False)`; several files: `bsa_engine.multi_statement.analyze_multiple(list_of_bytes, passwords)` | Returns `AnalysisResult`, a dataclass of 27 fields. Several files of different accounts raise `MismatchedAccountError`. A locked PDF raises `bsa_engine.parser.LockedPDFError` |
| ITR Extractor | `ITR Extractor/` | `itr_engine` (editable install) | `itr_engine.parser.spread(paths, api_key=None, use_vision=False)` returns year columns; `itr_engine.analysis.flags(cols)`, `analysis.summary(cols)`, `itr_engine.explain.all_attention(cols)` | Scans read by local OCR (RapidOCR or Tesseract). Writes its own reading cache in `ITR Extractor/data/cache/` (that is its normal behaviour; leave it) |
| GST Analyser | `GST Analyser/` | `gst_engine` (editable install) | `gst_engine.analyse(paths)` returns `CaseResult`; `res.report` is a JSON ready dict; `gst_engine.generate_excel(res)` | Accepts files or folders, classifies by content, skips non GST files and lists them. PAN and GSTIN appear **as dictionary keys** in `report["canonical"]` |

**Install.** All three new engines ship a top level package literally named `engine`, so they
must be installed under their unique names as **editable** installs, the way FinSight does
(`FinSight/backend/adapters/analyzers.py` explains why: a normal install breaks their
resource paths). In `pyproject.toml`, use uv path sources with `editable = true` pointing at
the sibling folders. ITR needs its `ocr` extra (rapidocr, onnxruntime); bank its `ocr` extra
(rapidocr-onnxruntime). The bank and ITR engines list `langchain-google-genai` as a
dependency; it gets installed but must never be called (section 7). v1 pins
`pymupdf==1.24.11`; the engines ask for `>=1.24`, so the pin stands. If the resolver
disagrees, report it rather than loosening pins silently.

AutoCAM keeps loading from its folder. Its bare `import parser` does not clash with
`bsa_engine.parser` or `itr_engine.parser`, because those are namespaced.

**Proven on 2026-10-31 in a clean throwaway venv** (Python 3.12, uv; the script was
`import_test.py` in the session scratchpad):
- `mcp==2.3.0`, the v1 pins (`pymupdf==1.24.11` and the rest) and editable installs of
  `Bank Statemnt Analyser[ocr]`, `ITR Extractor[ocr]` and `GST Analyser` resolved together
  with no conflict.
- In both import orders (AutoCAM first, AutoCAM last), every module resolved to its own repo:
  `parser` and `excel_generator` to `CIBIL_EXCEL/`, and `bsa_engine`, `itr_engine` and
  `gst_engine` to each repo's `engine/`. No bare `engine` module was ever loaded.
- Resources resolved: GST's `config/policy.yaml` exists, and ITR's `data/label_map.json` and
  `data/cache` point into the ITR repo.
- One real file went through each engine in that environment: AutoCAM (a CRIF Commercial
  report and its workbook), bank (420 rows, all VERIFIED, and its workbook), ITR (4 year
  columns and flags) and GST (24 files, findings and its workbook).
- The engines use only relative imports internally (checked by grep), which is why the
  renames hold. `GEMINI_API_KEY` was not in the environment.

The build adds a permanent test for this (section 9).

## 4. Tools (12)

v1's four bureau tools are unchanged. Eight are new.

| Document | Tool | Purpose |
|---|---|---|
| Bureau | `load_bureau_report`, `get_bureau_summary`, `find_bureau_accounts`, `get_bureau_account` | as v1 |
| Bank | `load_bank_statement(paths: list[str], password: str = None)` | one path: `analyze`; two or more: `analyze_multiple` (same account only). Returns `document_id`, bank, masked account number, period, transaction count, how many rows reconcile, and limitations |
| Bank | `get_bank_summary(document_id)` | the facts in section 5.1 |
| Bank | `find_bank_transactions(document_id, ...)` | section 5.2 |
| ITR | `load_itr(paths: list[str])` | one PDF per financial year. Returns `document_id`, entity, years found, per year whether the statements reconciled, and limitations |
| ITR | `get_itr_financials(document_id)` | section 5.3 |
| GST | `load_gst_returns(paths: list[str])` | files or folders of GSTR-3B. Returns `document_id`, GSTINs (masked), periods found, files skipped and why, and limitations |
| GST | `get_gst_summary(document_id)` | section 5.4 |
| All | `export_excel(document_ids: list[str])` | section 6. Returns the workbook path |

**Ids.** Like v1's `report_id`: a hash of everything section 8 lists (the input files'
bytes, the engine's source, the reading packages' versions), so the same files load from the
cache and any change that could alter the reading parses afresh. Prefix the id with the document type (`bank-`, `itr-`,
`gst-`) so `export_excel` knows which kind each one is. The bureau `report_id` keeps its v1
format; treat any id without a prefix as bureau.

**Every result carries** `document_type`, `engine`, `limitations` and `masking`. That is v1's
envelope (`provider`, `extraction_method`, `limitations`, `masking`) generalised; the bureau
tools keep their v1 fields.

## 5. What each tool returns

The principle: facts the engine read or added up, with the engine's own doubt passed on.
Field names below are the engines'. Where this document says "my call", it is the designer's
judgement, not the user's, and the user may overrule it.

### 5.1 `get_bank_summary`
- Statement: bank, account type, masked account number, account holder, period (first and last
  transaction date), pages, scanned pages.
- Reading quality: `summary.counts` (VERIFIED, FAILED, UNVERIFIED, OPENING), `unresolved_count`
  and `unresolved_amount` (money whose direction could not be decided, missing from both
  totals), `dates_inferred`, `statement_check` (the printed summary against the rows read),
  `ledger_notes`.
- Totals: opening and closing balance, total credits, total debits, transaction count.
- Monthly: for each month, credits, debits, their counts, net, and a **completeness status**
  with its reason. The average monthly credit and debit are taken **over complete months
  only**, with the months used named. If no month is complete, no average is given, and the
  result says why. (The engine's own internal average falls back to all months there; the MCP
  does not.)
- **How a month is judged complete.** Checked on 2026-10-31: the engine's own test
  (`bsa_engine.signals.scoring._complete`) uses only the first and last transaction dates,
  within 3 days of the month's edges. That alone is not enough, so a month is **complete**
  only when all of these hold:
  1. the transactions span the whole month, by the engine's own edge test;
  2. if the statement prints its period (`statement_check.period`, read from the first
     pages), that period covers the whole month;
  3. no `PERIOD_NOT_COVERED`, `PAGE_BOUNDARY_BREAK` or `DATE_GAP` signal (the engine's
     continuity checks, delivered inside `fraud_signals`) falls within the month;
  4. no row in the month has an unresolved direction (that money is missing from both
     totals).

  Otherwise the month is **partial** (fails 1 or 2) or **doubtful** (fails 3 or 4), and the
  reason is given.
- The declared period is not always available. On a merged multi file result the engine does
  not compute `statement_check`, and some statements print no period. Then condition 2 cannot
  be checked, and `limitations` says: "statement period not printed; completeness judged from
  transaction dates and continuity checks only". Chain breaks between merged files
  (`statement_chain_breaks`) count under condition 3.
- Average balance: `abb` windows (1, 3, 6, 12 months) as the engine computes them.
- Credits net of own transfers, loan disbursals, reversals and bounce returns (`credits`), with
  the gross figure beside it. Call it "credits", never "income".
- Bounces (`bounces`): count, the split the engine gives, and instances.
- EMI debits seen (the engine's EMI series, `signals.loans.emi_series`), cash deposit share
  (`cashflow.cash_dependency_ratio`), category totals (`category_summary`), overdraft drawing
  profile when the account is an OD or CC (`overdraft`).
- **Salary like credits** (`salary`), named exactly that: "salary like credits detected by the
  engine (recurring credits matching salary patterns); not verified income".
- Signals: `red_flags` and `fraud_signals` passed through as reported, **with their full
  evidence**: code, severity, detail, and every `instances` entry (dates, amounts, pages) the
  engine attaches. Each signal also carries the line "an attention signal from the engine's
  checks; not a conclusion". My call: they are deterministic checks with evidence, like
  AutoCAM's DPD doubt, not a score.
- For several files: files merged, duplicates removed, merge conflicts.
- **Never included:** `score` (and everything under it, `foir_dscr` included).

### 5.2 `find_bank_transactions`
Filters, all combined with AND: `date_from`, `date_to`, `direction` (credit, debit, any),
`min_amount`, `max_amount`, `narration_contains` (case insensitive), `category`, `status`
(the reconciliation status). Sort by date or amount, `descending`, `limit` (1 to 200) and
`offset`. Returns `total_matches`, `has_more`, one page, and totals over **all** matches (count
and sum of credits, count and sum of debits). A row whose amount or direction is unknown is
never summed; count it in `excluded_because_value_unknown`, exactly as
`find_bureau_accounts` does.

### 5.3 `get_itr_financials`
One entry per financial year, oldest first:
- year, provisional or not, entity, form, assessment year, filing date, PAN masked
- whether the statements reconciled (the engine's check status per block: verified,
  unverified, failed) and `mapping_check`
- the P&L and balance sheet values (`values`, 41 keys) with plain labels, in rupees. A `None`
  value is "could not be read", stated as such.
- the ratios, **filtered**. Found on 2026-10-31: the engine's ratio helper reads an unread
  value as 0 (`_g(d, key)` returns `d.get(key) or 0`). So when one statement was not read, a
  ratio can come back as a confident 0.0, which breaks "unknown is never zero". FinSight
  already solved this (`FinSight/backend/business/ratios.py`, `RATIO_INPUTS`): a ratio is
  published only when every statement it draws on (P&L, balance sheet or both) was read for
  that year. The MCP copies that small table, citing its source, rather than importing
  FinSight, which stays a non-dependency. A withheld ratio appears as `null` with the reason.
  Each published ratio carries its formula from `itr_engine.mapping.template_config.RATIOS`,
  labelled "a standard ratio derived from this year's statements".
- **DSCR is never published by the tools.** Its formula,
  `(PAT + depreciation + interest) / (interest + asset financed loans / 4)`, assumes
  term debt is repaid over four years. FinSight withholds it for that reason, with two real
  cases where it misled. Interest coverage stays, under the rule above.
- `flags`, `summary` (revenue CAGR) and the `attention` rows (what to check and where)

### 5.4 `get_gst_summary`
**Shape.** The engine keys its output by PAN and GSTIN (`canonical.borrowers[PAN].gstins[GSTIN]`).
Those keys are not masked in place: two GSTINs of the same PAN can mask to the same string and
overwrite each other. The loader instead turns each into a **record**, in a list:

```
{"entity_id": "gstin-1", "gstin_masked": "29**********1Z5", "pan_masked": "******234F", ...}
```

`entity_id` is opaque and stable within the document, assigned in the engine's order. Every
other reference in the report (files, findings, returns, confidence keyed by GSTIN) is
rewritten to use the `entity_id`. Unmasked mode adds `gstin` and `pan` as fields; it never
restores them as keys.

Per GSTIN record:
- legal and trade name, filing pattern (monthly or quarterly), coverage of the window
- per period (`series.points`): aggregate turnover, taxable outward, output tax, net ITC, tax
  paid through ITC and in cash, interest and late fee, extraction confidence
- FY totals (`fy_totals`) with months covered against months expected
- trends (`series.trends`) and filing timeliness (due date, ARN date, delay)
- `findings` exactly as the engine words them (they are attention signals and say so),
  validation counts, confidence, and the files skipped
- My call: the engine's per period ratios (cash tax ratio, ITC utilisation and the rest) are
  included. They are observations from the return's own tables, and the engine labels them
  that way.

## 6. The Excel workbook

`export_excel(document_ids)` writes one `.xlsx` under `<home>/workbooks/` and returns its path.
The workbook gets one sheet per document id, in the order given. Two documents of the same
type get "Bank (1)" and "Bank (2)", and so on.

**Bureau sheet.** AutoCAM's first sheet, "CIBIL Summary", copied exactly. v1 already writes
AutoCAM's workbook on load (`loader.load`); open that file and copy its first sheet. On a
Commercial report this leaves out AutoCAM's second sheet, "Credit Analysis" (the market
comparison). My call, following "1 document = 1 sheet"; its facts stay available through
`get_bureau_summary`, and the full AutoCAM workbook still sits in `workbooks/`.

**ITR sheet.** ITR Extractor's first sheet, "ITR Validation", copied exactly, live formulas
included. Write the engine's workbook (`itr_engine.excel_generator.generate_excel`, check its
signature) at load time, as bureau does, then copy its first sheet. Its formulas refer to cells
on the same sheet, so they survive the copy; verify this by opening the result.

The copy is cell by cell, because openpyxl cannot move a sheet between workbooks. Copying is
chosen over rebuilding so every improvement the user makes to AutoCAM or ITR Extractor reaches
the MCP's Excel unchanged.

What the two sheets actually use (checked in both generators on 2026-10-31), so these are what
the copy must carry: values and formulas, font, fill, border, alignment, number format,
comments (ITR's "not read" notes), column widths, row heights, merged cells, freeze panes, and
print settings (CIBIL: print title rows, landscape, fit to width). Neither sheet uses
conditional formatting, named ranges, data validation, images or charts. ITR's formulas all
refer to cells on the same sheet (`template_config` builds them as `=B7-B17`). **The copy must
not assume this stays true:** if a source sheet ever gains any of the unused features, or a
formula referring to another sheet, the export fails loudly ("the ITR sheet uses X, which
the Excel export does not copy yet") rather than producing a silently broken sheet.

**The ITR sheet's DSCR is replaced by a note** (decided 2026-10-31). DSCR appears twice on
that sheet: its row in the Ratios block, and the Financial Snap row that repeats it
(`template_config.SNAP_RATIO_NAMES`). After the copy, every year cell of both rows holds the
text "Not shown" in place of the formula, with a cell comment: "DSCR is not shown: the
formula assumes asset financed loans are repaid over four years (interest + loans / 4), which
the ITR does not state." The row labels stay, so the layout is otherwise identical. Locate
both rows by their label in column A, not by a fixed row number, and fail loudly if either is
not found. No other formula on the sheet refers to the DSCR cells (checked: the Snap repeats
the ratio and nothing reads it); verify that again on the generated sheet.

**Bank sheet and GST sheet.** New, built in the MCP, in the **same visual style** as the two
copied sheets (both use it: see `CIBIL_EXCEL/excel_generator.py` and
`ITR Extractor/engine/excel_generator.py`):
- Arial throughout; navy `1F3864` header bars with white bold text; thin borders `BFBFBF`;
  alternating `F2F2F2` and white rows; totals on yellow `FFF2CC`; Indian comma notation for
  rupees
- a cell that could not be read is shown as text ("Not read") on red `FFC7CE`, never 0
- a header block at the top: label in bold navy, value beside it

Bank sheet: header block (bank, account type, masked account number, period, opening and
closing balance, rows reconciled out of total, scanned pages skipped), then a month by month
table (month, credits, credit count, debits, debit count, net, bounces, EMI debits), then a
yellow total row and a yellow "average over complete months" row. No FOIR, DSCR or score.

GST sheet: header block (GSTIN masked, legal name, filing pattern, window and coverage), then a
period by period table (period, aggregate turnover, output tax, net ITC, tax paid in cash, tax
paid by ITC, filing delay in days), then yellow FY total rows with months covered.

**Explicit ids, and what a combined workbook does not mean.** `export_excel` takes a
non empty list of document ids and nothing else: no "everything loaded" default. Its result
lists, for each sheet, the document id, type, source file names and entity (borrower, account
holder or legal name, masked as usual), and states: "These documents were combined as
requested. Combining them does not establish that they belong to the same borrower, and no
reconciliation between them has been made." The same text, with the per sheet source list,
goes in the workbook's document properties (title and description). No extra sheet is added,
so "1 document = 1 sheet" holds.

**Masking in the Excel.** My call: the workbook stays on the user's machine, like the PDF it
came from, and is never sent to the LLM (the tool returns only its path). So the two copied
sheets keep what their engine wrote. The two new sheets follow the server's masking setting,
like the tool results.

## 7. Rules carried from v1, and how they apply to the new documents

- **Unknown is never zero.** Bank: `unresolved_amount`, FAILED rows and inferred dates are
  reported, never folded in. ITR: a `None` value is "could not be read". GST: `-` was read
  as N/A by the engine; keep it unknown.
- **The engine's doubt is passed on.** Bank: reconciliation counts, `statement_check`,
  `vlm_recommended` (say "the engine suggests a closer read of thin OCR pages"; do not run
  Vision). ITR: check status per year, `attention`. GST: `extraction_confidence`, validation
  counts.
- **Scanned pages.** The bank engine skips scanned pages unless Vision runs, and Vision is off.
  Verify whether its local OCR covers them when the `ocr` extra is installed; either way, state
  the scanned page count in `limitations` and in the sheet.
- **No LLM.** Bank: `use_vlm_refinement=False`. ITR: `api_key=None`, `use_vision=False`. GST
  has no LLM path. Never read or pass `GEMINI_API_KEY`. The ITR and bank repos keep a `.env`
  with that key; do not load it, and check that no engine picks it up from the environment on
  its own.
- **Masking**, on by default (`LENDING_MCP_UNMASKED=1` turns it off): PAN, DOB and phone as in
  v1, plus the bank account number (last four digits shown), the ITR PAN, and GSTIN (it
  contains the PAN). GST uses PAN and GSTIN as dictionary keys; the loader turns those into
  records with opaque ids (5.4) instead of masking keys in place.
- **Only `allowed_dirs` load**, for every path in a list. GST also accepts a folder; the folder
  must be inside `allowed_dirs`, and so must every file found in it.
- **Logs** carry no extracted values. stdout stays the MCP protocol.
- **Errors** reach Claude as clear `ToolError` messages: a locked PDF ("password needed"),
  statements of different accounts, no GSTR-3B found, an engine that failed (type name only, as
  v1 does).

## 8. Caching and layout

The same pattern as v1's `loader.py`: parse once, store a JSON-safe dict under
`<home>/reports/<document_id>.json`, keep it in memory too.

**The cache holds normalised, unmasked data only. Masking is applied when a response or a
sheet is built**, from the current setting, as v1's bureau tools already do
(`bureau.envelope`, `bureau.identity`). So switching `LENDING_MCP_UNMASKED` never serves a
stale or wrongly masked result, and a cached file never needs to be rebuilt for masking. The
GST records of 5.4 are built at normalisation time with both the full identifiers and the
opaque `entity_id`; the masked fields are derived at response time.

**What the cache key covers**, so any change that could alter a reading parses afresh:
- the input files' bytes (sorted)
- a hash of the engine's own source files (as v1 does for AutoCAM)
- the installed versions of the packages that do the reading: `pymupdf` and `openpyxl` for
  all; `rapidocr`, `onnxruntime` and `pytesseract` where the engine uses them; `pyyaml` for GST
- for GST, a hash of `config/policy.yaml` (it changes the analysis)
- a `NORMALISE_VERSION` constant in the MCP, raised whenever the normalised shape changes

The bureau `report_id` gains the same package versions and constant. Existing v1 cache
entries are then simply re-parsed once; nothing else depends on their ids. The engines return dataclasses,
`date` values and dicts keyed by `(year, month)` tuples; the loader converts them to JSON-safe
forms once, at load time (dates as ISO strings, month keys as `"2025-10"`), and the query
functions work only on that stored form.

Suggested layout (builder may adjust):

```
lending_mcp/
  server.py     tools, argument validation only
  loader.py     shared file checks and cache; one load function per document type
  bureau.py     unchanged
  bank.py       pure query functions over the stored bank result
  itr.py        pure query functions over the stored ITR result
  gst.py        pure query functions over the stored GST result
  workbook.py   export_excel: sheet copy, and the bank and GST sheets
  masking.py    shared masking, if bureau.py's can be lifted out cleanly
```

## 9. Build order and acceptance criteria

Built in sequence. Each step is shown working on a real file (counts only printed) and
accepted before the next starts.

**Step 1. Shared infrastructure.** The engines join `pyproject.toml` as editable path sources.
The shared loader pieces (path checks for lists and folders, cache, ids, masking) are lifted
out of the bureau loader without changing its behaviour.
- `uv sync` from scratch succeeds, and all 67 v1 tests still pass.
- The cache key covers what section 8 lists. Changing a package version, the policy file or
  `NORMALISE_VERSION` gives a new id (tested). Masking is applied only at response time.
- A permanent test proves each engine module resolves inside its own repo and that no bare
  `engine` module is loaded (the section 3 proof, kept).
- The stdio test lists the tools.

**Step 2. Bank.** `load_bank_statement`, `get_bank_summary`, `find_bank_transactions`.
- A single statement and a multi file merge both load. Different accounts and a locked PDF
  without a password each give a clear error.
- Month completeness follows 5.1. A test exists for each reason a month is partial or doubtful.
- Averages use complete months only, and none is given when no month is complete.
- `find_bank_transactions` totals cover all matches and never sum an unknown amount.
- The word FOIR, DSCR or score appears nowhere in any bank result (asserted).
- Masking covers the account number. Salary is named as in 5.1.
- Shown working on a real statement and on a real multi file set.

**Step 3. ITR.** `load_itr`, `get_itr_financials`.
- Year columns, values with labels, and check status per year are returned.
- A ratio is withheld when a statement it needs was not read (a test for each of P&L only and
  balance sheet only). DSCR is never returned.
- PAN masked.
- A real bundle loads. The load time on a scanned bundle is measured and reported (risk 1).

**Step 4. GST.** `load_gst_returns`, `get_gst_summary`.
- Entities are returned as records with opaque ids (5.4). A test has two GSTINs under one PAN,
  so their masked strings would collide as keys.
- No **full, unmasked** PAN or GSTIN value from the input appears anywhere in a masked
  result: keys, values or nested text (asserted by a recursive scan for the exact fixture
  identifiers). Masked forms such as `29**********1Z5` are expected and allowed.
- The same cached document, served once masked and once with `LENDING_MCP_UNMASKED=1`, gives
  the right form each time without re-parsing (cache hit both times).
- Non GST files are listed as skipped. A folder inside `allowed_dirs` works; a folder outside
  is refused.
- A real case folder loads.

**Step 5. Excel.** `export_excel`.
- Workbooks of 1, 2 and 4 documents are built.
- The copied CIBIL and ITR sheets are compared with their source sheet cell by cell: value or
  formula, displayed number format, font, fill, border, alignment, comments, merged ranges,
  column widths, row heights, freeze panes and print settings. Every difference fails the test.
- The ITR formulas are recalculated outside openpyxl (LibreOffice headless, if installed;
  otherwise opened in Excel by the user once) and match the source workbook's values.
- The bank and GST sheets show "Not read" in place of an unknown, and contain no FOIR, DSCR
  or score.
- On the ITR sheet, both DSCR rows (Ratios and Financial Snap) hold "Not shown" with the
  explanatory comment in every year cell, and no other cell on the sheet differs from the
  source except those.
- The disclaimer and source list appear in the tool result and the document properties.
- An unsupported feature in a source sheet makes the export fail loudly (tested with a
  synthetic sheet that has conditional formatting).

## 10. Testing

As v1: synthetic fixtures shaped like each engine's real output (no real data in the repo),
the engines replaced by fakes in unit tests the way `loader.load` already takes `parse` and
`make_workbook`. Cover, for each new document type: load, cache hit, every filter, totals over
all matches, unknown values, masking (keys too, for GST), refused paths, and the error cases
in section 7. For the Excel: one workbook from each combination size (1, 2, 4 documents), the
copied sheets' formulas intact, no "FOIR", "DSCR" (bank) or "score" text anywhere in a bank
sheet. Keep the stdio test.

Then, locally only: run each engine path once on real files from `FinSight/storage` (the
`finsight.db` `document` table says which file is which type) and print counts only.

## 11. Risks the builder should check early

1. **Slow loads.** ITR OCR on a scanned bundle and GST on a large folder can take minutes. MCP
   clients may time out a long tool call. Measure on a real scanned ITR; if it is near the
   limit, say so to the user before building around it.
2. **Dependency resolution** of four engines in one venv. Proven in a clean venv (section 3);
   the risk left is the real `uv sync` with `uv.lock`.
3. **The ITR workbook's formulas** after the cell by cell copy (step 5 tests it).

## 12. Status

**Frozen on 2026-10-31.** The architecture is approved; no further redesign. Changes during
the build are limited to what an acceptance criterion in section 9 forces, and each one is
reported to the user.

## 13. After this

The batch run the user already asked for: every file in `C:\Users\91748\Downloads\CIBIL`
through the bureau loader, with a table by file number (provider, loaded or refused, account
counts, reconciles or not, DPD doubt counts), names never printed. Then update `CLAUDE.md`,
`docs/project.md` and the README for v2.
