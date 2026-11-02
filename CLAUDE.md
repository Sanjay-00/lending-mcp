# CLAUDE.md. Working agreement for Lending MCP

Read `docs/project.md` first. It holds the full context: why this exists, every decision with
its reason, what was verified, the known issues and the future scope. This file is the short
version and the rules.

## What this is

An open source MCP server that lets Claude (Desktop or Code, or any MCP client) answer plain
language questions about a loan file's documents without the LLM ever reading the PDFs. The
user's own deterministic engines read each document once, locally. Twelve tools expose what
they read. Claude chooses what to ask for; counting, filtering and totals happen in code.

v2 (built 2026-10-31 to 2026-11-01) covers four document types, each on its own, plus one
combined Excel: CRIF bureau reports (AutoCAM), bank statements (Bank Statement Analyser),
ITRs (ITR Extractor) and GSTR-3B returns (GST Analyser). Reconciliation between documents is
later work. TransUnion is set aside by the user. The design is
`docs/superpowers/specs/2026-10-31-four-document-mcp-design.md` (frozen) and the build plan
`docs/superpowers/plans/2026-10-31-four-document-mcp.md`.

## Layout

```
lending_mcp/
  server.py    MCPServer and the twelve tools; argument validation only, no logic
  bureau.py    pure query functions over AutoCAM's parsed dict (summary, find, account)
  bank.py      pure: normalise (drops the engine's score), month completeness, summary, finder
  itr.py       pure: year columns, ratios filtered by which statements were read, no DSCR
  gst.py       pure: GSTINs as records with opaque entity ids, summary
  workbook.py  export_excel: exact copy of the CIBIL and ITR first sheets, bank and GST sheets
  masking.py   masks and scrub (identifiers inside text too); applied per answer, never cached
  loader.py    path checks (lists, folders), engine wrappers, fingerprint, JSON cache, slow
               reads in the background
  config.py    settings from ~/.lending-mcp/config.toml and the environment
scripts/real_run.py  local check on real files; prints counts only
tests/       synthetic documents shaped like each engine's real output; in process and stdio MCP tests
```

The tools are `load_bureau_report`, `get_bureau_summary`, `find_bureau_accounts`,
`get_bureau_account`, `load_bank_statement`, `get_bank_summary`, `find_bank_transactions`,
`load_itr`, `get_itr_financials`, `load_gst_returns`, `get_gst_summary` and `export_excel`.

## Related folders (siblings under `Personal Projects/`)

- `CIBIL_EXCEL/` is **AutoCAM**, the engine. It is loaded from its folder through `sys.path`,
  as FinSight does; it is not an installable package yet. Its own `CLAUDE.md` governs work
  inside it.
- `Bank Statemnt Analyser/`, `ITR Extractor/` and `GST Analyser/` are installed as editable
  path sources (`pyproject.toml`, `[tool.uv.sources]`) under the names `bsa_engine`,
  `itr_engine` and `gst_engine`, because each ships a top level package named `engine`.
  `tests/test_infra.py` proves each resolves inside its own repo.
- `FinSight/` is the user's private underwriting app. It is **not** a dependency and stays
  unchanged. Real CRIF test reports are in `FinSight/storage/`, and `FinSight/finsight.db`
  says which file is which provider (read only).
- Real bank, ITR and GST files are in `FinSight/storage/` too (the `document` table of
  `finsight.db` says which is which, read only); bank samples are in
  `Bank Statemnt Analyser/Sample Data/`.

## Rules (do not break these)

- **Unknown is never zero.** AutoCAM leaves an unreadable value as `None` (its Excel prints
  "Check CIBIL"). It is never summed, compared or counted as 0. Totals report
  `known_total`, `accounts_counted` and `accounts_unknown`.
- **Not provided is never false.** A flag a report format does not carry reads "not provided
  by this report format" (on CRIF retail: settled, suit filed, delinquent). Filtering on it
  is refused.
- **AutoCAM's doubt is passed on, never hidden.** That means the `dpd_sources_disagree` and
  `dpd_block_contaminated` flags, plus the validation against the report's own printed
  totals.
- **Every answer** carries `limitations` and `masking`; bureau answers also `provider` and
  `extraction_method`, the others `document_type` and `engine`.
- **Only files inside `allowed_dirs` load**, every path in a list, every file in a folder.
  PAN, DOB, phone, bank account number and GSTIN are masked by default, **at answer time**:
  the cache is unmasked. Logs carry no extracted values. No AI is ever called: no Vision, no
  Gemini (`use_vlm_refinement=False`, `api_key=None`, `use_vision=False`).
- **No internal scoring.** The bank engine's `score` (FOIR, DSCR, cash flow score) is dropped
  before caching and never shown. ITR's DSCR is never given by a tool and reads "Not shown" in
  the workbook. An ITR ratio is withheld when a statement it needs was not read. Bank credits
  are never called income; salary is "salary like credits".
- **stdout is the MCP protocol.** Logs go to stderr. SDK 2.3.0 already diverts stray prints
  away from the wire, and `tests/test_server.py` checks a real stdio session.
- **No calculators, no FOIR, no affordability figures, no LLM inside the server.** See
  `docs/project.md` for why.

## How the user works

- **Keep it simple.** The user's words: "our engines are made, now we just need a working
  mcp". Build the smallest thing that works on top of the existing engines. Engine hardening
  is separate work. Flag over engineering instead of building it.
- **The user often pastes a review from ChatGPT.** Check each point against the code. Accept,
  reject or improve it with a reason (the user forwards the reasons back), and ask when a
  point conflicts with the user's own decisions.
- **The user makes every git write.** Never run `git add`, `commit` or `push`. Commit
  messages are under 13 words, subject only, with no Co-Authored-By.
- **No hyphen used as a joining dash, no em dash, no en dash** in documents, comments or chat.
  Hyphens inside compound words and identifiers are fine.
- **Never put real borrower data in the repo.** Fixtures are synthetic; real reports are used
  only locally, and printed output from them shows counts and matches, not names.

## Commands

```
uv sync                 install (Python 3.12, mcp==2.3.0 pinned)
uv run pytest           197 tests at the last run (2026-11-01)
uv run lending-mcp      start the server on stdio (normally the MCP client starts it)
uv run python scripts/real_run.py bank|itr|gst <files>   local real file check, counts only
```

While Claude Desktop or Claude Code runs the server, `uv sync` cannot replace
`.venv\Scripts\lending-mcp.exe`; close them first, or use `uv run --no-sync pytest`.

Where things are wired:
- **Claude Desktop:** the Microsoft Store build's config,
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`,
  key `mcpServers.lending-mcp`. A backup from before the edit sits beside it.
- **Claude Code:** registered at user scope (`claude mcp list`).
- **Settings:** `C:\Users\91748\.lending-mcp\config.toml`. The allowed folder is
  `C:\Users\91748\Documents\Bureau reports`.
