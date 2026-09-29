from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe_wrds_csmar.py"
SPEC = importlib.util.spec_from_file_location("probe_wrds_csmar", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_candidate_libraries_filters_case_insensitively() -> None:
    libraries = ["comp", "CSMAR", "csmar_financial", "china_stock_demo"]

    assert MODULE._candidate_libraries(libraries) == [
        "CSMAR",
        "china_stock_demo",
        "csmar_financial",
    ]
