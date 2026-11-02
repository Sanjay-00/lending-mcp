import pytest

from lending_mcp import loader
from lending_mcp.config import load_settings
from lending_mcp.loader import LoadError
from tests.fixtures import COMMERCIAL, RETAIL, TRANSUNION


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A home, an allowed folder with a PDF, an outside folder, and a fake
    AutoCAM folder (only its files' contents matter, for the engine hash)."""
    home, allowed, outside, autocam = (tmp_path / n for n in ("home", "allowed", "outside", "autocam"))
    for d in (home, allowed, outside, autocam):
        d.mkdir()
    (autocam / "parser.py").write_text("# engine v1")
    (autocam / "excel_generator.py").write_text("# excel v1")
    (allowed / "report.pdf").write_bytes(b"%PDF-1.7\nfake content")
    (outside / "report.pdf").write_bytes(b"%PDF-1.7\nfake content")
    monkeypatch.setenv("LENDING_MCP_HOME", str(home))
    monkeypatch.setenv("LENDING_MCP_ALLOWED_DIRS", str(allowed))
    monkeypatch.setenv("LENDING_MCP_AUTOCAM_DIR", str(autocam))
    monkeypatch.delenv("LENDING_MCP_UNMASKED", raising=False)
    loader._memory.clear()
    loader._jobs.clear()
    return {"home": home, "allowed": allowed, "outside": outside, "autocam": autocam}


class FakeEngine:
    def __init__(self, parsed):
        self.parsed, self.calls = parsed, 0

    def parse(self, path):
        self.calls += 1
        return dict(self.parsed)

    @staticmethod
    def workbook(parsed):
        return b"xlsx bytes"


def _load(env, parsed=RETAIL, name="report.pdf"):
    engine = FakeEngine(parsed)
    report = loader.load(str(env["allowed"] / name), load_settings(), parse=engine.parse, make_workbook=engine.workbook)
    return report, engine


# --- configuration ----------------------------------------------------------------------


def test_config_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("LENDING_MCP_HOME", str(tmp_path))
    for var in ("LENDING_MCP_ALLOWED_DIRS", "LENDING_MCP_AUTOCAM_DIR", "LENDING_MCP_UNMASKED"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config.toml").write_text(f'allowed_dirs = ["{tmp_path.as_posix()}"]\nautocam_dir = "{tmp_path.as_posix()}"\n')
    s = load_settings()
    assert s.allowed_dirs == [tmp_path.resolve()] and s.autocam_dir == tmp_path.resolve() and not s.unmasked


def test_environment_overrides_file(env, monkeypatch):
    (env["home"] / "config.toml").write_text('allowed_dirs = ["C:/somewhere/else"]\n')
    monkeypatch.setenv("LENDING_MCP_UNMASKED", "1")
    s = load_settings()
    assert s.allowed_dirs == [env["allowed"].resolve()] and s.unmasked


# --- file checks ------------------------------------------------------------------------


def test_nothing_loads_without_an_allowed_folder(env, monkeypatch):
    monkeypatch.delenv("LENDING_MCP_ALLOWED_DIRS")
    with pytest.raises(LoadError, match="No folder is allowed"):
        _load(env)


def test_outside_the_allowed_folders_is_refused(env):
    engine = FakeEngine(RETAIL)
    with pytest.raises(LoadError, match="outside the allowed folders"):
        loader.load(str(env["outside"] / "report.pdf"), load_settings(), engine.parse, engine.workbook)


def test_dot_dot_escape_is_refused(env):
    sneaky = env["allowed"] / ".." / "outside" / "report.pdf"
    with pytest.raises(LoadError, match="outside the allowed folders"):
        loader.load(str(sneaky), load_settings(), FakeEngine(RETAIL).parse, FakeEngine.workbook)


def test_missing_file(env):
    with pytest.raises(LoadError, match="not found"):
        _load(env, name="nope.pdf")


def test_wrong_extension(env):
    (env["allowed"] / "report.docx").write_bytes(b"%PDF-1.7")
    with pytest.raises(LoadError, match="Only PDF or HTML"):
        _load(env, name="report.docx")


def test_fake_pdf(env):
    (env["allowed"] / "fake.pdf").write_bytes(b"MZ not a pdf at all")
    with pytest.raises(LoadError, match="not a PDF"):
        _load(env, name="fake.pdf")


def test_html_needs_no_pdf_magic(env):
    (env["allowed"] / "report.html").write_text("<html>report</html>")
    report, _ = _load(env, name="report.html")
    assert report["source_file"] == "report.html"


def test_oversized_file(env, monkeypatch):
    monkeypatch.setattr(loader, "MAX_BYTES", 10)
    with pytest.raises(LoadError, match="larger than 200 MB"):
        _load(env)


def test_autocam_not_configured(env, monkeypatch):
    monkeypatch.delenv("LENDING_MCP_AUTOCAM_DIR")
    with pytest.raises(LoadError, match="AutoCAM is not configured"):
        _load(env)


# --- parsing, cache, workbook -------------------------------------------------------------


def test_load_parses_writes_workbook_and_cache(env):
    report, engine = _load(env)
    assert engine.calls == 1 and report["from_cache"] is False
    assert report["parsed"]["provider"] == "crif"
    assert (env["home"] / "workbooks" / f"{report['report_id']}.xlsx").read_bytes() == b"xlsx bytes"
    assert (env["home"] / "reports" / f"{report['report_id']}.json").is_file()


def test_second_load_is_a_cache_hit(env):
    first, _ = _load(env)
    second, engine = _load(env)
    assert engine.calls == 0 and second["from_cache"] is True and second["report_id"] == first["report_id"]


def test_cache_survives_a_restart(env):
    first, _ = _load(env)
    loader._memory.clear()
    second, engine = _load(env)
    assert engine.calls == 0 and second["from_cache"] is True
    assert loader.require(first["report_id"], load_settings())["parsed"]["name"] == RETAIL["name"]


def test_engine_change_invalidates_the_cache(env):
    first, _ = _load(env)
    (env["autocam"] / "parser.py").write_text("# engine v2, an uncommitted edit")
    second, engine = _load(env)
    assert engine.calls == 1 and second["report_id"] != first["report_id"]


def test_different_file_gets_a_different_id(env):
    first, _ = _load(env)
    (env["allowed"] / "other.pdf").write_bytes(b"%PDF-1.7\nother")
    second, _ = _load(env, COMMERCIAL, name="other.pdf")
    assert second["report_id"] != first["report_id"]


def test_transunion_is_refused(env):
    with pytest.raises(LoadError, match="TransUnion CIBIL is not supported"):
        _load(env, TRANSUNION)


def test_engine_failure_names_the_type_only(env):
    def boom(path):
        raise RuntimeError("text quoted from the borrower's report")
    with pytest.raises(LoadError, match=r"could not read this report \(RuntimeError\)") as exc:
        loader.load(str(env["allowed"] / "report.pdf"), load_settings(), parse=boom, make_workbook=FakeEngine.workbook)
    assert "borrower" not in str(exc.value)


def test_unknown_report_id(env):
    with pytest.raises(LoadError, match="Call load_bureau_report first"):
        loader.require("does-not-exist", load_settings())
    assert loader.get("../../etc", load_settings()) is None


# --- shared pieces for every document type (v2) ------------------------------------------


def test_checked_paths_expands_folder_and_checks_each_file(env):
    case = env["allowed"] / "case"
    (case / "inner").mkdir(parents=True)
    (case / "a.pdf").write_bytes(b"%PDF-1.7 a")
    (case / "inner" / "b.PDF").write_bytes(b"%PDF-1.7 b")
    (case / "notes.txt").write_text("not a return")
    paths = loader.checked_paths([str(case)], load_settings(), {".pdf"}, allow_folders=True)
    assert [p.name for p in paths] == ["a.pdf", "b.PDF"]


def test_checked_paths_deduplicates_and_sorts(env):
    pdf = str(env["allowed"] / "report.pdf")
    assert len(loader.checked_paths([pdf, pdf], load_settings(), {".pdf"})) == 1


def test_checked_paths_refuses_folder_outside_allowed(env):
    with pytest.raises(LoadError, match="outside the allowed folders"):
        loader.checked_paths([str(env["outside"])], load_settings(), {".pdf"}, allow_folders=True)


def test_checked_paths_refuses_folder_when_not_allowed(env):
    with pytest.raises(LoadError, match="Not a file"):
        loader.checked_paths([str(env["allowed"])], load_settings(), {".pdf"})


def test_checked_paths_folder_without_matching_files(env):
    empty = env["allowed"] / "empty"
    empty.mkdir()
    with pytest.raises(LoadError, match="No PDF found"):
        loader.checked_paths([str(empty)], load_settings(), {".pdf"}, allow_folders=True)


def test_checked_paths_needs_at_least_one_path(env):
    with pytest.raises(LoadError, match="at least one"):
        loader.checked_paths([], load_settings(), {".pdf"})


def _fp(env, **kw):
    return loader.fingerprint([env["autocam"] / "parser.py"], ["openpyxl"], **kw)


def test_fingerprint_changes_with_package_version(env, monkeypatch):
    before = _fp(env)
    monkeypatch.setattr(loader, "_package_version", lambda name: "999.0")
    assert _fp(env) != before


def test_fingerprint_changes_with_extra_file(env):
    extra = env["autocam"] / "policy.yaml"
    extra.write_text("a: 1")
    before = _fp(env, extra=[extra])
    extra.write_text("a: 2")
    assert _fp(env, extra=[extra]) != before


def test_fingerprint_changes_with_normalise_version(env, monkeypatch):
    before = _fp(env)
    monkeypatch.setattr(loader, "NORMALISE_VERSION", loader.NORMALISE_VERSION + 1)
    assert _fp(env) != before


def test_document_id_ignores_file_order():
    a = loader.document_id("bank", [b"one", b"two"], "f" * 64)
    b = loader.document_id("bank", [b"two", b"one"], "f" * 64)
    assert a == b and a.startswith("bank-") and a.endswith("-ffffffff")


def test_to_jsonable_converts_dates_month_tuples_and_dataclasses():
    import dataclasses
    from collections import OrderedDict
    from datetime import date, datetime
    from decimal import Decimal

    @dataclasses.dataclass
    class Row:
        when: date
        amount: Decimal

    out = loader.to_jsonable({
        "rows": [Row(date(2025, 1, 31), Decimal("10.5"))],
        "monthly": OrderedDict({(2025, 1): {"credit": 1.0}}),
        "at": datetime(2025, 1, 1, 9, 30),
        "tags": {"b", "a"},
        "pair": (1, 2),
        "blob": b"\x00",
        3: "int key",
    })
    assert out == {
        "rows": [{"when": "2025-01-31", "amount": 10.5}],
        "monthly": {"2025-01": {"credit": 1.0}},
        "at": "2025-01-01T09:30:00",
        "tags": ["a", "b"],
        "pair": [1, 2],
        "blob": None,
        "3": "int key",
    }


def test_store_get_and_kind(env):
    settings = load_settings()
    loader.store({"document_id": "bank-abc-123", "x": 1}, settings)
    loader._memory.clear()
    assert loader.require("bank-abc-123", settings, kind="bank")["x"] == 1
    assert loader.kind_of("bank-abc-123") == "bank"
    assert loader.kind_of("gst-abc-123") == "gst"
    assert loader.kind_of("0123abcd-89abcdef") == "bureau"


def test_require_refuses_kind_mismatch(env):
    settings = load_settings()
    loader.store({"document_id": "bank-abc-123"}, settings)
    with pytest.raises(LoadError, match="is a bank document, not itr"):
        loader.require("bank-abc-123", settings, kind="itr")


def test_bureau_id_changes_with_package_version(env, monkeypatch):
    first, _ = _load(env)
    monkeypatch.setattr(loader, "_package_version", lambda name: "999.0")
    second, engine = _load(env)
    assert engine.calls == 1 and second["report_id"] != first["report_id"]


# --- bank statements (v2) ----------------------------------------------------------------

import copy as _copy  # noqa: E402
import types as _types  # noqa: E402

from tests.fixtures_bank import EMI, ENGINE_COMPLETE, STATEMENT  # noqa: E402


class FakeBank:
    """Stands in for bsa_engine: records which entry point ran and with what."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def analyze(self, data, password):
        self.calls.append(("analyze", password))
        if self.error:
            raise self.error
        return _copy.deepcopy(STATEMENT)

    def analyze_multiple(self, datas, passwords):
        self.calls.append(("analyze_multiple", len(datas), passwords))
        if self.error:
            raise self.error
        return _types.SimpleNamespace(result=_copy.deepcopy(STATEMENT), files_merged=len(datas),
                                      duplicates_removed=1, merge_conflicts=[])

    def emi_series(self, result):
        return _copy.deepcopy(EMI)

    def complete(self, year, month, first, last):
        return ENGINE_COMPLETE.get(f"{year:04d}-{month:02d}", False)


def _bank_files(env, *names):
    for n in names:
        (env["allowed"] / n).write_bytes(b"%PDF-1.7 " + n.encode())
    return [str(env["allowed"] / n) for n in names]


def test_load_bank_single_and_multi(env):
    engine = FakeBank()
    one = loader.load_bank(_bank_files(env, "jan.pdf"), load_settings(), engine=engine)
    assert engine.calls == [("analyze", None)]
    assert one["document_id"].startswith("bank-") and one["source_files"] == ["jan.pdf"]
    assert "score" not in one and one["from_cache"] is False
    assert [m["status"] for m in one["months"]] == ["complete", "complete", "partial"]

    engine = FakeBank()
    two = loader.load_bank(_bank_files(env, "jan.pdf", "feb.pdf"), load_settings(), password="pw", engine=engine)
    assert engine.calls == [("analyze_multiple", 2, ["pw", "pw"])]
    assert two["merge"] == {"files_merged": 2, "duplicates_removed": 1, "merge_conflicts": []}
    assert two["document_id"] != one["document_id"]


def test_load_bank_cache_hit(env):
    files = _bank_files(env, "jan.pdf")
    first = loader.load_bank(files, load_settings(), engine=FakeBank())
    loader._memory.clear()
    engine = FakeBank()
    second = loader.load_bank(files, load_settings(), engine=engine)
    assert engine.calls == [] and second["from_cache"] is True and second["document_id"] == first["document_id"]


def test_masking_follows_setting_on_cache_hit(env, monkeypatch):
    from lending_mcp import bank
    from tests.fixtures_bank import ACCOUNT

    files = _bank_files(env, "jan.pdf")
    engine = FakeBank()
    doc = loader.load_bank(files, load_settings(), engine=engine)
    masked = bank.summary(loader.require(doc["document_id"], load_settings(), kind="bank"), load_settings().unmasked)
    monkeypatch.setenv("LENDING_MCP_UNMASKED", "1")
    again = loader.load_bank(files, load_settings(), engine=engine)
    unmasked = bank.summary(loader.require(again["document_id"], load_settings(), kind="bank"), load_settings().unmasked)
    assert len(engine.calls) == 1 and again["from_cache"] is True
    assert masked["statement"]["account_number"] == "**********6789"
    assert unmasked["statement"]["account_number"] == ACCOUNT
    stored = (env["home"] / "reports" / f"{doc['document_id']}.json").read_text()
    assert ACCOUNT in stored  # the cache is unmasked; masking happens per answer


def test_load_bank_locked_pdf_message(env):
    from bsa_engine.parser import LockedPDFError

    with pytest.raises(LoadError, match="password protected: pass password"):
        loader.load_bank(_bank_files(env, "jan.pdf"), load_settings(), engine=FakeBank(LockedPDFError("locked")))


def test_load_bank_mismatched_accounts_message(env):
    from bsa_engine.multi_statement import MismatchedAccountError

    with pytest.raises(LoadError, match="not of one account; load them separately"):
        loader.load_bank(_bank_files(env, "a.pdf", "b.pdf"), load_settings(),
                         engine=FakeBank(MismatchedAccountError("x")))


def test_load_bank_engine_failure_names_the_type_only(env):
    with pytest.raises(LoadError, match=r"could not read this statement \(KeyError\)") as exc:
        loader.load_bank(_bank_files(env, "jan.pdf"), load_settings(), engine=FakeBank(KeyError("HOLDER NAME")))
    assert "HOLDER" not in str(exc.value)


def test_load_bank_refuses_path_outside_allowed(env):
    with pytest.raises(LoadError, match="outside the allowed folders"):
        loader.load_bank([str(env["outside"] / "report.pdf")], load_settings(), engine=FakeBank())


def test_load_bank_refuses_non_pdf(env):
    (env["allowed"] / "s.html").write_text("<html>")
    with pytest.raises(LoadError, match="Only .pdf files"):
        loader.load_bank([str(env["allowed"] / "s.html")], load_settings(), engine=FakeBank())


def test_default_bank_engine_never_uses_vision(env, monkeypatch):
    import bsa_engine.statement

    monkeypatch.setenv("GEMINI_API_KEY", "a-key-that-must-not-be-used")
    seen = {}
    monkeypatch.setattr(bsa_engine.statement, "analyze", lambda data, **kw: seen.update(kw) or "result")
    loader.BankEngine().analyze(b"%PDF", None)
    assert seen["use_vlm_refinement"] is False


# --- ITR (v2) ------------------------------------------------------------------------------

from tests import fixtures_itr as _itr  # noqa: E402


class FakeItr:
    def __init__(self, error=None):
        self.calls, self.error = 0, error

    def spread(self, paths):
        self.calls += 1
        if self.error:
            raise self.error
        return _itr.columns()

    def flags(self, columns):
        return _copy.deepcopy(_itr.FLAGS)

    def summary(self, columns):
        return dict(_itr.SUMMARY)

    def attention(self, columns):
        return _copy.deepcopy(_itr.ATTENTION)

    def workbook(self, columns):
        return b"itr xlsx"

    def formulas(self):
        return dict(_itr.FORMULAS)

    def labels(self):
        return dict(_itr.LABELS)


def test_load_itr_writes_workbook_and_caches(env):
    files = _bank_files(env, "fy23.pdf", "fy24.pdf")
    engine = FakeItr()
    doc = loader.load_itr(files, load_settings(), engine=engine)
    assert doc["document_id"].startswith("itr-") and doc["from_cache"] is False
    assert doc["source_files"] == ["fy23.pdf", "fy24.pdf"] and isinstance(doc["load_seconds"], float)
    assert (env["home"] / "workbooks" / f"{doc['document_id']}.xlsx").read_bytes() == b"itr xlsx"
    assert doc["workbook"].endswith(f"{doc['document_id']}.xlsx")
    loader._memory.clear()
    again = loader.load_itr(files, load_settings(), engine=engine)
    assert engine.calls == 1 and again["from_cache"] is True


def test_load_itr_engine_failure_names_the_type_only(env):
    with pytest.raises(LoadError, match=r"ITR Extractor could not read these files \(ValueError\)"):
        loader.load_itr(_bank_files(env, "fy23.pdf"), load_settings(), engine=FakeItr(ValueError("TEST TRADERS")))


def test_load_itr_no_year_found(env):
    engine = FakeItr()
    engine.spread = lambda paths: []
    with pytest.raises(LoadError, match="No financial year could be read"):
        loader.load_itr(_bank_files(env, "fy23.pdf"), load_settings(), engine=engine)


def test_default_itr_engine_never_uses_gemini(env, monkeypatch):
    import itr_engine.parser

    monkeypatch.setenv("GEMINI_API_KEY", "a-key-that-must-not-be-used")
    seen = {}
    monkeypatch.setattr(itr_engine.parser, "spread", lambda sources, **kw: seen.update(kw) or [])
    loader.ItrEngine().spread(["x.pdf"])
    assert seen["api_key"] is None and seen["use_vision"] is False


# --- slow reads go on in the background (ruling after checkpoint C) -----------------------

import threading as _threading  # noqa: E402


class SlowItr(FakeItr):
    def __init__(self, error=None):
        super().__init__(error)
        self.release = _threading.Event()

    def spread(self, paths):
        self.release.wait(5)
        return super().spread(paths)


def _finish(doc_id):
    loader._jobs[doc_id]["thread"].join(5)


def test_slow_itr_answers_reading_then_the_result(env, monkeypatch):
    monkeypatch.setattr(loader, "BACKGROUND_WAIT", 0.05)
    files = _bank_files(env, "fy23.pdf")
    engine = SlowItr()
    first = loader.load_itr(files, load_settings(), engine=engine)
    assert first["status"] == "reading" and first["document_id"].startswith("itr-")
    assert "ask again" in first["message"].lower()
    with pytest.raises(LoadError, match="still being read"):
        loader.require(first["document_id"], load_settings(), kind="itr")
    again = loader.load_itr(files, load_settings(), engine=engine)
    assert again["status"] == "reading" and engine.calls == 0  # no second reading started
    engine.release.set()
    _finish(first["document_id"])
    done = loader.load_itr(files, load_settings(), engine=engine)
    assert done.get("status") is None and done["document_id"] == first["document_id"]
    assert engine.calls == 1 and done["columns"]


def test_slow_itr_failure_is_reported_on_the_next_call(env, monkeypatch):
    monkeypatch.setattr(loader, "BACKGROUND_WAIT", 0.05)
    files = _bank_files(env, "fy23.pdf")
    engine = SlowItr(ValueError("TEST TRADERS"))
    first = loader.load_itr(files, load_settings(), engine=engine)
    assert first["status"] == "reading"
    engine.release.set()
    _finish(first["document_id"])
    with pytest.raises(LoadError, match=r"could not read these files \(ValueError\)"):
        loader.load_itr(files, load_settings(), engine=engine)
    assert first["document_id"] not in loader._jobs  # a later call may try again


def test_fast_itr_answers_at_once(env):
    doc = loader.load_itr(_bank_files(env, "fy23.pdf"), load_settings(), engine=FakeItr())
    assert doc.get("status") is None and doc["from_cache"] is False


def test_finished_background_job_is_cleared_on_cache_hit(env, monkeypatch):
    monkeypatch.setattr(loader, "BACKGROUND_WAIT", 0.05)
    files = _bank_files(env, "fy23.pdf")
    engine = SlowItr()
    first = loader.load_itr(files, load_settings(), engine=engine)
    engine.release.set()
    _finish(first["document_id"])
    loader.load_itr(files, load_settings(), engine=engine)
    assert first["document_id"] not in loader._jobs


# --- GST (v2) ------------------------------------------------------------------------------

from tests import fixtures_gst as _gst  # noqa: E402


class FakeGst:
    def __init__(self, report=None):
        self.calls, self.report = [], report

    def analyse(self, paths):
        self.calls.append(paths)
        return _copy.deepcopy(self.report) if self.report is not None else _gst.report()


def test_load_gst_folder_inside_allowed(env):
    case = env["allowed"] / "case"
    case.mkdir()
    for n in ("3b_apr.pdf", "3b_may.pdf"):
        (case / n).write_bytes(b"%PDF-1.7 " + n.encode())
    engine = FakeGst()
    doc = loader.load_gst([str(case)], load_settings(), engine=engine)
    assert doc["document_id"].startswith("gst-") and doc["source_files"] == ["3b_apr.pdf", "3b_may.pdf"]
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in engine.calls[0]] == ["3b_apr.pdf", "3b_may.pdf"]
    assert [e["entity_id"] for e in doc["entities"]] == ["gstin-1", "gstin-2"]
    again = loader.load_gst([str(case)], load_settings(), engine=engine)
    assert again["from_cache"] is True and len(engine.calls) == 1


def test_load_gst_folder_outside_refused(env):
    with pytest.raises(LoadError, match="outside the allowed folders"):
        loader.load_gst([str(env["outside"])], load_settings(), engine=FakeGst())


def test_load_gst_no_return_found(env):
    no_returns = _gst.report()
    no_returns["files"] = [f for f in no_returns["files"] if f["doc_type"] != "GSTR3B"]
    with pytest.raises(LoadError, match="No GSTR-3B return found in these files"):
        loader.load_gst(_bank_files(env, "bank.pdf"), load_settings(), engine=FakeGst(no_returns))
