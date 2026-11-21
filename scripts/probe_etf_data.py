"""ETF 数据可得性探测（1a-1 / 1a-2）。

目的：在写评分代码之前，先确认「能拿到什么、覆盖多少、多少只过得了门槛」。
输出：docs/ETF_DATA_PROBE.md + docs/etf_probe_raw.json

用法：
    python scripts/probe_etf_data.py
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
FUND_BASIC = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"

CLIST_FIELDS = "f2,f3,f5,f6,f8,f12,f13,f14,f20,f21,f26"
# f2 最新价 / f3 涨跌幅 / f5 成交量 / f6 成交额 / f8 换手率
# f12 代码 / f13 市场 / f14 名称 / f20 总市值 / f21 流通市值 / f26 上市日 / f116 总市值(估)

HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}


def fetch_all(client: httpx.Client) -> tuple[list[dict], int, list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    total = 0
    for page in range(1, 25):
        params = {
            "pn": page,
            "pz": 100,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": CLIST_FIELDS,
        }
        try:
            resp = client.get(CLIST, params=params, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"page {page}: {str(exc)[:120]}")
            break
        diff = data.get("diff") or []
        total = int(data.get("total") or 0)
        if not diff:
            break
        rows.extend(diff)
        if len(rows) >= total:
            break
        time.sleep(0.25)
    return rows, total, errors


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def fetch_fund_basic(client: httpx.Client, code: str) -> dict:
    """基金基本信息（含单位净值、报告期净资产、成立日、跟踪指数）。

    连续请求会被风控返回空 Datas，因此失败后重试一次并记录 ErrCode。
    """
    params = {
        "FCODE": code,
        "deviceid": "1",
        "plat": "Android",
        "product": "EFund",
        "version": "6.3.8",
    }
    last_error = ""
    for attempt in range(2):
        try:
            resp = client.get(FUND_BASIC, params=params, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            payload = resp.json()
            datas = payload.get("Datas") or {}
            if datas:
                return datas
            last_error = f"empty Datas, ErrCode={payload.get('ErrCode')}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:120]
        time.sleep(0.8)
    return {"_error": last_error}


def classify(name: str) -> str:
    rules = [
        ("货币", ["货币", "现金", "日利", "添益", "快线"]),
        ("债券", ["债", "国债", "政金", "信用", "转债"]),
        ("黄金商品", ["黄金", "白银", "有色", "豆粕", "能源化工", "原油", "商品"]),
        ("跨境", ["纳指", "标普", "纳斯达克", "恒生", "港股", "中概", "日经", "德国", "法国", "东南亚", "沙特", "美国", "亚太", "全球"]),
        ("行业主题", ["芯片", "半导体", "医药", "医疗", "白酒", "消费", "军工", "证券", "银行", "新能源", "光伏", "人工智能", "机器人", "科技", "传媒", "游戏", "地产", "基建", "电力", "煤炭", "钢铁", "化工", "农业", "食品", "汽车", "电池", "通信", "计算机", "软件"]),
        ("宽基", ["300", "500", "1000", "50", "创业板", "科创", "上证", "深证", "中证", "A50", "A500", "红利", "价值", "成长", "等权"]),
    ]
    for label, keys in rules:
        if any(k in name for k in keys):
            return label
    return "其他"


def main() -> None:
    started = time.time()
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        rows, total, errors = fetch_all(client)
        print(f"列表：total={total}，实际获取 {len(rows)}，错误 {len(errors)}")

        parsed = []
        for row in rows:
            code = str(row.get("f12") or "")
            if len(code) != 6:
                continue
            market = "SH" if row.get("f13") == 1 else "SZ"
            name = str(row.get("f14") or "")
            list_raw = row.get("f26")
            list_date = None
            if isinstance(list_raw, int) and list_raw > 19000101:
                try:
                    list_date = datetime.strptime(str(list_raw), "%Y%m%d").date()
                except ValueError:
                    list_date = None
            parsed.append(
                {
                    "code": code,
                    "symbol": f"{market.lower()}{code}",
                    "name": name,
                    "market": market,
                    "price": row.get("f2") if isinstance(row.get("f2"), (int, float)) else None,
                    "change_pct": row.get("f3") if isinstance(row.get("f3"), (int, float)) else None,
                    "amount": row.get("f6") if isinstance(row.get("f6"), (int, float)) else None,
                    "volume": row.get("f5") if isinstance(row.get("f5"), (int, float)) else None,
                    "turnover": row.get("f8") if isinstance(row.get("f8"), (int, float)) else None,
                    "mktcap": row.get("f20") if isinstance(row.get("f20"), (int, float)) else None,
                    "list_date": list_date.isoformat() if list_date else None,
                    "category": classify(name),
                }
            )

        # 抽样：成交额头部 5 只 + 中位数附近 15 只
        with_amount = sorted([p for p in parsed if p["amount"]], key=lambda x: x["amount"], reverse=True)
        sample = with_amount[:5]
        if len(with_amount) > 30:
            mid = len(with_amount) // 2
            sample += with_amount[mid - 7 : mid + 8]
        sample_codes = {p["code"] for p in sample}

        checks = []
        for item in sample:
            basic = fetch_fund_basic(client, item["code"])
            nav = basic.get("DWJZ")
            endnav = basic.get("ENDNAV")
            try:
                nav_f = float(nav) if nav not in (None, "--", "") else None
            except (TypeError, ValueError):
                nav_f = None
            # ENDNAV 在接口里是字符串，需要显式转换
            try:
                endnav_f = float(str(endnav).replace(",", "")) if endnav not in (None, "--", "") else None
            except (TypeError, ValueError):
                endnav_f = None

            # 货币 ETF 的“净值”是每万份收益口径，不能用来算折溢价
            premium = None
            if nav_f and item["price"] and item["category"] != "货币":
                premium = item["price"] / nav_f - 1
            checks.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "price": item["price"],
                    "nav": nav_f,
                    "premium_est": premium,
                    "mktcap_clist": item["mktcap"],
                    "endnav": endnav_f,
                    "index_name": basic.get("INDEXNAME"),
                    "ftype": basic.get("FTYPE"),
                    "estab": basic.get("ESTABDATE"),
                    "error": basic.get("_error"),
                }
            )
            time.sleep(0.45)

    amounts = [p["amount"] for p in parsed if p["amount"]]
    caps = [p["mktcap"] for p in parsed if p["mktcap"]]
    today = date.today()
    listed_days = []
    for p in parsed:
        if p["list_date"]:
            listed_days.append((today - date.fromisoformat(p["list_date"])).days)

    def coverage(key: str) -> tuple[int, float]:
        count = len([p for p in parsed if p.get(key) not in (None, 0)])
        return count, (count / len(parsed) if parsed else 0)

    thresholds = [1000e4, 3000e4, 5000e4, 1e8, 2e8]
    funnel = []
    for th in thresholds:
        passed = [
            p for p in parsed
            if (p["amount"] or 0) >= th
            and p["list_date"]
            and (today - date.fromisoformat(p["list_date"])).days >= 250
        ]
        funnel.append({"amount_threshold": th, "count": len(passed)})

    raw = {
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "total_reported": total,
        "total_parsed": len(parsed),
        "errors": errors,
        "items": parsed,
        "sample_checks": checks,
    }
    (ROOT / "docs" / "etf_probe_raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    def pct(v: float) -> str:
        return f"{v*100:.1f}%"

    lines = [
        "# ETF 数据可得性探测报告",
        "",
        f"- 探测时间：{raw['probed_at']}",
        f"- 列表源：东方财富 clist（ETF 板块 MK0021–0024）",
        f"- 接口报告总数：{total}，成功解析：{len(parsed)}",
        f"- 分页请求错误：{len(errors)}",
        "",
        "## 1. 字段覆盖",
        "",
        "| 字段 | 有值条数 | 覆盖率 | 判定 |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in [
        ("price", "最新价"),
        ("change_pct", "涨跌幅"),
        ("amount", "成交额"),
        ("volume", "成交量"),
        ("mktcap", "规模（市值口径 f20）"),
        ("list_date", "上市日期 f26"),
    ]:
        count, ratio = coverage(key)
        verdict = "可得" if ratio >= 0.95 else ("部分可得" if ratio >= 0.5 else "不可得")
        lines.append(f"| {label} | {count} | {pct(ratio)} | {verdict} |")

    lines += [
        "",
        "## 2. 成交额分布与门槛通过数",
        "",
        f"- 成交额分位（元）：p10={quantile(amounts,0.1)/1e4:.0f}万，p25={quantile(amounts,0.25)/1e4:.0f}万，"
        f"中位={quantile(amounts,0.5)/1e4:.0f}万，p75={quantile(amounts,0.75)/1e4:.0f}万，p90={quantile(amounts,0.9)/1e4:.0f}万",
        f"- 规模分位（元）：中位={quantile(caps,0.5)/1e8:.1f}亿，p75={quantile(caps,0.75)/1e8:.1f}亿，p90={quantile(caps,0.9)/1e8:.1f}亿",
        f"- 上市天数中位：{quantile(listed_days,0.5):.0f} 天",
        "",
        "| 20 日均成交额门槛 | 同时满足上市 ≥250 天的数量 |",
        "| --- | --- |",
    ]
    for row in funnel:
        lines.append(f"| {row['amount_threshold']/1e4:.0f} 万 | {row['count']} |")

    lines += [
        "",
        "> 注：此处用「当日成交额」近似 20 日均额，仅用于首轮粗筛；正式准入会用 20 日均值。",
        "",
        "## 3. 分类分布（名称启发式，待用基金类型校准）",
        "",
        "| 类别 | 数量 |",
        "| --- | --- |",
    ]
    cat: dict[str, int] = {}
    for p in parsed:
        cat[p["category"]] = cat.get(p["category"], 0) + 1
    for k, v in sorted(cat.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## 4. 抽样核对（规模口径与折溢价）",
        "",
        "| 代码 | 名称 | 价格 | 单位净值 | 折溢价(估算) | 市值口径/亿 | 报告期净资产/亿 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in checks:
        premium = f"{c['premium_est'] * 100:+.2f}%" if c["premium_est"] is not None else "—"
        lines.append(
            f"| {c['code']} | {c['name']} | {c['price']} | {c['nav'] or '—'} | {premium} | "
            f"{(c['mktcap_clist'] or 0) / 1e8:.1f} | {(c['endnav'] or 0) / 1e8:.1f} |"
        )

    nav_errors = [c for c in checks if c["error"]]
    lines += [
        "",
        f"- 抽样 {len(checks)} 只，净值接口失败 {len(nav_errors)} 只。",
        "- 折溢价按「最新价 / 最新单位净值 − 1」估算，净值通常为上一交易日，属于日频近似。",
        "- 货币 ETF 的净值是每万份收益口径，不参与折溢价计算；跨境 ETF 存在净值滞后，折溢价需谨慎解读。",
        "- `f20` 与 `f116` 是份额×价格的市值口径，`ENDNAV` 是报告期净资产，两者不可混用。",
        "",
        "## 5. 结论",
        "",
        f"1. 单次分页可拉全 {len(parsed)} 只 ETF，耗时约 {time.time()-started:.1f} 秒，可作为每日全量刷新。",
        f"2. 成交额、价格、涨跌幅覆盖 {'高' if coverage('amount')[1] >= 0.95 else '中'}，可直接用于 ACL 硬门槛。",
        f"3. 规模字段覆盖率 {pct(coverage('mktcap')[1])}，可用于二级门槛；需注意与报告期净资产口径不同。",
        "4. 折溢价只能日频估算（价格/净值），不能宣称实时 IOPV。",
        "5. 费率、成分权重未在本次探测中取得，首期标记「未覆盖」。",
        "",
        f"原始数据：`docs/etf_probe_raw.json`（{len(parsed)} 条）",
    ]
    (ROOT / "docs" / "ETF_DATA_PROBE.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：docs/ETF_DATA_PROBE.md（{len(parsed)} 只，{time.time()-started:.1f}s）")


if __name__ == "__main__":
    main()
