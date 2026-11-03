"""The four tools through a real MCP client: in process, and as a stdio subprocess."""

import json
import os
import sys
import types

import anyio
import pytest
from mcp import Client, StdioServerParameters

from lending_mcp import loader
from lending_mcp.server import mcp
from tests.fixtures import COMMERCIAL, RETAIL
from tests.test_loader import env  # noqa: F401  (the shared environment fixture)

TOOLS = {
    "load_bureau_report", "get_bureau_summary", "find_bureau_accounts", "get_bureau_account",
    "load_bank_statement", "get_bank_summary", "find_bank_transactions",
    "load_itr", "get_itr_financials",
    "load_gst_returns", "get_gst_summary",
    "export_excel",
}
LOADERS = {"load_bureau_report", "load_bank_statement", "load_itr", "load_gst_returns", "export_excel"}  # they write files


@pytest.fixture
def fake_autocam(monkeypatch):
    parsed = {"value": RETAIL}
    module = types.SimpleNamespace(parse=lambda path, api_key=None: dict(parsed["value"]),
                                   generate_excel=lambda data: b"xlsx")
    monkeypatch.setattr(loader, "_autocam", lambda d: (module, module))
    return parsed


def run(coro_fn):
    return anyio.run(coro_fn)


def payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_the_tools_with_annotations():
    async def main():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools
    tools = {t.name: t for t in run(main)}
    assert set(tools) == TOOLS
    for name in TOOLS - LOADERS:
        a = tools[name].annotations
        assert a.read_only_hint is True and a.destructive_hint is False
    for name in LOADERS:
        assert tools[name].annotations.destructive_hint is False
    props = tools["find_bureau_accounts"].input_schema["properties"]
    assert {"status", "min_dpd", "dpd_window", "limit", "offset", "sort_by"} <= set(props)


def test_every_tool_end_to_end(env, fake_autocam):  # noqa: F811
    path = str(env["allowed"] / "report.pdf")

    async def main():
        async with Client(mcp) as c:
            loaded = payload(await c.call_tool("load_bureau_report", {"path": path}))
            rid = loaded["report_id"]
            summary = payload(await c.call_tool("get_bureau_summary", {"report_id": rid}))
            found = payload(await c.call_tool("find_bureau_accounts",
                                              {"report_id": rid, "status": "active", "min_dpd": 90}))
            account = payload(await c.call_tool("get_bureau_account", {"report_id": rid, "sr_no": 2}))
            return loaded, summary, found, account

    loaded, summary, found, account = run(main)
    assert loaded["provider"] == "crif" and loaded["accounts"]["total"] == 5
    assert loaded["borrower"]["identity"]["pan"] == "******234F"
    assert summary["worst_dpd_all_accounts"]["ever"]["worst_known"] == 120
    assert [a["sr_no"] for a in found["accounts"]] == [2]
    assert account["account"]["lender"] == "Beta Finance"


def test_errors_come_back_as_tool_errors(env, fake_autocam):  # noqa: F811
    fake_autocam["value"] = COMMERCIAL
    path = str(env["allowed"] / "report.pdf")

    async def main():
        async with Client(mcp) as c:
            rid = payload(await c.call_tool("load_bureau_report", {"path": path}))["report_id"]
            return [
                await c.call_tool("get_bureau_summary", {"report_id": "nope"}),
                await c.call_tool("find_bureau_accounts", {"report_id": rid, "status": "dormant"}),
                await c.call_tool("find_bureau_accounts", {"report_id": rid, "limit": 500}),
                await c.call_tool("get_bureau_account", {"report_id": rid, "sr_no": 42}),
                await c.call_tool("load_bureau_report", {"path": str(env["outside"] / "report.pdf")}),
            ]

    results = run(main)
    assert all(r.is_error for r in results)
    assert "outside the allowed folders" in results[-1].content[0].text


