"""
Loading a bureau report: file checks, AutoCAM's parse, the workbook, the cache.

A report is parsed once. Its result is stored as JSON under
`<home>/reports/<report_id>.json`, and AutoCAM's own CAM workbook under
`<home>/workbooks/<report_id>.xlsx`. report_id is the file's SHA-256 plus a
hash of AutoCAM's engine source, so the same file loads from the cache, and
any change to AutoCAM (committed or not) parses it afresh.

Vision is never used: no API key is passed to AutoCAM. Logs carry no
extracted values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from lending_mcp.bureau import PROVIDER_NAMES
from lending_mcp.config import Settings

logger = logging.getLogger(__name__)

MAX_BYTES = 200 * 1024 * 1024
EXTENSIONS = {".pdf", ".html", ".htm"}
_PDF_MAGIC_WINDOW = 1024
# AutoCAM's engine modules; the Streamlit app and the tests are left out of the hash.
_ENGINE_FILES = (
    "parser.py", "crif_parser.py", "crif_commercial_parser.py", "tu_parser.py",
    "ocr_extractor.py", "html_extractor.py", "excel_generator.py",
)
# The packages that do AutoCAM's reading: a new version can read a report differently.
_AUTOCAM_PACKAGES = ["pymupdf", "openpyxl", "pytesseract", "pillow"]

# Raised whenever the normalised shape this server stores changes, so old cache entries
# are parsed afresh instead of being read in a shape the query code no longer expects.
NORMALISE_VERSION = 1
# Document kinds other than bureau; their ids start with the kind. A bureau id has no prefix.
KINDS = ("bank", "itr", "gst")

_memory: Dict[str, dict] = {}


class LoadError(ValueError):
    """A report that cannot be loaded; the message says why and what to do."""


# --- the file ------------------------------------------------------------------------


def checked_path(raw: str, settings: Settings) -> Path:
    if not settings.allowed_dirs:
        _inside_allowed(Path(raw), settings)  # raises: nothing is allowed yet
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise LoadError(f"File not found: {raw}")
    _inside_allowed(path, settings)
    if not path.is_file():
        raise LoadError(f"Not a file: {raw}")
    if path.suffix.lower() not in EXTENSIONS:
        raise LoadError("Only PDF or HTML bureau reports can be loaded (.pdf, .html, .htm).")
    if path.stat().st_size > MAX_BYTES:
        raise LoadError("The file is larger than 200 MB.")
    return path


def checked_paths(raws: List[str], settings: Settings, extensions: Set[str],
                  allow_folders: bool = False) -> List[Path]:
    """Every path checked as checked_path does. A folder (only when allow_folders) stands for
    every file with one of `extensions` found anywhere inside it, each checked in turn, so a
    link inside an allowed folder cannot reach outside it. Sorted, without repeats."""
    if not raws:
        raise LoadError("Give at least one file path.")
    found: List[Path] = []
    for raw in raws:
        if allow_folders:
            try:
                folder = Path(raw).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                folder = None
            if folder is not None and folder.is_dir():
                _inside_allowed(folder, settings)
                files = [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
                if not files:
                    raise LoadError(f"No PDF found in {raw}.")
                found += [_checked(str(f), settings, extensions) for f in files]
                continue
        found.append(_checked(raw, settings, extensions))
    return sorted(set(found), key=lambda p: (p.name.lower(), str(p)))


def _inside_allowed(path: Path, settings: Settings) -> None:
    if not settings.allowed_dirs:
        raise LoadError(
            "No folder is allowed yet. Add allowed_dirs to "
            f"{settings.home / 'config.toml'} (or set LENDING_MCP_ALLOWED_DIRS) and restart."
        )
    if not any(path.is_relative_to(root) for root in settings.allowed_dirs):
        raise LoadError(
            "This file is outside the allowed folders: "
            + ", ".join(str(d) for d in settings.allowed_dirs)
        )


def _checked(raw: str, settings: Settings, extensions: Set[str]) -> Path:
    path = checked_path(raw, settings)
    if path.suffix.lower() not in extensions:
        raise LoadError("Only " + ", ".join(sorted(extensions)) + " files can be loaded here.")
    return path


def _read(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise LoadError("The file is larger than 200 MB.")
    if path.suffix.lower() == ".pdf" and b"%PDF-" not in data[:_PDF_MAGIC_WINDOW]:
        raise LoadError("The file has a .pdf name but is not a PDF.")
    return data


# --- AutoCAM -------------------------------------------------------------------------


def _autocam_dir(settings: Settings) -> Path:
    if settings.autocam_dir is None or not (settings.autocam_dir / "parser.py").is_file():
        raise LoadError(
            "AutoCAM is not configured. Set autocam_dir in "
            f"{settings.home / 'config.toml'} (or LENDING_MCP_AUTOCAM_DIR) to the AutoCAM folder."
        )
    return settings.autocam_dir


def engine_hash(autocam_dir: Path) -> str:
    return fingerprint([autocam_dir / name for name in _ENGINE_FILES], _AUTOCAM_PACKAGES)


# --- what a cached reading depends on --------------------------------------------------


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "absent"


def fingerprint(engine_files: Iterable[Path], packages: Iterable[str], extra: Iterable[Path] = ()) -> str:
    """A hash of everything that can change how a document is read: the engine's source
    files (committed or not), the installed versions of the packages that do the reading,
    extra inputs such as a policy file, and this server's NORMALISE_VERSION."""
    h = hashlib.sha256()
    for f in list(engine_files) + list(extra):
        f = Path(f)
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    for name in packages:
        h.update(f"{name}=={_package_version(name)}".encode())
    h.update(f"normalise={NORMALISE_VERSION}".encode())
    return h.hexdigest()


