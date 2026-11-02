# Lending MCP

Ask Claude (or any MCP client) about a loan file's documents, a credit bureau report, bank
statements, ITRs and GST returns, without the LLM ever reading the PDFs.

Each document is read once, on your machine, by its own deterministic engine:
[AutoCAM](https://github.com/Sanjay-00/AutoCAM) for bureau reports, Bank Statement Analyser,
ITR Extractor and GST Analyser. Lending MCP exposes what they read through twelve tools.
Claude chooses what to ask for; the counting, filtering and totals happen in code.

> "Which active accounts have been 90+ DPD?" → `find_bureau_accounts(status="active", min_dpd=90)`
> "What came in each month, on average?" → `get_bank_summary(document_id)`

Bureau reports: **CRIF High Mark** retail and Commercial ACE (PDF or HTML). TransUnion CIBIL
is not supported yet. Bank statements: digital PDFs of Indian banks. ITR: one PDF per
financial year. GST: GSTR-3B returns.

## Tools

| Tool | What it answers |
|---|---|
| `load_bureau_report(path)` | Parses a report (cached afterwards), writes AutoCAM's workbook, returns a `report_id`, the borrower, score, account counts and whether the reading reconciles with the report's own totals |
| `get_bureau_summary(report_id)` | Counts by status; known totals of sanction, outstanding, EMI and overdue; worst DPD ever, in 12 months and last reported; written off, settled, suit filed and delinquent accounts |
| `find_bureau_accounts(report_id, ...)` | Any set of accounts, filtered, sorted and paged, with totals over all matches |
| `get_bureau_account(report_id, sr_no)` | Every field of one account, unknown fields and DPD doubts explained |
| `load_bank_statement(paths, password)` | One statement, or several of one account merged; returns a `document_id`, the dates covered, rows reconciled and which months are complete |
| `get_bank_summary(document_id)` | Balances, monthly credits and debits, averages over complete months, average balance, credits net of own transfers, bounces, EMI debits seen, salary like credits, the engine's attention signals |
| `find_bank_transactions(document_id, ...)` | Transactions by date, credit or debit, amount, narration, category; totals over all matches |
| `load_itr(paths)` | An ITR bundle; returns a `document_id`, each year found and whether its statements were read |
| `get_itr_financials(document_id)` | Year by year P&L and balance sheet figures, standard ratios with formulas, the engine's flags |
| `load_gst_returns(paths)` | GSTR-3B files or a folder; other documents are listed as skipped |
| `get_gst_summary(document_id)` | Per GSTIN: turnover, tax, ITC, tax paid by period, FY totals, filing delays, findings |
| `export_excel(document_ids)` | One workbook, one sheet per document loaded (see below) |

## What you can rely on

- **Unknown is never zero.** A value an engine could not read is `null` in answers and "Not
  read" in the workbook. Totals say what they could not count.
- **The engines' doubts are passed on**: AutoCAM's DPD doubts and its reconciliation with the
  report's printed totals, the bank engine's balance checks and missing page signals, ITR
  Extractor's statement checks, GST Analyser's validation and confidence.
- **A bank month counts as complete** only when the transactions span it, the statement's
  printed period covers it, and no missing page, date gap or undecided row falls in it.
  Averages use complete months only.
- **No internal scoring.** The bank engine's FOIR, DSCR and cash flow score are never shown.
  ITR's DSCR is not shown either (its formula assumes four year repayment), and an ITR ratio
  is withheld when a statement it needs was not read. Bank credits are credits, never income.
- **Limitations are stated in every answer.**

## The workbook

`export_excel` builds one workbook from the documents you name: one sheet per document, in
your order. The bureau sheet is AutoCAM's CIBIL Summary and the ITR sheet ITR Extractor's ITR
Validation, both copied exactly (the DSCR rows read "Not shown"). The bank and GST sheets are
built in the same style. Combining documents does not establish that they belong to one
borrower, and no reconciliation between them is made; the workbook says so.

## Privacy

The PDFs stay on your machine and are never sent to the LLM. **The facts the tools return do
go to whichever LLM provider your client uses**: names, lenders, amounts. PAN, date of birth,
phone, bank account number and GSTIN are masked by default (set `LENDING_MCP_UNMASKED=1` to
show them). The server reads only files inside folders you allow, never calls an AI model
(no Gemini, no Vision), and writes no extracted values to its logs.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and the four engines as sibling
folders of this repo: `CIBIL_EXCEL` (AutoCAM), `Bank Statemnt Analyser`, `ITR Extractor` and
`GST Analyser`. Scanned documents need Tesseract OCR or RapidOCR.

```
git clone <this repo> lending-mcp
cd lending-mcp
uv sync
```

Create `~/.lending-mcp/config.toml`:

```toml
allowed_dirs = ["C:/Users/you/Documents/Bureau reports"]
autocam_dir  = "C:/Users/you/Projects/CIBIL_EXCEL"
```

### Claude Desktop

Add this to `claude_desktop_config.json`, then restart Claude Desktop. The file is in
`%APPDATA%\Claude\` on Windows; the Microsoft Store build keeps it in
`%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\`.

```json
{
  "mcpServers": {
    "lending-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/lending-mcp", "lending-mcp"]
    }
  }
}
```

### Claude Code

```
claude mcp add lending-mcp -- uv run --directory C:/path/to/lending-mcp lending-mcp
```

## Slow first reads

A scanned ITR or GST return is read by OCR, and its first read can take a minute or two. The
load tool then answers "still reading, ask again in a minute" instead of timing out, and the
reading carries on. Every later load of the same files is instant.

## Where data is kept

Read documents are cached as JSON in `~/.lending-mcp/reports/`, and workbooks are written to
`~/.lending-mcp/workbooks/`. A cache entry is keyed by the files' SHA-256, the engine's source,
the versions of the packages that do the reading and, for GST, its policy file, so any change
that could alter a reading parses afresh. The cache is unmasked; masking is applied to each
answer. To remove everything, delete those two folders.

## Tests

```
uv run pytest
```

The tests run on synthetic documents shaped like the engines' real output, and go through a
real MCP client, both in process and as a stdio subprocess.
