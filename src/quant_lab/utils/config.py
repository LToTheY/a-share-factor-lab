"""Small configuration loader with no framework dependency."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    """Load YAML configuration.

    PyYAML is kept at the boundary so the core research code stays testable with
    plain dictionaries.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - installation guidance
        raise RuntimeError("Install PyYAML with: pip install PyYAML") from exc

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise TypeError(f"Configuration must be a mapping: {config_path}")
    return data


def project_root() -> Path:
    """Return the repository root from this source file."""
    return Path(__file__).resolve().parents[3]


def resolve_from_root(path: str | Path) -> Path:
    """Resolve a configuration path relative to the repository root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root() / candidate