def package_dir(pkg: str) -> Path:
    """An installed engine's folder, found without importing it."""
    spec = importlib.util.find_spec(pkg)
    if spec is None or spec.origin is None:
        raise LoadError(f"The {pkg} engine is not installed in this environment. Run uv sync.")
    return Path(spec.origin).resolve().parent


def engine_files(pkg: str) -> List[Path]:
    return sorted(package_dir(pkg).rglob("*.py"))


def document_id(kind: str, datas: List[bytes], fp: str) -> str:
    files = "".join(sorted(hashlib.sha256(d).hexdigest() for d in datas))
    return f"{kind}-{hashlib.sha256(files.encode()).hexdigest()[:16]}-{fp[:8]}"


def kind_of(document_id: str) -> str:
    prefix = document_id.split("-", 1)[0]
    return prefix if prefix in KINDS else "bureau"


def to_jsonable(obj: Any) -> Any:
    """An engine's result in a form JSON can store: dataclasses as dicts, dates as ISO text,
    (year, month) keys as "YYYY-MM", sets and tuples as lists, Decimal as float. Bytes
    (an embedded file, say) are dropped to None: nothing here needs them."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, tuple) and len(k) == 2 and all(isinstance(x, int) for x in k):
                key = f"{k[0]:04d}-{k[1]:02d}"
            elif isinstance(k, str):
                key = k
            else:
                key = str(k)
            out[key] = to_jsonable(v)
        return out
    if isinstance(obj, set):
        return sorted((to_jsonable(v) for v in obj), key=str)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return None
    return obj


def _autocam(autocam_dir: Path):
    """AutoCAM's parser and Excel generator, imported from its folder the way
    FinSight's own adapter does (it is not an installable package yet)."""
    folder = str(autocam_dir)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module("parser"), importlib.import_module("excel_generator")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- loading and lookup ----------------------------------------------------------------


def load(raw_path: str, settings: Settings, parse=None, make_workbook=None) -> dict:
    """The stored report for this file, parsing it only if no cached parse
    exists for this file and this AutoCAM version. parse and make_workbook
    replace AutoCAM's functions in tests."""
    path = checked_path(raw_path, settings)
    data = _read(path)
    autocam_dir = _autocam_dir(settings)
    report_id = f"{hashlib.sha256(data).hexdigest()[:16]}-{engine_hash(autocam_dir)[:8]}"

    cached = get(report_id, settings)
    if cached is not None:
        logger.info("Report %s loaded from cache", report_id)
        return {**cached, "from_cache": True}

    if parse is None or make_workbook is None:
        autocam_parser, autocam_excel = _autocam(autocam_dir)
        parse = parse or (lambda p: autocam_parser.parse(p, api_key=None))
        make_workbook = make_workbook or autocam_excel.generate_excel

    logger.info("Parsing report %s", report_id)
    try:
        parsed = parse(str(path))
    except Exception as exc:
        # The exception type only: AutoCAM's messages can quote report text.
        logger.warning("AutoCAM failed on report %s: %s", report_id, type(exc).__name__)
        raise LoadError(f"AutoCAM could not read this report ({type(exc).__name__}).") from exc

    provider = parsed.get("provider")
    if provider not in PROVIDER_NAMES:
        raise LoadError(
            f"This report was recognised as {provider!r}. Lending MCP v1 supports CRIF High Mark "
            "retail and Commercial ACE reports only; TransUnion CIBIL is not supported yet."
        )

    workbook = settings.workbooks_dir / f"{report_id}.xlsx"
    _atomic_write(workbook, make_workbook(parsed))
    report = {
        "report_id": report_id,
        "source_file": path.name,
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workbook": str(workbook),
        "parsed": parsed,
    }
    store(report, settings)
    return {**report, "from_cache": False}


