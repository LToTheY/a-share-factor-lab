"""Inspect dependencies and paths; use a unique, disposable write probe."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("core", "dashboard"), default="core")
    args = parser.parse_args()
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
        "streamlit",
        "plotly",
        "wrds",
    ]
    writable = False
    write_error = None
    try:
        (ROOT / "data").mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=ROOT / "data", prefix="doctor-", suffix=".tmp", encoding="utf-8") as probe:
            probe.write("ok")
        writable = True
    except OSError as exc:
        write_error = str(exc)
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
    present = {package: importlib.util.find_spec(package) is not None for package in packages}
    names = {"yaml": "PyYAML", "dotenv": "python-dotenv", "sklearn": "scikit-learn"}
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(names.get(package, package))
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    required = ["numpy", "pandas", "yaml"]
    if args.profile == "dashboard":
        required += ["baostock", "pyarrow", "duckdb", "streamlit", "plotly"]
    missing = [name for name in required if not present[name]]
    compatibility_errors = []
    minimum_versions = {"pandas": ((2, 2), 3)}
    if args.profile == "dashboard":
        minimum_versions["streamlit"] = ((1, 51), 2)
    for name, (minimum, upper_major) in minimum_versions.items():
        match = re.match(r"(\d+)\.(\d+)", versions.get(name) or "")
        if match:
            version = tuple(map(int, match.groups()))
            if version < minimum or version[0] >= upper_major:
                compatibility_errors.append(f"{name} {versions[name]} outside supported >={minimum[0]}.{minimum[1]},<{upper_major}")
    report = {
        "profile": args.profile,
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "project_root": str(ROOT),
        "project_path_contains_non_ascii": not str(ROOT).isascii(),
        "project_writable": writable,
        "write_error": write_error,
        "quant_lab_origin": str(quant_lab_origin) if quant_lab_origin else None,
        "editable_install_matches_project": editable_install_matches_project,
        "tushare_token_present": bool(os.getenv("TUSHARE_TOKEN")),
        "packages": present,
        "package_versions": versions,
        "missing_required": missing,
        "compatibility_errors": compatibility_errors,
        "optional_missing_is_error": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (
        sys.version_info < (3, 10)
        or not writable
        or not editable_install_matches_project
        or missing
        or compatibility_errors
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
