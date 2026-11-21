"""ETF 候选池：分类、去重、门槛与接口测试。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.providers.etf_universe import EtfRow
from app.services import config_service
from app.services.universe_service import build_records, classify, clean_theme, session_state


def _row(code: str, name: str, amount: float, days: int = 800, price: float = 4.0, mktcap: float = 5e10) -> EtfRow:
    return EtfRow(
        code=code,
        symbol=f"sh{code}",
        name=name,
        market="SH",
        price=price,
        change_pct=0.5,
        amount=amount,
        mktcap=mktcap,
        list_date=date.today() - timedelta(days=days),
    )


def _detail(nav: float = 4.0, index_name: str = "沪深300指数", fund_type: str = "指数型-股票") -> dict:
    return {"nav": nav, "endnav": 1e11, "index_name": index_name, "fund_type": fund_type}


def test_reit_is_excluded_by_name():
    """REITs 不纳入系统：必须被明确拒绝，而不是掉进行业 ETF 参与打分。"""
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("508000", "华安张江产业园REIT", 50e8, price=3.0, mktcap=5e9)]
    details = {"508000": _detail(nav=3.0, index_name="")}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 0
    record = built["buckets"]["reit"][0]
    assert record["status"] == "rejected"
    assert any(r.startswith("name_excluded:REIT") for r in record["reject_reasons"])


def test_delisted_name_is_excluded():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("510999", "某某退市ETF", 50e8)]
    details = {"510999": _detail()}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 0
    record = next(r for group in built["buckets"].values() for r in group)
    assert any(r.startswith("name_excluded:") for r in record["reject_reasons"])


def test_session_state_flags_incomplete_amount():
    from datetime import datetime

    assert session_state(datetime(2026, 11, 5, 8, 0)) == "pre_open"
    assert session_state(datetime(2026, 11, 5, 10, 0)) == "intraday"
    assert session_state(datetime(2026, 11, 5, 12, 39)) == "intraday"  # 午休也只有半日量
    assert session_state(datetime(2026, 11, 5, 15, 30)) == "after_close"
    assert session_state(datetime(2026, 11, 7, 10, 0)) == "closed"     # 周六


def test_classify_categories():
    assert classify("沪深300ETF华泰柏瑞", "指数型-股票") == "broad"
    assert classify("科创半导体ETF华夏", "指数型-股票") == "sector"
    assert classify("纳指ETF国泰", "国际(QDII)") == "cross_border"
    assert classify("国债ETF国泰", "债券型") == "bond"
    assert classify("银华日利ETF", "货币型-普通货币") == "money"
    assert classify("黄金ETF华安", "商品型") == "commodity"
    # 以下三类名称里没有"债"/"沪深300"/"港股"等典型词，曾被误判为行业主题
    assert classify("短融ETF海富通", "") == "bond"
    assert classify("同业存单指数ETF", "") == "bond"
    assert classify("上证指数ETF富国", "") == "broad"
    assert classify("香港证券ETF易方达", "") == "cross_border"
    assert classify("巴西ETF华夏", "") == "cross_border"
    assert classify("日本东证指数ETF南方", "") == "cross_border"
    # 反向保护：不能因为加了"亚洲/新兴"把国内主题误判成跨境
    assert classify("战略新兴ETF华夏", "") == "sector"
    # 宽基前缀 + 行业词：创业板软件/新能源是行业主题，不是宽基
    assert classify("创业板软件ETF华夏", "") == "sector"
    assert classify("创业板新能源ETF国泰", "") == "sector"
    assert classify("创业板人工智能ETF华宝", "") == "sector"
    # 纯宽基仍应归宽基
    assert classify("创业板ETF易方达", "") == "broad"
    assert classify("创业板50ETF华安", "") == "broad"
    assert classify("创业板成长ETF华夏", "") == "broad"


def test_clean_theme_strips_company():
    assert clean_theme("沪深300ETF华泰柏瑞") == "沪深300"
    assert clean_theme("科创半导体ETF华夏") == "科创半导体"


def test_dedupe_keeps_two_per_index():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [
        _row("510300", "沪深300ETF华泰柏瑞", 10e8),
        _row("159919", "沪深300ETF嘉实", 8e8),
        _row("510310", "沪深300ETF易方达", 1e8),
    ]
    details = {r.code: _detail() for r in rows}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 2
    rejected = [r for group in built["buckets"].values() for r in group if r["status"] == "rejected"]
    assert any(reason.startswith("duplicate_index") for r in rejected for reason in r["reject_reasons"])


def test_premium_gate_rejects_expensive_etf():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("512480", "半导体ETF国联安", 5e8, price=4.2)]
    details = {"512480": _detail(nav=4.0, index_name="半导体指数")}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 0
    record = built["buckets"]["sector"][0]
    assert any("premium" in reason for reason in record["reject_reasons"])


def test_premium_uses_prev_close_not_intraday_price():
    """盘中刷新时，当日涨幅不能被算成溢价。

    回归 2026-11-05 的 bug：price/nav 会把 +6% 的当日涨幅算成 6% 溢价，
    导致 premium>3% 的标的从 8 只暴增到 49 只、候选数从 254 掉到 198。
    """
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    row = EtfRow(
        code="510300",
        symbol="sh510300",
        name="沪深300ETF华泰柏瑞",
        market="SH",
        price=1.06,
        change_pct=6.0,
        amount=10e8,
        mktcap=5e10,
        list_date=date.today() - timedelta(days=800),
    )
    details = {"510300": _detail(nav=1.0, index_name="沪深300指数")}
    built = build_records([row], details, cfg)
    record = built["buckets"]["broad"][0]
    assert built["candidate"] == 1, record["reject_reasons"]
    assert record["premium_basis"] == "prev_close"
    assert abs(record["premium_est"]) < 1e-9


def test_listed_days_gate():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("588999", "科创芯片ETF某公司", 5e8, days=100)]
    details = {"588999": _detail(index_name="科创芯片指数")}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 0
    record = next(r for group in built["buckets"].values() for r in group)
    assert "listed_days<250" in record["reject_reasons"]


def test_money_etf_skips_premium_check():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("511880", "银华日利ETF", 20e8, price=100.9, mktcap=1e11)]
    details = {"511880": _detail(nav=100.93, index_name="", fund_type="货币型-普通货币")}
    built = build_records(rows, details, cfg)
    assert built["candidate"] == 1
    record = built["buckets"]["money"][0]
    assert record["premium_est"] is None


def test_missing_detail_is_rejected_not_guessed():
    cfg = config_service.load_yaml("etf_universe_rules.yaml")
    rows = [_row("511990", "华宝添益ETF", 20e8, price=99.99, mktcap=1e11)]
    built = build_records(rows, {}, cfg)
    assert built["candidate"] == 0
    record = built["buckets"]["money"][0]
    assert "detail_budget_exceeded" in record["reject_reasons"]


def test_universe_api_empty_state(client):
    resp = client.get("/api/universe")
    assert resp.status_code == 200
    assert "items" in resp.json()
    summary = client.get("/api/universe/summary")
    assert summary.status_code == 200
    assert summary.json()["total"] >= 0
    assert client.get("/api/universe/999999").status_code in (200, 404)
