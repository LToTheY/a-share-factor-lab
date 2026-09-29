"""Unfinished studies must remain visible and cannot become zero-return cases."""

import json

import pandas as pd
from streamlit.testing.v1 import AppTest

from dashboard.views import research_archive


def test_campaign_keeps_missing_case_and_source_order(tmp_path, monkeypatch):
    state = {"code_version": "historical", "status": "stopped", "reason": "storage budget",
             "plan": [{"key": name, "pool": "000905.SH", "factor": name, "selection_mode": "rank"}
                      for name in ("loss", "unfinished")],
             "cases": {"loss": {"status": "succeeded", "metrics": {"annual_return": -.2},
                                "diagnostics": {"average_cash_ratio": .8}}}}
    table = research_archive.campaign_table(state)
    assert table["因子"].tolist() == ["loss", "unfinished"]
    assert table["年化收益（%）"].iloc[0] == -20
    assert pd.isna(table["年化收益（%）"].iloc[1])
    folder = tmp_path / "reports/generated/campaigns/example"
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(research_archive, "PROJECT_ROOT", tmp_path)
    app = AppTest.from_string("from dashboard.views.research_archive import render\nrender()").run()
    assert not app.exception
    assert app.metric[0].value == "1 / 2"
    assert any("历史代码版本" in value.value for value in app.warning)
    assert app.dataframe[0].value["状态"].tolist() == ["完成", "未完成"]
