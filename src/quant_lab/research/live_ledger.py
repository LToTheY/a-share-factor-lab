"""Immutable local snapshots for genuinely unseen paper signals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _frame_digest(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_live_snapshot(
    latest: pd.DataFrame,
    orders: pd.DataFrame,
    config_path: str | Path,
    state_dir: str | Path,
    live_start_date: str | None,
) -> dict[str, Any]:
    """Write once per signal date and refuse to silently revise history."""
    if latest.empty:
        return {"status": "NO_SIGNAL"}
    signal_date = pd.Timestamp(latest["trade_date"].max()).normalize()
    if live_start_date and signal_date < pd.Timestamp(live_start_date):
        return {"status": "BEFORE_LIVE_START", "signal_date": str(signal_date.date())}
    output = Path(state_dir) / str(signal_date.date())
    output.mkdir(parents=True, exist_ok=True)
    signal_columns = [
        column
        for column in [
            "trade_date",
            "symbol",
            "factor_rank",
            "factor_processed",
            "valid_factor_count",
            "close",
        ]
        if column in latest
    ]
    signal = latest[signal_columns].sort_values(["factor_rank", "symbol"])
    order = orders.sort_values(list(orders.columns)).reset_index(drop=True)
    config_bytes = Path(config_path).read_bytes()
    metadata = {
        "signal_date": str(signal_date.date()),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "signal_sha256": _frame_digest(signal),
        "orders_sha256": _frame_digest(order),
    }
    metadata_path = output / "metadata.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if existing != metadata:
            raise RuntimeError(
                f"Live snapshot for {signal_date.date()} already exists and differs"
            )
        return {"status": "VERIFIED_EXISTING", **metadata}
    signal.to_csv(output / "signals.csv", index=False, encoding="utf-8-sig")
    order.to_csv(output / "orders.csv", index=False, encoding="utf-8-sig")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"status": "RECORDED", **metadata}
