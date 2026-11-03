# Lending MCP. Project record

Written 2026-10-31, at the end of the session that designed and built v1. It is meant to give a
new session (or a new reader) the whole context without asking the user again.

## 1. The goal, in the user's words

> "I want to connect Claude Desktop or Claude Code to my existing CIBIL and bank statement
> analysis engines and be able to ask virtually any relevant question about the underlying
> documents through natural language."

> "assume i send a cibil and instead being parsed by llm our mcp will handle it and give the
> excel analysis for full analysis or whatever the llm ask like give me cibil score or give me
> max dpd overall, or last 12 months max dpd, or how many loans are there"

Example questions it must handle:
- all active loans, and the total outstanding
- accounts reported at 90+ DPD
- one account explained
- written off or settled accounts
- the worst delinquency
- the reported monthly obligations

Why it matters to the user:
1. Free, consolidated, accurate analysis of lending documents, open source, positioned against
   Perfios. The user's critique of Perfios: "too much info and scattered", no cross document
   view, low extraction accuracy on some documents.
2. Usable from any LLM client: Claude Desktop, Claude Code, Cursor, and later ChatGPT and
   claude.ai connectors.
3. A portfolio and interview piece.
4. If it proves better, FinSight may later use the same layer.

## 2. The idea in one picture

```
You: "Which active loans have been 90+ DPD?"
        |
Claude (MCP client)  chooses a tool, never sees the PDF
        |  find_bureau_accounts(report_id, status="active", min_dpd=90)
Lending MCP server   validates, filters, totals in code
        |
AutoCAM parse, done once, cached as JSON   <- the PDF, read locally
        |
Claude answers from the returned rows and totals
```

The LLM chooses and explains. The deterministic engine reads and computes. The PDF never
leaves the machine, but the facts the tools return do go to the LLM provider. That is stated
in the README.

## 3. Decisions, with reasons

| Decision | Reason |
|---|---|
| A separate repo (`Personal Projects/lending-mcp`), not inside FinSight | The user wants it open source. FinSight encodes the structure of a confidential lender policy and holds real case data and private docs, so it stays private. |
| AutoCAM loaded from its folder, not copied | One source of truth for parsing, so AutoCAM fixes reach the MCP at once. AutoCAM is not packaged yet (it uses a bare `import parser`); packaging it is later work. |
| Four tools, not one per question | A tool per question would never end. A single flexible `find_bureau_accounts` (filters, sort, paging, totals over all matches) answers most questions, and the LLM composes the arguments. |
| Totals and filters computed in code | The LLM never adds figures. Tools return totals over all matches, not just the page shown. |
| CRIF only in v1; TransUnion refused | The user's call. AutoCAM's TransUnion path has known gaps: written off and settled accounts read as Closed, an unreadable DPD may read as 0, and there are no tests and no real sample. |
| HTML kept | Verified that AutoCAM supports it (`parser.py::_is_html_source`, `_read_html`); its README lists "PDF or raw HTML export". A reviewer had suggested PDF only. |
| Unknown is never zero; not provided is never false | AutoCAM already marks unreadable values `None`. CRIF retail has no settled, suit filed or delinquent fields. Treating either as 0 or false would invent facts. |
| Cache key = file SHA-256 + a hash of AutoCAM's engine source files | The same file loads instantly, and any AutoCAM change, even an uncommitted one, parses afresh. A git commit would miss uncommitted edits, and AutoCAM had uncommitted changes. |
| Only `allowed_dirs` readable | The LLM supplies paths, so the server must not be able to read arbitrary files. |
| PAN, DOB and phone masked by default | Tool results go to the LLM provider. `LENDING_MCP_UNMASKED=1` turns masking off locally. |
| Official MCP Python SDK `mcp==2.3.0`, `MCPServer` from `mcp.server.mcpserver` | The current stable release, verified installed. In v2, FastMCP was renamed MCPServer, and old tutorials no longer apply. |
| stdio transport, local only | No hosting or login needed. The web connector (ChatGPT, claude.ai) needs HTTP, authentication and an upload step; that is later. |
| `uv` for the environment and lock | The user's choice. |
| No FOIR, no affordability ratio, no calculators | Bank credits are not verified income. A FOIR needs an income methodology (future ITR work). The user explicitly removed the EMI calculator: no arbitrary financial tools. |
| No LLM inside the server, Vision off | The engines own extraction and arithmetic. AutoCAM's paid Gemini Vision path is never called. |

