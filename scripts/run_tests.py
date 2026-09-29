"""Run all pytest and unittest-style tests through pytest."""
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    raise SystemExit(pytest.main([str(ROOT / "tests"), "--basetemp", str(ROOT / "data/interim" / ("pytest-" + uuid4().hex)), *sys.argv[1:]]))
