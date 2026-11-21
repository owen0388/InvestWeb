"""每日机会输出：空池提示、无候选归因、质量门槛、数量上限。"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.models.entities import EtfUniverse, MarketObservation
from app.services.opportunity_radar_service import MIN_QUALITY, build_daily


def _series(start: float, step: float, n: int = 260) -> list[float]:
    return [start + i * step for i in range(n)]


def _add_etf(
    db,
    code: str,
    prices: list[float],
    quality: float | None,
    category: str = "broad",
    tracked_index: str = "",
) -> None:
    base = date(2025, 9, 1)
    db.add(
        EtfUniverse(
            code=code,
            symbol=f"sh{code}",
            name=f"测试{code}",
            market="SH",
            category=category,
            tracked_index=tracked_index,
            status="candidate",
            quality_score=quality,
            quality_confidence=1.0,
            quality_breakdown={},
            amount=1e9,
            mktcap=1e10,
            premium_est=0.001,
            listed_days=800,
            list_date=base - timedelta(days=800),
        )
    )
    for idx, price in enumerate(prices):
        db.add(
            MarketObservation(
                instrument_id=None,
                symbol=f"sh{code}",
                trade_date=base + timedelta(days=idx),
                close=price,
                data_type="close",
                currency="CNY",
                source_code="eastmoney_kline",
            )
        )


def _cleanup(db) -> None:
    codes = [row.code for row in db.scalars(select(EtfUniverse).where(EtfUniverse.code.like("T%")))]
    symbols = [f"sh{c}" for c in codes]
    db.execute(delete(MarketObservation).where(MarketObservation.symbol.in_(symbols)))
    db.execute(delete(EtfUniverse).where(EtfUniverse.code.like("T%")))
    db.commit()


def test_empty_universe_says_so(client, db):
    _cleanup(db)
    payload = client.get("/api/opportunity-radar/daily").json()
    assert payload["message"] == "候选池为空，请先刷新 ETF 候选池"
    assert payload["items"] == []


def test_no_candidate_explains_why(client, db):
    _cleanup(db)
    # 持续下跌 → 全部跌破 MA60
    for idx in range(3):
        _add_etf(db, f"T{idx:05d}", _series(200.0, -0.3), quality=80.0)
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert payload["items"] == []
    assert payload["message"] == "今日无候选"
    assert payload["blocking"], "无候选时必须给出原因"
    assert any("MA60" in reason for reason in payload["blocking"])
    _cleanup(db)


def test_candidate_returned_with_invalidation(client, db):
    _cleanup(db)
    _add_etf(db, "T00001", _series(100.0, 0.2), quality=80.0)
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["state"] == "CANDIDATE"
    assert item["score"] > 0
    assert "MA20" in item["invalidation"], "失效条件必须可执行"
    assert "候选" in payload["message"]
    _cleanup(db)


def test_low_quality_is_excluded_and_counted(client, db):
    _cleanup(db)
    _add_etf(db, "T00002", _series(100.0, 0.2), quality=MIN_QUALITY - 1)
    _add_etf(db, "T00003", _series(100.0, 0.2), quality=None)
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert payload["items"] == []
    assert payload["buckets"].get("LOW_QUALITY") == 1
    assert payload["buckets"].get("NO_QUALITY") == 1
    assert payload["blocked_by_quality"] == 2
    _cleanup(db)


def test_limit_cannot_exceed_ten(client, db):
    assert client.get("/api/opportunity-radar/daily?limit=50").status_code == 422
    assert client.get("/api/opportunity-radar/daily?limit=10").status_code == 200


def test_score_prefers_stronger_trend(db):
    _cleanup(db)
    # 必须给不同主题，否则会被同主题去重合并，比不了分数
    _add_etf(db, "T00004", _series(100.0, 0.4), quality=80.0, tracked_index="指数A")
    _add_etf(db, "T00005", _series(100.0, 0.1), quality=80.0, tracked_index="指数B")
    db.commit()

    payload = build_daily(db)
    items = {item["code"]: item for item in payload["items"]}
    assert items["T00004"]["score"] > items["T00005"]["score"]
    _cleanup(db)


def test_same_theme_is_deduplicated(client, db):
    """同一主题只保留分数最高的一只，避免前 10 条被同一板块占满。"""
    _cleanup(db)
    _add_etf(db, "T00010", _series(100.0, 0.4), quality=80.0, tracked_index="中证消费指数")
    _add_etf(db, "T00011", _series(100.0, 0.3), quality=80.0, tracked_index="中证消费指数")
    _add_etf(db, "T00012", _series(100.0, 0.2), quality=80.0, tracked_index="中证白酒指数")
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    themes = [item["theme"] for item in payload["items"]]
    # 三只名称各异、指数名各异，但"酒/白酒/消费"同属消费族；
    # 同族上限 2，所以留 2 条、跳过 1 条
    assert max(Counter(themes).values()) <= 2, f"同族超过上限：{themes}"
    assert payload["skipped_same_theme"] == 1
    assert set(themes) == {"family:consumer"}
    _cleanup(db)


def test_category_quota_caps_dominant_class(client, db):
    """单一资产类别不能占满列表：债券各指数名不同，靠主题去重拦不住。"""
    _cleanup(db)
    # 8 只债券，指数名各不相同 → 不会触发主题去重。
    # 涨幅必须保证 8 只**全部**过机会分门槛：同类排名最后一名的 rs=0，
    # 其 score = 0.6 × 趋势分。1.5/日 时趋势分 62.8 → 37.7 被过滤，
    # 只剩 7 只参与配额。5.0/日 时趋势分约 68.7 → 41.2，全部通过。
    for idx in range(8):
        _add_etf(
            db,
            f"T0002{idx}",
            _series(100.0, 5.0),
            quality=80.0,
            category="bond",
            tracked_index=f"某债券指数{idx}",
        )
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert len(payload["items"]) == 3, payload["items"]
    assert payload["skipped_category_quota"] == 5
    assert all(item["category"] == "bond" for item in payload["items"])
    _cleanup(db)


def test_low_score_is_not_padded(client, db):
    """趋势达标但机会分过低时不入选，不为了凑数塞进列表。"""
    _cleanup(db)
    # 极慢的上涨：趋势成立但强度极低
    _add_etf(db, "T00030", _series(100.0, 0.001), quality=80.0, tracked_index="慢涨指数")
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert payload["items"] == []
    assert payload["below_min_score"] == 1
    assert payload["message"] == "今日无候选"
    _cleanup(db)


def test_strategy_comparison_has_baselines(client):
    """对比必须包含「买入持有」这个基准，否则无法判断策略有没有超额收益。"""
    payload = client.post("/api/backtests/comparison", json={}).json()
    if not payload.get("ok"):
        # 测试库无日线，明确失败也要给出原因，不能空返回
        assert payload["message"]
        return
    codes = [s["code"] for s in payload["strategies"]]
    assert "buy_hold" in codes
    assert len(codes) >= 4
    hold = next(s for s in payload["strategies"] if s["code"] == "buy_hold")
    assert hold["metrics"]["turnover_total"] == 0, "买入持有不应产生换手"
    rebal = next(s for s in payload["strategies"] if s["code"] == "rebalance")
    assert rebal["metrics"]["turnover_total"] > 0, "定期再平衡必须产生换手（权重漂移后拉回）"
    assert payload["period"]["start"] <= payload["period"]["end"]
    assert set(payload["sensitivity"]) == {"max_tilt", "rebalance_days", "cost_bps"}


REQUIRED_CARD_FIELDS = [
    "title", "assets", "change", "first_seen", "evidence", "logic",
    "drivers", "risks", "transmission", "horizon", "score", "confidence",
    "priced_in", "counter_evidence", "invalidation", "watch_triggers",
    "action", "weight_range", "delta", "updated_at",
]


def test_card_has_all_required_fields(client, db):
    """任务书要求 20 项研究卡片字段，缺一项就不是完整卡片。"""
    _cleanup(db)
    _add_etf(db, "T00040", _series(100.0, 0.5), quality=80.0, tracked_index="测试指数A")
    db.commit()

    payload = client.get("/api/opportunity-radar/daily").json()
    assert payload["items"], payload["message"]
    card = payload["items"][0]
    missing = [f for f in REQUIRED_CARD_FIELDS if f not in card]
    assert not missing, f"缺少卡片字段：{missing}"
    assert payload["card_fields"] == 20
    _cleanup(db)


def test_card_never_fakes_evidence(client, db):
    """没有关联事件时必须明说，不能编造新闻证据。"""
    _cleanup(db)
    _add_etf(db, "T00041", _series(100.0, 0.5), quality=80.0, tracked_index="测试指数B")
    db.commit()

    card = client.get("/api/opportunity-radar/daily").json()["items"][0]
    assert card["evidence"]["kind"] == "price_series"
    assert "未关联具体新闻事件" in card["evidence"]["note"]
    assert card["counter_evidence"], "相反证据不能为空数组，必须明确说明"
    _cleanup(db)


def test_card_action_mapping(client, db):
    _cleanup(db)
    _add_etf(db, "T00042", _series(100.0, 0.5), quality=80.0, tracked_index="测试指数C")
    db.commit()

    card = client.get("/api/opportunity-radar/daily").json()["items"][0]
    assert card["action"] in {
        "积极研究", "观察等待", "维持配置", "考虑提高权重",
        "考虑降低权重", "降低风险", "暂不判断",
    }
    _cleanup(db)
