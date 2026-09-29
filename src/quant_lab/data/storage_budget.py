"""Bound local data growth before writing downloaded partitions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


def directory_bytes(root: str | Path) -> int:
    directory = Path(root)
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


@dataclass(frozen=True)
class StorageBudget:
    root: Path
    maximum_bytes: int = 10_000_000_000
    minimum_free_bytes: int = 10_000_000_000

    def check(self, additional_bytes: int = 0) -> dict[str, int]:
        if additional_bytes < 0:
            raise ValueError("additional_bytes must be nonnegative")
        if self.maximum_bytes <= 0 or self.minimum_free_bytes < 0:
            raise ValueError("Invalid storage budget")
        used = directory_bytes(self.root)
        disk_root = self.root.resolve()
        while not disk_root.exists():
            disk_root = disk_root.parent
        free = shutil.disk_usage(disk_root).free
        if used + additional_bytes > self.maximum_bytes:
            raise RuntimeError("Data storage budget exceeded; download stopped")
        if free - additional_bytes < self.minimum_free_bytes:
            raise RuntimeError("Free disk reserve would be exceeded; download stopped")
        return {"data_bytes": used, "disk_free_bytes": free}


def write_budgeted_frame(frame, path: Path, budget: StorageBudget) -> None:
    """Serialize bounded partitions, accounting for the complete temporary copy."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    for target in (path, temporary):
        if not target.resolve().is_relative_to(budget.root.resolve()):
            raise ValueError("Output must stay within the budgeted data directory")
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), sink,
                   compression="zstd")
    payload = sink.getvalue().to_pybytes()
    budget.check(len(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(payload)
    temporary.replace(path)
