"""Internal local worker; request files contain no credentials."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.jobs import execute_job

if __name__ == "__main__":
    execute_job(ROOT, sys.argv[1])