def get(report_id: str, settings: Settings) -> Optional[dict]:
    if report_id in _memory:
        return _memory[report_id]
    if not all(c.isalnum() or c == "-" for c in report_id):
        return None
    f = settings.reports_dir / f"{report_id}.json"
    if not f.is_file():
        return None
    report = json.loads(f.read_text(encoding="utf-8"))
    _memory[report_id] = report
    return report


# --- slow reads ---------------------------------------------------------------------------

# How long a load waits for a reading before answering "still reading". MCP clients often
# give up on a tool call after about a minute, and a scanned ITR's first read measured 46
# to 102 seconds on real files.
BACKGROUND_WAIT = 40.0
_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()


def in_background(doc_id: str, read) -> dict:
    """Run read() (which stores and returns the document) in a background thread, waiting
    up to BACKGROUND_WAIT seconds. Finished in time: its result, or its LoadError. Not
    finished: a "reading" answer, and the next call for the same id picks up the same
    reading instead of starting another. A failure is raised on that next call."""
    with _jobs_lock:
        job = _jobs.get(doc_id)
        if job is None:
            job = {"done": threading.Event(), "result": None, "error": None}

            def run():
                try:
                    job["result"] = read()
                except BaseException as exc:  # handed to the caller, never lost in the thread
                    job["error"] = exc
                finally:
                    job["done"].set()

            job["thread"] = threading.Thread(target=run, name=f"read-{doc_id}", daemon=True)
            _jobs[doc_id] = job
            job["thread"].start()

    if not job["done"].wait(BACKGROUND_WAIT):
        return {
            "status": "reading",
            "document_id": doc_id,
            "message": ("This document is still being read (a scanned file can take a few minutes on its first "
                        "read). Ask again in a minute by calling the same load tool; nothing is read twice."),
        }
    with _jobs_lock:
        _jobs.pop(doc_id, None)
    if job["error"] is not None:
        raise job["error"]
    return job["result"]


_LOAD_TOOL = {
    "bureau": "load_bureau_report", "bank": "load_bank_statement", "itr": "load_itr", "gst": "load_gst_returns",
}


def require(report_id: str, settings: Settings, kind: Optional[str] = None) -> dict:
    """The stored document with this id. With `kind`, the id must be of that kind."""
    actual = kind_of(report_id)
    if kind is not None and actual != kind:
        article = "an" if actual == "itr" else "a"
        raise LoadError(f"{report_id!r} is {article} {actual} document, not {kind}.")
    report = get(report_id, settings)
    if report is None and report_id in _jobs:
        raise LoadError(f"{report_id!r} is still being read. Call {_LOAD_TOOL[actual]} again in a minute.")
    if report is None:
        what = "report" if actual == "bureau" else "document"
        raise LoadError(f"No loaded {what} with id {report_id!r}. Call {_LOAD_TOOL[actual]} first.")
    return report


def store(doc: dict, settings: Settings) -> None:
    """Keep a normalised, unmasked document: as JSON under reports/, and in memory."""
    doc_id = doc.get("document_id") or doc["report_id"]
    _atomic_write(settings.reports_dir / f"{doc_id}.json", json.dumps(doc).encode("utf-8"))
    _memory[doc_id] = doc


# --- bank statements ---------------------------------------------------------------------

_BANK_PACKAGES = ["pymupdf", "openpyxl", "pytesseract", "pillow", "rapidocr-onnxruntime"]


