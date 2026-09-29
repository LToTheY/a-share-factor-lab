"""Connect to WRDS and report the CSMAR libraries available to this account.

The password is requested locally and never saved. Approve Duo Mobile locally.
Only metadata is downloaded; the report contains no login credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quant_lab.data.wrds_access import (
    candidate_csmar_libraries,
    describe_library,
    open_wrds,
)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def _candidate_libraries(libraries: list[str]) -> list[str]:
    return candidate_csmar_libraries(libraries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        help="WRDS username; defaults to the WRDS_USERNAME environment variable",
    )
    parser.add_argument(
        "--library",
        help="After connecting, list tables in this exact library",
    )
    parser.add_argument(
        "--output", default="data/state/wrds_csmar_catalog.json",
        help="Local metadata-only report; no username, password or market rows",
    )
    args = parser.parse_args()

    _load_dotenv_if_available()
    username = args.username or os.getenv("WRDS_USERNAME") or input("WRDS username: ")
    if not username:
        raise SystemExit(
            "Missing WRDS username. Set WRDS_USERNAME in .env or pass --username."
        )

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata_only": True,
        "libraries": {},
    }
    print("Connecting read-only. Enter password locally and approve Duo; nothing is saved.")
    try:
        with open_wrds(username.strip()) as connection:
            libraries = list(connection.list_libraries())
            candidates = [args.library] if args.library else _candidate_libraries(libraries)
            print(f"Connected. Account can see {len(libraries)} WRDS libraries.")
            if not candidates:
                print("No CSMAR-named library found. Check your institution's subscription.")
            for library in candidates:
                try:
                    metadata = describe_library(connection, library)
                except Exception as exc:  # noqa: BLE001 - redact remote errors
                    # Do not persist raw exception messages or connection strings.
                    metadata = {"error_type": type(exc).__name__}
                report["libraries"][library] = metadata
                print(f"{library}: {metadata.get('table_count', 'unavailable')} tables")
                for table in metadata.get("tables", {}):
                    print(f"  - {table}")
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - redact credentials
        raise SystemExit(
            f"WRDS probe stopped ({type(exc).__name__}). "
            "Check local network, username/password, Duo and subscription; "
            "credentials and exception details were not saved."
        ) from None

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(f"Metadata report: {output}")
    if not report["libraries"] or any(
        not item.get("tables") for item in report["libraries"].values()
    ):
        raise SystemExit("CSMAR metadata incomplete; inspect report before downloading data.")


if __name__ == "__main__":
    main()
