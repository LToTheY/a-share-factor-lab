from pathlib import Path

from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from dashboard.views import factor_library
from quant_lab.dashboard import ArtifactStore
from quant_lab.dashboard.catalog import FactorCatalog

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


def test_create_edit_reload_and_search(tmp_path, monkeypatch):
    path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(factor_library, "CATALOG_PATH", path)
    app = AppTest.from_file(str(APP))
    app._page_hash = calc_hash("factor-library")
    app.run()
    assert not app.exception
    app.radio[0].set_value("intraday").run()
    app.text_input[0].set_value("reversal_test")
    app.text_input[1].set_value("分钟反转")
    app.text_area[0].set_value("过去五分钟收益反向")
    app.selectbox[0].set_value("5m")
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    assert FactorCatalog(path).records()[0]["interval"] == "5m"
    app.text_area[-1].set_value("检查过时点")
    next(button for button in app.button if button.label == "保存修改").click().run()
    assert not app.exception
    assert FactorCatalog(path).records()[0]["notes"] == "检查过时点"
    reloaded = AppTest.from_file(str(APP))
    reloaded._page_hash = calc_hash("factor-library")
    reloaded.run()
    reloaded.radio[0].set_value("intraday").run()
    assert any(heading.value == "分钟反转" for heading in reloaded.subheader)
    next(field for field in reloaded.text_input if field.label == "搜索因子").set_value(
        "no_matching_factor"
    ).run()
    assert not any(heading.value == "分钟反转" for heading in reloaded.subheader)
    assert not reloaded.exception


def test_diagnostics_navigation_preserves_selected_factor(tmp_path, monkeypatch):
    monkeypatch.setattr(factor_library, "CATALOG_PATH", tmp_path / "catalog.sqlite3")
    (tmp_path / "factor_summary.csv").write_text(
        "factor,mean_ic,icir,win_rate,observations\n"
        "momentum_20_5,0.01,0.5,0.5,3\nreversal_5,0.02,0.6,0.6,3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(factor_library, "store", lambda: ArtifactStore(tmp_path))
    app = AppTest.from_file(str(APP))
    app._page_hash = calc_hash("factor-library")
    app.session_state["report_dir"] = str(tmp_path)
    app.run()
    app.button(key="diagnose_reversal_5").click().run()
    assert not app.exception
    assert app.selectbox[0].value == "reversal_5"
