"""交易日志归因。

目标只有一个：用真实成交回答「这套信号到底有没有用」。

三条原则：
1. **样本不足不给结论**：少于 20 笔只输出原始数字并标注"样本不足"，
   不写"策略有效/无效"——3 笔盈利说明不了任何事。
2. **区分执行与策略**：按 decision 分组对比「照做的」和「没做的」，
   否则永远分不清是信号差还是自己没执行。
3. **不做年化**：持有期长短不一时，年化会放大短期运气，只给区间收益。
"""
from __future__ import annotations

from statistics import mean
from typing import Iterable, Optional

MIN_RELIABLE_SAMPLE = 20


def compute_pnl(
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_open: float = 0.0,
    fee_close: float = 0.0,
) -> tuple[float, float]:
    """返回 (净盈亏金额, 收益率)。费用从收益中扣除，不单独展示"毛收益"。"""
    if entry_price <= 0 or quantity <= 0:
        return 0.0, 0.0
    gross = (exit_price - entry_price) * quantity
    net = gross - (fee_open or 0.0) - (fee_close or 0.0)
    cost = entry_price * quantity
    return round(net, 2), round(net / cost, 6) if cost else 0.0


def _stats_of(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("pnl") is not None]
    sample = len(closed)
    if sample == 0:
        return {
            "sample": 0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "payoff_ratio": None,
            "expectancy": None,
            "profit_factor": None,
            "total_pnl": 0.0,
            "avg_holding_days": None,
            "reliable": False,
        }

    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] <= 0]
    win_rate = len(wins) / sample
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    holdings = [t["holding_days"] for t in closed if t.get("holding_days") is not None]

    return {
        "sample": sample,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(payoff, 3) if payoff is not None else None,
        "expectancy": round(expectancy, 2),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "total_pnl": round(sum(t["pnl"] for t in closed), 2),
        "avg_holding_days": round(mean(holdings), 1) if holdings else None,
        "reliable": sample >= MIN_RELIABLE_SAMPLE,
    }


def summarize(trades: Iterable[dict]) -> dict:
    """总体归因 + 分组对比。"""
    rows = list(trades)
    overall = _stats_of(rows)

    by_decision: dict[str, dict] = {}
    for key in ("followed", "skipped", "manual"):
        subset = [t for t in rows if t.get("decision") == key]
        if subset:
            by_decision[key] = _stats_of(subset)

    by_class: dict[str, dict] = {}
    for row in rows:
        code = row.get("asset_class_code") or "UNKNOWN"
        by_class.setdefault(code, []).append(row)
    by_class_stats = {
        code: _stats_of(subset) for code, subset in sorted(by_class.items())
    }

    warnings: list[str] = []
    if overall["sample"] < MIN_RELIABLE_SAMPLE:
        warnings.append(
            f"已平仓样本 {overall['sample']} 笔，不足 {MIN_RELIABLE_SAMPLE} 笔，"
            "以下数字只作记录，不能据此判断策略有效"
        )
    if by_decision.get("followed", {}).get("sample", 0) == 0:
        warnings.append("没有一笔标记为「照信号执行」，无法判断信号本身的效果")

    return {
        "overall": overall,
        "by_decision": by_decision,
        "by_asset_class": by_class_stats,
        "open_positions": len([t for t in rows if t.get("status") == "open"]),
        "min_reliable_sample": MIN_RELIABLE_SAMPLE,
        "warnings": warnings,
    }


def compare_followed_vs_skipped(summary: dict) -> Optional[str]:
    """一句话结论：照做是否比不做好。样本不足时明确说不知道。"""
    followed = summary["by_decision"].get("followed", {})
    skipped = summary["by_decision"].get("skipped", {})
    if followed.get("sample", 0) < 5 or skipped.get("sample", 0) < 5:
        return None
    f_exp = followed.get("expectancy") or 0.0
    s_exp = skipped.get("expectancy") or 0.0
    diff = f_exp - s_exp
    if abs(diff) < 1e-9:
        return "照做与不做的期望收益没有差别（样本内）"
    better = "照做" if diff > 0 else "不照做"
    return f"按期望收益，{better}更好（差 {abs(diff):.2f} 元/笔，样本内，未做显著性检验）"
