"""Inspect the local environment without installing or changing anything."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    packages = [
        "numpy",
        "pandas",
        "yaml",
        "pyarrow",
        "baostock",
        "tushare",
        "dotenv",
        "duckdb",
        "matplotlib",
        "seaborn",
        "pytest",
        "ruff",
        "scipy",
        "sklearn",
        "lightgbm",
        "torch",
    ]
    writable_probe = ROOT / "data" / ".write_probe"
    writable = False
    try:
        writable_probe.write_text("ok", encoding="utf-8")
        writable = True
    finally:
        writable_probe.unlink(missing_ok=True)
    quant_lab_spec = importlib.util.find_spec("quant_lab")
    quant_lab_origin = (
        Path(quant_lab_spec.origin).resolve()
        if quant_lab_spec is not None and quant_lab_spec.origin is not None
        else None
    )
    expected_source = (ROOT / "src").resolve()
    editable_install_matches_project = bool(
        quant_lab_origin is not None
        and quant_lab_origin.is_relative_to(expected_source)
    )
    report = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "project_root": str(ROOT),
        "project_path_contains_non_ascii": not str(ROOT).isascii(),
        "project_writable": writable,
        "quant_lab_origin": str(quant_lab_origin) if quant_lab_origin else None,
        "editable_install_matches_project": editable_install_matches_project,
        "tushare_token_present": bool(os.getenv("TUSHARE_TOKEN")),
        "packages": {
            package: importlib.util.find_spec(package) is not None
            for package in packages
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (
        sys.version_info < (3, 10)
        or not writable
        or not editable_install_matches_project
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
