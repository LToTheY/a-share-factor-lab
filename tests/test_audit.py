import tempfile
import unittest
from pathlib import Path

from quant_lab.data.audit import audit_daily_data, write_audit_report
from quant_lab.data.synthetic import make_synthetic_daily_data


class AuditTest(unittest.TestCase):
    def test_synthetic_panel_passes_strict_status_audit(self) -> None:
        frame = make_synthetic_daily_data(10, 120)
        result = audit_daily_data(frame)
        self.assertFalse(result.has_errors)
        self.assertTrue(result.summary["audit_passed"])

    def test_unknown_status_is_blocking(self) -> None:
        frame = make_synthetic_daily_data(10, 120)
        frame["is_st_known"] = False
        result = audit_daily_data(frame)
        self.assertTrue(result.has_errors)
        self.assertIn("LOW_IS_ST_KNOWN", {issue.code for issue in result.issues})

    def test_writes_human_and_machine_reports(self) -> None:
        result = audit_daily_data(make_synthetic_daily_data(10, 120))
        with tempfile.TemporaryDirectory() as temporary:
            write_audit_report(result, temporary)
            self.assertTrue((Path(temporary) / "audit.json").exists())
            self.assertTrue((Path(temporary) / "audit.md").exists())


if __name__ == "__main__":
    unittest.main()