## 4. What was tried and dropped (do not reintroduce without the user asking)

The plan went through five rounds of external review (ChatGPT, relayed by the user). Early
rounds kept adding machinery, and the user stopped it: "what are we doing so complex? our 4
engines are made, now we just need a working mcp".

Dropped:
- **Process isolation:** sandboxed child processes with CPU, memory and disk limits, a network
  egress guard, and a preflight child for PDF parsing.
- **Case and run tracking:** a full case store with document states, analysis run ids, stale
  handle rejection, and a multi part cache key.
- **An admin command line** for deletion.
- **Calculators and ratios:** `calculate_emi`, a "bank credit obligation ratio", any FOIR
  proxy.
- **The wider v1 scope:** a bundle of financial conventions for bank income, an obligations
  merge, an as-of date model, and wrapping the whole FinSight rule set (verification, fraud,
  data quality).
- **A raw text search tool:** it would hand PDF text to the LLM, the thing this design avoids.

Several of these may return when there is a concrete need: hosting, many users, bank
statements. Bring them back as a deliberate decision, not by default.

## 5. What AutoCAM gives (inspected 2026-10-31)

`parser.parse(path, api_key=None)` returns:
- **Borrower:** `name`, and `score` (an int on CRIF retail, "NA" when there is none; on
  Commercial a rank string such as "3 (Medium Risk)", where 1 is the best).
- **Report:** `provider` (`crif`, `crif_commercial`, `transunion`), `extraction_method`, and
  `identity` (PAN, DOB, phone).
- **`validation`:** the key `valid`, plus `issues` and the extracted versus printed counts
  and totals.
- **`analysis`:** `credit_profile_summary` and `derog_summary`; on Commercial also
  `borrower_summary`, the market comparison.
