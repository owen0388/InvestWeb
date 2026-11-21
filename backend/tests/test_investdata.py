"""InvestData 接入层测试。

依赖外部服务（127.0.0.1:8100）。服务不可达时整组跳过，
而不是让 CI 失败——但跳过的原因会打印出来，不静默。
"""
from __future__ import annotations

import pytest

from app.datasource import AmbiguousSymbol, InvestDataClient
from app.datasource.investdata import BarSeries


def _service_ready() -> bool:
    try:
        c = InvestDataClient(timeout=5)
        return (c.ready() or {}).get("database") == "ok"
    except Exception:  # noqa: BLE001
        return False


READY = _service_ready()
pytestmark = pytest.mark.skipif(
    not READY, reason="InvestData 服务不可达（127.0.0.1:8100），跳过接入层测试"
)


@pytest.fixture(scope="module")
def client():
    c = InvestDataClient()
    yield c
    c.close()


# ---------- 标的识别 ----------

def test_all_five_types_are_available(client):
    for t in ("stock", "etf", "index", "industry_index", "concept_index"):
        items = client.list_instruments(instrument_type=t, limit=3)
        assert len(items) > 0, f"{t} 类别无数据"
        assert all(x.get("instrument_type") == t for x in items)


def test_same_symbol_different_type_is_not_confused(client):
    """000001 既是平安银行也是上证指数，必须靠 instrument_type 区分。"""
    with pytest.raises(AmbiguousSymbol):
        client.resolve("000001")
    stock = client.resolve("000001", instrument_type="stock")
    index = client.resolve("000001", instrument_type="index")
    assert stock["instrument_id"] != index["instrument_id"]
    assert "平安" in (stock.get("name") or "")
    assert "上证" in (index.get("name") or "")


def test_instrument_id_is_unique_key(client):
    """同一代码不同类型必须得到不同 instrument_id。"""
    a = client.resolve("000001", instrument_type="stock")
    b = client.resolve("000001", instrument_type="index")
    assert len(a["instrument_id"]) >= 32
    assert a["instrument_id"] != b["instrument_id"]


# ---------- 行情 ----------

@pytest.fixture(scope="module")
def sample_stock(client):
    return client.resolve("600519", instrument_type="stock")


def test_bars_return_recent_data_not_oldest(client, sample_stock):
    """接口按日期升序返回，实现必须取到最近 N 根，而不是最早的 N 根。

    这是开发中真实出现过的 bug：取第一页拿到 2021 年数据。
    """
    latest = client.latest_trade_date()
    s = client.bars(sample_stock["instrument_id"], days=5, adjustment="qfq")
    assert s.length == 5
    assert s.last_date == latest, f"末根 {s.last_date} 应等于最新交易日 {latest}"
    # 日期必须递增
    dates = [b.trade_date for b in s.bars]
    assert dates == sorted(dates)


def test_days_parameter_controls_length(client, sample_stock):
    for n in (20, 60):
        s = client.bars(sample_stock["instrument_id"], days=n, adjustment="qfq")
        assert s.length <= n
        assert s.length >= min(n, 10)


def test_three_adjustments_differ(client, sample_stock):
    """raw/qfq/hfq 必须给出不同价格（除权过的标的）。"""
    raw = client.bars(sample_stock["instrument_id"], days=1, adjustment="raw")
    qfq = client.bars(sample_stock["instrument_id"], days=1, adjustment="qfq")
    hfq = client.bars(sample_stock["instrument_id"], days=1, adjustment="hfq")
    prices = {raw.bars[-1].close, qfq.bars[-1].close, hfq.bars[-1].close}
    assert len(prices) >= 2, "三种口径价格完全相同，复权可能未生效"


def test_invalid_adjustment_rejected(client, sample_stock):
    with pytest.raises(ValueError):
        client.bars(sample_stock["instrument_id"], days=5, adjustment="invalid")


def test_quality_status_present(client, sample_stock):
    s = client.bars(sample_stock["instrument_id"], days=5, adjustment="qfq")
    assert all(b.quality_status for b in s.bars)
    assert all(b.is_clean for b in s.bars)


def test_volume_and_amount_units(client, sample_stock):
    """volume 是股、amount 是元：amount/volume 应接近当日均价。"""
    s = client.bars(sample_stock["instrument_id"], days=3, adjustment="raw")
    b = s.bars[-1]
    assert b.volume and b.volume > 0
    assert b.amount and b.amount > 0
    avg = b.amount / b.volume
    assert 0.85 <= avg / b.close <= 1.15, f"均价 {avg} 与收盘 {b.close} 偏离过大，单位可能不一致"


# ---------- 容错 ----------

def test_unknown_symbol_raises(client):
    with pytest.raises(Exception):
        client.resolve("999999999")


def test_batch_tolerates_individual_failure(client):
    """单只失败不应影响整批。"""
    good = client.resolve("600519", instrument_type="stock")["instrument_id"]
    bad = "00000000-0000-0000-0000-000000000000"
    res = client.bars_batch([good, bad], days=5)
    assert res[good].length > 0
    assert res[bad].length == 0


def test_industry_lookup_and_members(client):
    """按代码检索板块。

    不用中文名——本地全量库有 1171/1727 个板块 name 为 null（线上已补齐，
    本地尚未跑该迁移）。按 symbol 检索两边都成立，测试不会因环境差异失败。
    """
    lst = client.industries(keyword="881319", limit=5)
    assert lst, "按代码 881319（半导体）应能检索到板块"
    target = next((x for x in lst if x.get("symbol") == "881319"), lst[0])
    assert target["instrument_type"] in ("industry_index", "concept_index")
    members = client.industry_members(target["instrument_id"], limit=500)
    assert len(members) > 10


def test_history_is_capped_to_five_years(client):
    """无论请求多少根，最多返回 5 年（与线上口径一致）。"""
    iid = client.resolve("600519", instrument_type="stock")["instrument_id"]
    s = client.bars(iid, days=8000, adjustment="qfq")
    assert s.length <= 1212, f"返回 {s.length} 根，超过 5 年上限"