def test_stdio_process_speaks_only_protocol(env):  # noqa: F811
    """The real server as a subprocess over stdio: the handshake, the tool list
    and a tool error all arrive intact, so nothing but protocol frames reached
    stdout."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "lending_mcp"],
        env={**os.environ, "LENDING_MCP_HOME": str(env["home"]),
             "LENDING_MCP_ALLOWED_DIRS": str(env["allowed"])},
    )

    async def main():
        async with Client(params) as c:
            tools = (await c.list_tools()).tools
            err = await c.call_tool("get_bureau_summary", {"report_id": "nope"})
            return {t.name for t in tools}, err

    names, err = run(main)
    assert names == TOOLS
    assert err.is_error and "load_bureau_report first" in err.content[0].text


def test_bank_tools_end_to_end(env, monkeypatch):  # noqa: F811
    from tests.fixtures_bank import ACCOUNT
    from tests.test_loader import FakeBank, _bank_files

    monkeypatch.setattr(loader, "BankEngine", FakeBank)
    paths = _bank_files(env, "jan.pdf")

    async def main():
        async with Client(mcp) as c:
            loaded = payload(await c.call_tool("load_bank_statement", {"paths": paths}))
            did = loaded["document_id"]
            summary = payload(await c.call_tool("get_bank_summary", {"document_id": did}))
            found = payload(await c.call_tool("find_bank_transactions",
                                              {"document_id": did, "direction": "debit", "min_amount": 8000}))
            wrong = await c.call_tool("get_bureau_summary", {"report_id": did})
            bad = await c.call_tool("find_bank_transactions", {"document_id": did, "limit": 500})
            return loaded, summary, found, wrong, bad

    loaded, summary, found, wrong, bad = run(main)
    assert loaded["document_type"] == "bank" and loaded["statement"]["account_number"] == "**********6789"
    assert loaded["transactions"] == 8 and loaded["rows_verified"] == "8 of 8"
    assert summary["averages"]["months_used"] == ["2025-01", "2025-02"]
    assert found["total_matches"] == 3 and found["totals"]["debit_sum"] == 26000.0
    assert ACCOUNT not in json.dumps([loaded, summary, found])
    assert wrong.is_error and "is a bank document, not bureau" in wrong.content[0].text
    assert bad.is_error


def test_itr_tools_end_to_end(env, monkeypatch):  # noqa: F811
    from tests.fixtures_itr import PAN
    from tests.test_loader import FakeItr, _bank_files

    monkeypatch.setattr(loader, "ItrEngine", FakeItr)
    paths = _bank_files(env, "fy23.pdf", "fy24.pdf")

    async def main():
        async with Client(mcp) as c:
            loaded = payload(await c.call_tool("load_itr", {"paths": paths}))
            fin = payload(await c.call_tool("get_itr_financials", {"document_id": loaded["document_id"]}))
            wrong = await c.call_tool("get_bank_summary", {"document_id": loaded["document_id"]})
            return loaded, fin, wrong

    loaded, fin, wrong = run(main)
    assert loaded["document_type"] == "itr" and [y["year"] for y in loaded["years"]] == [2023, 2024]
    assert fin["years"][1]["ratios_withheld"]["Interest Coverage"].startswith("needs the profit and loss")
    assert PAN not in json.dumps([loaded, fin])
    assert wrong.is_error and "is an itr document, not bank" in wrong.content[0].text


def test_gst_tools_end_to_end(env, monkeypatch):  # noqa: F811
    from tests.fixtures_gst import GSTIN_1, GSTIN_2, PAN
    from tests.test_loader import FakeGst, _bank_files

    monkeypatch.setattr(loader, "GstEngine", FakeGst)
    paths = _bank_files(env, "3b_apr.pdf")

    async def main():
        async with Client(mcp) as c:
            loaded = payload(await c.call_tool("load_gst_returns", {"paths": paths}))
            summary = payload(await c.call_tool("get_gst_summary", {"document_id": loaded["document_id"]}))
            return loaded, summary

    loaded, summary = run(main)
    assert loaded["document_type"] == "gst" and [e["periods_found"] for e in loaded["entities"]] == [2, 2]
    assert summary["entities"][1]["entity_id"] == "gstin-2"
    text = json.dumps([loaded, summary])
    assert all(full not in text for full in (GSTIN_1, GSTIN_2, PAN))


def test_twelve_tools():
    assert len(TOOLS) == 12

    async def main():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in run(main)}
    assert set(tools) == TOOLS
    assert tools["export_excel"].annotations.destructive_hint is False


def test_export_excel_end_to_end(env, monkeypatch):  # noqa: F811
    from openpyxl import load_workbook

    from tests.test_loader import FakeBank, FakeGst, _bank_files

    monkeypatch.setattr(loader, "BankEngine", FakeBank)
    monkeypatch.setattr(loader, "GstEngine", FakeGst)
    bank_paths, gst_paths = _bank_files(env, "jan.pdf"), _bank_files(env, "3b.pdf")

    async def main():
        async with Client(mcp) as c:
            b = payload(await c.call_tool("load_bank_statement", {"paths": bank_paths}))["document_id"]
            g = payload(await c.call_tool("load_gst_returns", {"paths": gst_paths}))["document_id"]
            out = payload(await c.call_tool("export_excel", {"document_ids": [b, g]}))
            empty = await c.call_tool("export_excel", {"document_ids": []})
            unknown = await c.call_tool("export_excel", {"document_ids": ["bank-nope-00000000"]})
            return out, empty, unknown

    out, empty, unknown = run(main)
    assert load_workbook(out["workbook"]).sheetnames == ["Bank", "GST"]
    assert "does not establish that they belong to the same borrower" in out["note"]
    assert empty.is_error and unknown.is_error and "bank-nope-00000000" in unknown.content[0].text


def test_instructions_cover_every_document_type_and_slow_reads():
    from lending_mcp.server import INSTRUCTIONS

    for name in ("load_bureau_report", "load_bank_statement", "load_itr", "get_itr_financials",
                 "load_gst_returns", "get_gst_summary", "export_excel"):
        assert name in INSTRUCTIONS, name
    assert '"reading"' in INSTRUCTIONS and "DSCR" in INSTRUCTIONS
