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
        "matplotlib",
        "pytest",
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
    report = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "project_root": str(ROOT),
        "project_path_contains_non_ascii": not str(ROOT).isascii(),
        "project_writable": writable,
        "tushare_token_present": bool(os.getenv("TUSHARE_TOKEN")),
        "packages": {
            package: importlib.util.find_spec(package) is not None
            for package in packages
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if sys.version_info < (3, 10) or not writable:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
