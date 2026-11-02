"""The three installed engines coexist with AutoCAM: each resolves inside its own repo,
finds its own resources, and exposes the entry points this server relies on."""

import importlib
import sys
from pathlib import Path

import pytest

PROJECTS = Path(__file__).resolve().parents[2]
REPOS = {"bsa_engine": "Bank Statemnt Analyser", "itr_engine": "ITR Extractor", "gst_engine": "GST Analyser"}


@pytest.mark.parametrize("pkg,repo", REPOS.items())
def test_engine_resolves_inside_its_own_repo(pkg, repo):
    mod = importlib.import_module(pkg)
    assert Path(mod.__file__).resolve().is_relative_to((PROJECTS / repo / "engine").resolve())


def test_no_bare_engine_module_loaded():
    for pkg in REPOS:
        importlib.import_module(pkg)
    assert "engine" not in sys.modules


def test_engine_resources_resolve():
    import gst_engine.pipeline
    import itr_engine.cache

    assert gst_engine.pipeline.DEFAULT_POLICY.is_file()
    assert Path(itr_engine.cache.CACHE_DIR).resolve().is_relative_to((PROJECTS / "ITR Extractor").resolve())


def test_engine_entry_points_this_build_relies_on_exist():
    from bsa_engine.excel_generator import generate_excel as _bank_excel  # noqa: F401
    from bsa_engine.multi_statement import MismatchedAccountError, analyze_multiple  # noqa: F401
    from bsa_engine.parser import LockedPDFError  # noqa: F401
    from bsa_engine.signals.loans import emi_series  # noqa: F401
    from bsa_engine.signals.scoring import MONTH_EDGE_TOLERANCE_DAYS, _complete  # noqa: F401
    from bsa_engine.statement import analyze  # noqa: F401
    from gst_engine import analyse, generate_excel as _gst_excel  # noqa: F401
    from itr_engine import analysis, explain  # noqa: F401
    from itr_engine.excel_generator import generate_excel as _itr_excel  # noqa: F401
    from itr_engine.mapping.template_config import RATIOS, SNAP_RATIO_NAMES  # noqa: F401
    from itr_engine.parser import spread  # noqa: F401

    assert MONTH_EDGE_TOLERANCE_DAYS == 3