- **Vision flags:** recommended, used, pages.
- **`accounts`:**
  - `sr_no`, `entity` (the lender), `type_of_loan`, `ownership`
  - `date_of_sanction` (dd-mm-yyyy)
  - `sanction_amount`, `current_balance`, `emi`, `overdue`
  - `max_dpd`, `max_dpd_12mo`, `last_reported_dpd`
  - `dpd_sources_disagree`, `dpd_block_contaminated`
  - **CRIF retail:** `status` is Active or Closed. Also `written_off` (a bool) and
    `status_per_cibil` (the report's printed tag).
  - **CRIF Commercial:** `status` is Active, Closed, Written Off or Settled. Also
    `delinquent`, `suit_filed`, and `dpd_reported` (False means the bureau reports no DPD).

`excel_generator.generate_excel(data)` returns the CAM workbook as bytes: a "CIBIL Summary"
sheet, plus "Credit Analysis" on Commercial.

Not extracted: credit enquiries (the section is skipped on purpose), the month by month DPD
grid (read only to compute the worst values), account numbers, closed dates, last payment
dates, page references.

## 6. Verification done (2026-10-31)

- `uv run pytest` passes, 67 tests:
  - every filter on its own and combined, sort, paging and aggregates
  - missing values and unavailable flags
  - invalid inputs: an unknown id, a bad filter, a wrong extension, a fake PDF, an oversized
    file, a path outside the allowed folders, no folder configured, TransUnion
  - masking, and the cache (a hit, a restart, an engine change)
  - a real MCP client in process, plus a real stdio subprocess
- **Real reports:** 8 CRIF reports from `FinSight/storage` (4 retail, 4 Commercial) run
  through the actual stdio server.
  - Every sanction amount (359 of 359) and outstanding balance (78 of 78) appears in the
    original PDF text.
  - Every worst DPD value appears in the PDF. CRIF prints DPD zero padded, such as
    `078/XXX`.
  - The second load was a cache hit, workbooks were written, and PAN was masked.
- **The real launch command** (the one Claude Desktop uses) lists the four tools and refuses a
  file outside the allowed folder. Claude Code shows "Connected".

## 7. Known issues (AutoCAM side, not the MCP)

1. **DPD doubt is common.** On one retail report AutoCAM flagged 42 of 58 accounts with
   `dpd_sources_disagree`. Both worst DPD holders traced were doubted: the value is printed
   in the report, but it may belong to a neighbouring account. The MCP reports the doubt.
   Reducing it is the top AutoCAM improvement.
2. **Readings that don't reconcile.** 3 of the 8 reports did not reconcile with their printed
   totals (1 retail, 2 small Commercial). The MCP says so in `limitations` and
   `reconciles_with_report_totals`.
3. **Two example questions are unanswerable today:** enquiries in the last six months, and
   one account's payment history. AutoCAM does not extract them.

## 8. Future scope, in order

1. **The user evaluates v1** in Claude Desktop on real reports and lists the questions it
   could not answer. That list decides what comes next.
2. **AutoCAM improvements**, driven by step 1: fewer DPD doubts, extracting enquiries,
   returning the month by month DPD grid (then add `get_payment_history`), and account
   numbers and dates.
3. **Bank statements**, in the same shape over Bank Statement Analyser (`bsa_engine`). A load
   tool, a summary, a transaction finder (date range, credit or debit, amount, narration
   contains, category, with sums in code) and monthly figures. Bank credits stay descriptive
   cash flow, never "income".
4. **Cross document checks** once bureau and bank both work alone: bureau loans against bank
   EMI debits, and identity across documents.
5. **ITR**, then **GST**. ITR first needs an income methodology: salaried versus self
   employed, the schedules, the assessment years and the annual to monthly conversion. Only
   then can a FOIR exist, with its formula and sources visible. GST Analyser is not under git
   yet.
6. **TransUnion** support, once a real TransUnion report is available to test against.
7. **Before going public:**
   - package AutoCAM so it installs (today it is a bare `import parser`)
   - rewrite AutoCAM's and ITR Extractor's public git history, which contains a real PAN and
     borrower names
   - choose licences (note: PyMuPDF is AGPL-3.0, and every engine depends on it)
   - `git init` this repo (the user does all git writes)
8. **A web connector** for ChatGPT and claude.ai: streamable HTTP, authentication, and an
   upload step outside the chat so the PDF never enters it.
9. **FinSight adoption**, only if a comparison on FinSight's golden cases shows the MCP
   matches or beats FinSight's own reading. The user decides.
10. **Bring your own policy:** a lender loads its own rulebook to grade the facts. Never the
    user's confidential Shriram policy.

## 9. How to resume

Open a chat in this folder. `CLAUDE.md` loads automatically and points here. To check
everything still works: `uv sync`, then `uv run pytest`. In Claude Desktop, drop a CRIF PDF
into `C:\Users\91748\Documents\Bureau reports` and ask, for example: "Load the bureau report
<file name> and list all active loans with their outstanding."

## 10. v2: four documents and one Excel (2026-10-31 to 2026-11-01)

The user widened the scope: "lets just focus on individual doc where anyone can connect this
mcp to their workflow and get analysis of financial docs". Reconciliation across documents
stays later work. The design (`docs/superpowers/specs/2026-10-31-four-document-mcp-design.md`,
frozen) went through two rounds of ChatGPT review relayed by the user; the plan is
`docs/superpowers/plans/2026-10-31-four-document-mcp.md`.

### Decisions, with reasons

| Decision | Reason |
|---|---|
| Plain facts only; the bank engine's FOIR, DSCR and 0 to 1000 score are never exposed | The user: "thats internal scoring system". FOIR is computed by the bank engine (`signals/scoring.py`), not by GST |
| ITR DSCR never given; "Not shown" on both DSCR rows of the copied ITR sheet | Its formula assumes asset financed loans are repaid over four years; FinSight withholds it after two misleading real cases |
| ITR ratios withheld when a statement they need was not read (FinSight's `RATIO_INPUTS`, copied) | The engine's ratio helper reads an unread value as 0, so a ratio came back a confident 0.0 |
| A bank month is complete only if the transactions span it, the printed period covers it, and no continuity signal or undecided row falls in it | The engine's own test uses transaction dates only; missing pages would pass it |
| GSTINs become records with opaque `entity_id`s | Masking dict keys in place can collide |
| Cache unmasked, masking per answer; cache key adds package versions, GST's policy file and `NORMALISE_VERSION` | Flipping masking must never serve a stale form; a new reader version must parse afresh |
| One workbook, one sheet per document; CIBIL and ITR first sheets copied exactly, bank and GST sheets in their style | The user loves those two sheets; copying carries every future engine improvement |
| Slow reads (every load) answer "still reading" after 40 s and finish in the background | Scanned first reads measured 46 to 150 s on real files (ITR, CRIF, bank), past typical MCP client timeouts |
| The three engines as editable uv path sources | Each ships a package named `engine`; FinSight installs them the same way |

### Verification done

- `uv run pytest`: 207 tests (synthetic fixtures, in process MCP client, stdio subprocess).
- A clean throwaway venv proved the four engines coexist (both import orders, resources found).
- Real files, counts only: bank, 3 statements (one merged pair; a statement whose printed
  period ends early shows its last month partial); ITR, 3 bundles (2 scanned), ratios withheld
  as designed, no DSCR, no PAN; GST, one case of 24 files (12 returns, 12 duplicates), no full
  GSTIN or PAN in masked output; Excel, exports of 1, 2 and 4 real documents.

### Known issues

1. The copied ITR sheet's live formulas have not been recalculated outside openpyxl
   (LibreOffice is not installed): open one export in Excel once to confirm.
2. Scanned bank pages are not read (Vision is off); the answer says how many.
3. Real Claude Desktop behaviour on a "still reading" answer is untested.

## 11. Batch run on real files (2026-11-02)

Counts only, never names. Bureau: 50 reports in `Downloads/CIBIL`: 42 loaded (30 reconcile
with the printed totals, 12 do not: 7 scanned with OCR losses, 5 digital with count, sanction
or balance gaps), 8 TransUnion refused, no crash. Bank: 30 statements in `Downloads/BS`, all
loaded. Cases: 6 in `Downloads/CAM/FC` and 2 in `Downloads/Finsight`, every type.

Fixed in the MCP, each with a test that failed first:

1. Bureau and bank loads ran inline; scanned first reads took 80 to 150 s. All loads now go
   through the background reading.
2. A failed background read made the get tools say "still being read" forever; they now
   raise the failure.
3. A bank statement whose amounts fell into the narration had 862 transactions read as
   "opening balance" rows (the engine marks any single amount row so), and the MCP published
   averages without them. Every OPENING row after the first now makes its month doubtful and
   is named in the limitations (`NORMALISE_VERSION` 2).
4. A PDF with no transactions read loaded as an empty statement; it is now refused.
5. A folder of GSTR-1 returns answered only "no GSTR-3B found"; the refusal now says what the
   files were.
6. `load_itr` now shows each year's form and the engine's warnings (an ITR-1 has no P&L or
   balance sheet at all).
7. The server instructions covered bureau and bank only; they now cover all twelve tools and
   the "reading" status.

Engine issues found (separate work, each engine's own repo):

- Bank Statement Analyser: `statement_table._finalize` marks any row with one amount as an
  opening row, and no check flags many of them; amounts in the narration column are lost.
- AutoCAM: 5 digital CRIF reports do not reconcile with their printed totals; on 2 retail
  reports AutoCAM doubts the DPD of most accounts (93 of 120, 91 of 115).
- ITR Extractor: FY2023 balance sheet not read on two bundles (one scanned).
- GST Analyser: GSTR-1 is not supported.