class BankEngine:
    """Bank Statement Analyser, called the way this server needs it: Vision never runs."""

    def analyze(self, data: bytes, password: Optional[str]):
        import bsa_engine.statement as statement

        return statement.analyze(data, password=password, use_vlm_refinement=False)

    def analyze_multiple(self, datas: List[bytes], passwords: List[Optional[str]]):
        from bsa_engine.multi_statement import analyze_multiple

        return analyze_multiple(datas, passwords)

    def emi_series(self, result) -> list:
        from bsa_engine.signals.loans import emi_series

        return emi_series(result.transactions)

    def complete(self, year: int, month: int, first: date, last: date) -> bool:
        from bsa_engine.signals.scoring import MONTH_EDGE_TOLERANCE_DAYS, _complete

        return _complete(year, month, first, last, MONTH_EDGE_TOLERANCE_DAYS)


def _engine_month_complete(result: dict, engine) -> Dict[str, bool]:
    """The engine's own edge test for each month: do the transactions reach within its
    tolerance of the month's first and last day."""
    dates = sorted(
        date.fromisoformat(t["date"][:10]) for t in result.get("transactions") or []
        if t.get("date") and t.get("status") != "OPENING"
    )
    if not dates:
        return {}
    first, last = dates[0], dates[-1]
    return {k: bool(engine.complete(int(k[:4]), int(k[5:7]), first, last)) for k in (result.get("monthly") or {})}


