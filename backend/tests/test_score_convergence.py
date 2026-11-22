"""P0 评分口径收敛测试。

验证：
1. 同一标的在 /radar 与 /opportunities 得到相同的核心机会分
2. 缺失数据不会被赋予高分
3. ETF 质量分未被本任务改动（registry 只读）
4. 非 ETF 标的保留 legacy_v1 标记
5. 动作词是研究优先级，不是交易指令
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import AssetSignal, EtfUniverse
from app.services import opportunity_registry as reg
from tests.test_radar import _add_etf, _cleanup, _series

RESEARCH_LEVELS = {"高优先级研究", "重点关注", "观察", "暂不关注", "数据不足"}
TRADE_WORDS = {"买入", "卖出", "加仓", "减仓", "降低风险", "考虑提高权重"}


def _cleanup_all(db) -> None:
    """清理本文件造的所有数据（含 Instrument / AssetSignal），避免污染其他用例。"""
    from sqlalchemy import delete

    from app.models.entities import Instrument

    _cleanup(db)
    sig_symbols = [r[0] for r in db.execute(
        select(AssetSignal.symbol).where(AssetSignal.symbol.like("shT9%"))
    ).all()]
    if sig_symbols:
        db.execute(delete(AssetSignal).where(AssetSignal.symbol.in_(sig_symbols)))
    db.execute(delete(Instrument).where(Instrument.symbol.like("shT9%")))
    db.commit()
    reg.invalidate_cache()


def _seed_rising_etf(db, code: str = "T90001", quality: float = 80.0) -> str:
    """造一只上涨趋势的候选 ETF —— 应当被判为 CANDIDATE 并给出机会分。"""
    _add_etf(db, code, _series(100.0, 0.5), quality=quality, tracked_index="测试指数X")
    db.commit()
    reg.invalidate_cache()
    return f"sh{code}"


def _seed_legacy_signal(db, symbol: str, score: float = 20.0) -> None:
    """造一条旧口径的 asset_signals 记录（低分、交易性动作词）。

    asset_signals.instrument_id 是 NOT NULL，所以要先建标的记录。
    """
    from app.models.entities import Instrument

    inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        inst = Instrument(
            symbol=symbol,
            name="测试标的",
            asset_class_code="FUND_ETF_SECTOR",
            instrument_type="etf",
        )
        db.add(inst)
        db.flush()

    db.add(
        AssetSignal(
            instrument_id=inst.id,
            symbol=symbol,
            asset_class_code="FUND_ETF_SECTOR",
            as_of=date.today(),
            total_score=score,
            confidence=0.5,
            data_quality=0.6,
            action="降低风险",
            trend=30.0,
            components={},
            version=1,
        )
    )
    db.commit()


# ---------- 等级映射 ----------

def test_level_mapping_is_monotonic():
    assert reg.level_for(None) == reg.LEVEL_NA
    assert reg.level_for(10) == reg.LEVEL_SKIP
    assert reg.level_for(35) == reg.LEVEL_WATCH
    assert reg.level_for(50) == reg.LEVEL_FOCUS
    assert reg.level_for(65) == reg.LEVEL_HIGH
    assert reg.level_for(99) == reg.LEVEL_HIGH


def test_missing_score_is_not_high_level():
    assert reg.level_for(None) != reg.LEVEL_HIGH
    assert reg.level_for(None, "NO_DATA") in (reg.LEVEL_NA, reg.LEVEL_SKIP)


def test_levels_are_research_priorities_not_trades():
    for score in (10, 40, 55, 70, 99):
        assert reg.level_for(score) in RESEARCH_LEVELS
        assert reg.level_for(score) not in TRADE_WORDS


# ---------- 统一结构 ----------

def test_unknown_symbol_returns_na(db):
    r = reg.get_unified_score(db, "NOT_A_REAL_SYMBOL")
    assert r["score"] is None
    assert r["level"] == reg.LEVEL_NA
    assert r["source"] == "none"


def test_etf_in_universe_uses_radar_version(db):
    """候选池内的 ETF 必须走 radar 口径，不能被旧的 asset_signals 覆盖。"""
    _cleanup_all(db)
    sym = _seed_rising_etf(db)
    _seed_legacy_signal(db, sym, score=20.0)   # 同时存在一条"降低风险 20 分"的旧记录
    reg.invalidate_cache()

    r = reg.get_unified_score(db, sym)
    assert r["score_version"] == reg.SCORE_VERSION, "应优先雷达口径而非 legacy"
    assert r["source"] == "radar"
    assert r["score"] is not None, "上涨趋势的 ETF 应给出机会分"
    assert r["score"] != 20.0, "不得沿用旧口径的低分"
    assert r["level"] in RESEARCH_LEVELS
    _cleanup_all(db)
    reg.invalidate_cache()


def test_quality_score_untouched_by_registry(db):
    """registry 只读：调用前后 ETF 质量分不得变化。"""
    _cleanup_all(db)
    sym = _seed_rising_etf(db, code="T90002", quality=77.0)
    code = "T90002"
    before = db.scalar(select(EtfUniverse.quality_score).where(EtfUniverse.code == code))
    reg.get_unified_score(db, sym)
    db.expire_all()
    after = db.scalar(select(EtfUniverse.quality_score).where(EtfUniverse.code == code))
    assert before == after == 77.0
    _cleanup_all(db)
    reg.invalidate_cache()


# ---------- 两接口一致性 ----------

def test_radar_and_opportunities_agree(client, db):
    """同一标的在 /radar 与 /opportunities 的核心机会分必须一致。"""
    _cleanup_all(db)
    sym = _seed_rising_etf(db, code="T90003")
    _seed_legacy_signal(db, sym, score=20.0)
    reg.invalidate_cache()

    radar = {i["symbol"]: i for i in
             client.get("/api/opportunity-radar/daily", params={"limit": 10}).json()["items"]}
    opps = {i["symbol"]: i for i in
            client.get("/api/opportunities", params={"page_size": 200}).json()["items"]}

    assert sym in radar, "造的 ETF 应出现在雷达结果里"
    if sym in opps:
        a, b = radar[sym]["score"], opps[sym]["score"]
        assert abs((a or 0) - (b or 0)) < 0.01, f"两处分数不一致: radar={a} opps={b}"
        assert radar[sym]["action"] == opps[sym]["level"]
    _cleanup_all(db)
    reg.invalidate_cache()


def test_opportunities_filters_use_research_levels(client):
    d = client.get("/api/opportunities", params={"page_size": 1}).json()
    actions = d["filters"]["actions"]
    assert set(actions) <= RESEARCH_LEVELS
    assert not (set(actions) & TRADE_WORDS)
