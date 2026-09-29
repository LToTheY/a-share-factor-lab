import pytest

from quant_lab.dashboard.catalog import FactorCatalog


def record(**changes):
    return dict(
        {
            "frequency": "intraday",
            "interval": "5m",
            "name": "reversal",
            "title": "短期反转",
            "category": "反转",
            "stage": "待研究",
            "definition": "过去五分钟收益的反方向",
            "notes": "检查时点",
        },
        **changes,
    )


def test_save_reload_edit_and_frequency_isolation(tmp_path):
    path = tmp_path / "state" / "catalog.sqlite3"
    catalog = FactorCatalog(path)
    assert catalog.records() == []
    assert not path.exists()
    catalog.save(record())
    catalog.save(record(frequency="daily", interval="1d"))
    catalog.save(record(interval="15m"))
    with pytest.raises(ValueError, match="同名"):
        catalog.save(record())
    FactorCatalog(path).save(record(notes="已完成初步检查"), replace=True)
    rows = FactorCatalog(path).records()
    assert len(rows) == 3
    assert (
        next(row for row in rows if row["interval"] == "5m")["notes"]
        == "已完成初步检查"
    )
    assert next(row for row in rows if row["interval"] == "1d")["notes"] == "检查时点"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "../bad"},
        {"title": " "},
        {"definition": ""},
        {"frequency": "daily"},
        {"stage": "有效"},
        {"category": "bad"},
    ],
)
def test_invalid_records_do_not_create_database(tmp_path, changes):
    catalog = FactorCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError):
        catalog.save(record(**changes))
    assert not catalog.path.exists()


def test_edit_missing_record_does_not_insert(tmp_path):
    catalog = FactorCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError, match="不存在"):
        catalog.save(record(), replace=True)
    assert catalog.records() == []