def load_bank(paths: List[str], settings: Settings, password: Optional[str] = None, engine=None) -> dict:
    """One statement, or several statements of the same account merged by the engine."""
    from lending_mcp import bank

    files = checked_paths(paths, settings, {".pdf"})
    datas = [_read(f) for f in files]
    doc_id = document_id("bank", datas, fingerprint(engine_files("bsa_engine"), _BANK_PACKAGES))

    cached = get(doc_id, settings)
    if cached is not None:
        logger.info("Bank statement %s loaded from cache", doc_id)
        return {**cached, "from_cache": True}

    from bsa_engine.multi_statement import MismatchedAccountError
    from bsa_engine.parser import LockedPDFError

    engine = engine or BankEngine()
    logger.info("Reading bank statement %s (%d file(s))", doc_id, len(datas))
    try:
        if len(datas) == 1:
            raw, merge = engine.analyze(datas[0], password), None
        else:
            multi = engine.analyze_multiple(datas, [password] * len(datas))
            raw = multi.result
            merge = {"files_merged": multi.files_merged, "duplicates_removed": multi.duplicates_removed,
                     "merge_conflicts": to_jsonable(multi.merge_conflicts)}
        emi = to_jsonable(engine.emi_series(raw))
    except LockedPDFError as exc:
        raise LoadError("The PDF is password protected: pass password (or the right one).") from exc
    except MismatchedAccountError as exc:
        raise LoadError("These statements are not of one account; load them separately.") from exc
    except Exception as exc:
        # The exception type only: the engine's messages can quote statement text.
        logger.warning("Bank Statement Analyser failed on %s: %s", doc_id, type(exc).__name__)
        raise LoadError(f"Bank Statement Analyser could not read this statement ({type(exc).__name__}).") from exc

    result = to_jsonable(raw)
    doc = bank.normalise(result, emi, merge, _engine_month_complete(result, engine))
    doc.update({
        "document_id": doc_id,
        "source_files": [f.name for f in files],
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    store(doc, settings)
    return {**doc, "from_cache": False}


# --- ITR ---------------------------------------------------------------------------------

_ITR_PACKAGES = ["pymupdf", "openpyxl", "pytesseract", "pillow", "rapidocr", "onnxruntime"]


class ItrEngine:
    """ITR Extractor, called the way this server needs it: no Gemini, no Vision."""

    def spread(self, paths: List[str]) -> list:
        import itr_engine.parser as parser

        return parser.spread(paths, api_key=None, use_vision=False)

    def flags(self, columns: list) -> list:
        from itr_engine import analysis

        return analysis.flags(columns)

    def summary(self, columns: list) -> dict:
        from itr_engine import analysis

        return analysis.summary(columns)

    def attention(self, columns: list) -> list:
        from itr_engine import explain

        return explain.all_attention(columns)

    def workbook(self, columns: list) -> bytes:
        from itr_engine.excel_generator import generate_excel

        return generate_excel(columns)

    def formulas(self) -> Dict[str, str]:
        from itr_engine.mapping.template_config import RATIOS

        return {name: formula for name, formula, _fmt in RATIOS}

    def labels(self) -> Dict[str, str]:
        from itr_engine.mapping import taxonomy

        return dict(taxonomy.LABELS)


def load_itr(paths: List[str], settings: Settings, engine=None) -> dict:
    """An ITR bundle, one PDF per financial year, read into year columns. A scanned bundle
    can take minutes on its first read, so the reading runs in the background when slow
    (see in_background)."""
    files = checked_paths(paths, settings, {".pdf"})
    datas = [_read(f) for f in files]
    label_map = package_dir("itr_engine").parent / "data" / "label_map.json"
    doc_id = document_id("itr", datas, fingerprint(engine_files("itr_engine"), _ITR_PACKAGES, [label_map]))

    cached = get(doc_id, settings)
    if cached is not None:
        with _jobs_lock:
            _jobs.pop(doc_id, None)  # a background reading that has finished
        logger.info("ITR %s loaded from cache", doc_id)
        return {**cached, "from_cache": True}

    engine = engine or ItrEngine()

    def read() -> dict:
        return _read_itr(doc_id, files, datas, settings, engine)

    return in_background(doc_id, read)


def _read_itr(doc_id: str, files: List[Path], datas: List[bytes], settings: Settings, engine) -> dict:
    import time

    from lending_mcp import itr

    logger.info("Reading ITR %s (%d file(s))", doc_id, len(datas))
    started = time.perf_counter()
    try:
        columns = engine.spread([str(f) for f in files])
        if not columns:
            raise LoadError("No financial year could be read from these files.")
        flags, summary, attention = engine.flags(columns), engine.summary(columns), engine.attention(columns)
        workbook_bytes = engine.workbook(columns)
    except LoadError:
        raise
    except Exception as exc:
        logger.warning("ITR Extractor failed on %s: %s", doc_id, type(exc).__name__)
        raise LoadError(f"ITR Extractor could not read these files ({type(exc).__name__}).") from exc
    seconds = round(time.perf_counter() - started, 1)

    workbook = settings.workbooks_dir / f"{doc_id}.xlsx"
    _atomic_write(workbook, workbook_bytes)
    doc = itr.normalise(to_jsonable(columns), to_jsonable(flags), to_jsonable(summary), to_jsonable(attention),
                        engine.formulas(), engine.labels())
    doc.update({
        "document_id": doc_id,
        "source_files": [f.name for f in files],
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "load_seconds": float(seconds),
        "workbook": str(workbook),
    })
    store(doc, settings)
    return {**doc, "from_cache": False}


# --- GST -----------------------------------------------------------------------------------

_GST_PACKAGES = ["pymupdf", "openpyxl", "pyyaml", "pytesseract", "rapidocr", "onnxruntime"]


class GstEngine:
    """GST Analyser: GSTR-3B returns in, its JSON ready report out. It has no AI path."""

    def analyse(self, paths: List[str]) -> dict:
        from gst_engine import analyse

        return analyse(paths).report


def load_gst(paths: List[str], settings: Settings, engine=None) -> dict:
    """GSTR-3B returns, given as files or folders. Every file found in a folder must itself be
    inside the allowed folders. The engine recognises other documents and lists them as
    skipped. A scanned return is read by OCR, so a slow reading goes on in the background."""
    from gst_engine.pipeline import DEFAULT_POLICY

    files = checked_paths(paths, settings, {".pdf"}, allow_folders=True)
    datas = [_read(f) for f in files]
    doc_id = document_id("gst", datas, fingerprint(engine_files("gst_engine"), _GST_PACKAGES, [DEFAULT_POLICY]))

    cached = get(doc_id, settings)
    if cached is not None:
        with _jobs_lock:
            _jobs.pop(doc_id, None)
        logger.info("GST returns %s loaded from cache", doc_id)
        return {**cached, "from_cache": True}

    engine = engine or GstEngine()

    def read() -> dict:
        from lending_mcp import gst

        logger.info("Reading GST returns %s (%d file(s))", doc_id, len(datas))
        try:
            report = to_jsonable(engine.analyse([str(f) for f in files]))
        except Exception as exc:
            logger.warning("GST Analyser failed on %s: %s", doc_id, type(exc).__name__)
            raise LoadError(f"GST Analyser could not read these files ({type(exc).__name__}).") from exc
        if not any(f.get("doc_type") == "GSTR3B" for f in report.get("files") or []):
            raise LoadError("No GSTR-3B return found in these files.")
        doc = gst.normalise(report)
        doc.update({
            "document_id": doc_id,
            "source_files": [f.name for f in files],
            "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        store(doc, settings)
        return {**doc, "from_cache": False}

    return in_background(doc_id, read)
