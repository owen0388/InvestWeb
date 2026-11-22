"""机会研究卡片：把「分数」变成「能读的决策依据」。

任务书要求 20 项字段。这里的原则是**只写有数据支撑的内容**：
- 没有关联新闻事件，就写"本卡基于价量，未关联具体事件"，不编造事件。
- 相反证据必须主动找：找不到也要说"未发现明显矛盾"，而不是省略这一项。
- 建议权重区间只在质量分足够时给，且标注"仅参考"。
"""
from __future__ import annotations

from typing import Optional

from app.services import config_service

ACTION_RULES = [
    ("EXIT", "降低风险"),
    ("TRIM", "考虑降低权重"),
    ("WAIT", "观察等待"),
    ("WATCH", "观察等待"),
]


def _theme_label(theme: str) -> str:
    """family:consumer → 消费；非家族主题返回原文。"""
    if theme.startswith("family:"):
        families_cfg = config_service.load_yaml("etf_theme_groups.yaml")
        labels = families_cfg.get("labels", {}) or {}
        key = theme.split(":", 1)[1]
        return labels.get(key, key)
    return theme


def _action(state: str, score: Optional[float], holding: bool = False) -> str:
    """统一的研究优先级标签（P0 过渡期）。

    原来这里按 state + 分数各自给词（积极研究/观察等待/考虑降低权重…），
    与 /opportunities 的动作词重叠但阈值不同，同一标的会出现相反结论。
    现在统一由 opportunity_registry.level_for 输出，`holding` 仅为向后兼容保留。
    """
    from app.services.opportunity_registry import level_for

    return level_for(score, state)


def _drivers(feats: dict, rs_score: Optional[float]) -> list[str]:
    items: list[str] = []
    if feats.get("ma_order") == "bull":
        items.append("均线多头排列（MA20 在 MA60 上方）")
    if (feats.get("dev20") or 0) > 0:
        items.append(f"价格站上 MA20 约 {feats['dev20']:.1%}")
    if (feats.get("ret60") or 0) > 0:
        items.append(f"60 日动量为 +{feats['ret60']:.1%}")
    if rs_score is not None and rs_score >= 70:
        items.append(f"同类相对强弱位于前 {100 - rs_score:.0f}%")
    return items or ["暂无可量化的上行驱动"]


def _risks(feats: dict, quality_confidence: Optional[float]) -> list[str]:
    items: list[str] = []
    vol = feats.get("vol20")
    if vol is not None and vol > 0.35:
        items.append(f"年化波动 {vol:.0%}，回撤可能明显")
    dd = feats.get("drawdown_250")
    if dd is not None and dd < -0.10:
        items.append(f"距 250 日高点仍有 {abs(dd):.0%} 未修复")
    if quality_confidence is not None and quality_confidence < 1:
        items.append("产品部分指标缺失，质量判断置信度不足")
    if not items:
        items.append("价量层面未发现突出风险；不代表无风险")
    return items


def _counter_evidence(feats: dict, rs_score: Optional[float]) -> list[str]:
    """主动找矛盾项。找不到也要明确说「未发现」，不能静默省略。"""
    items: list[str] = []
    if rs_score is not None and rs_score < 40:
        items.append(f"同类相对强弱仅 {rs_score:.0f} 分位，弱于多数同业")
    if (feats.get("vol20") or 0) > 0.45:
        items.append("波动率高于常见可承受区间")
    if (feats.get("ret20") or 0) > 0.15:
        items.append("20 日涨幅已偏大，存在追高风险")
    return items or ["已检查价量与相对强弱，未发现明显矛盾证据"]


def _priced_in(feats: dict) -> str:
    ret20 = feats.get("ret20")
    if ret20 is None:
        return "无法判断：缺少 20 日收益数据"
    if ret20 > 0.15:
        return f"可能已部分反应：20 日已上涨 {ret20:.1%}"
    if ret20 > 0.08:
        return f"反应程度中等：20 日上涨 {ret20:.1%}，未见透支"
    return f"未见明显提前反应：20 日变动 {ret20:+.1%}"


def _weight_range(quality: Optional[float], score: Optional[float]) -> dict:
    """权重区间只是量级参考，必须标注不能直接当指令。"""
    if quality is None:
        return {"range": None, "note": "质量分缺失，不给权重建议"}
    if quality >= 70 and (score or 0) >= 65:
        return {"range": "3%–5%", "note": "单一标的上限建议不超过 5%，实际以组合约束为准"}
    if quality >= 50:
        return {"range": "1%–3%", "note": "先小仓试探；产品指标不完整时不宜重仓"}
    return {"range": None, "note": f"质量分 {quality:.0f} 偏低，建议先不建仓"}


def build_card(
    item: dict,
    first_seen: Optional[str] = None,
    prev: Optional[dict] = None,
    updated_at: Optional[str] = None,
) -> dict:
    feats = item.get("features") or {}
    score = item.get("score")
    rs_score = item.get("rs_score")
    quality = item.get("quality_score")
    theme_label = _theme_label(item.get("theme", ""))

    drivers = _drivers(feats, rs_score)
    risks = _risks(feats, item.get("quality_confidence"))
    counters = _counter_evidence(feats, rs_score)

    if prev:
        delta = {
            "prev_score": prev.get("score"),
            "score_change": round((score or 0) - (prev.get("score") or 0), 2)
            if prev.get("score") is not None and score is not None
            else None,
            "prev_state": prev.get("state"),
            "state_changed": prev.get("state") != item.get("state"),
            "prev_as_of": prev.get("as_of"),
        }
    else:
        delta = {
            "prev_score": None,
            "score_change": None,
            "prev_state": None,
            "state_changed": False,
            "prev_as_of": None,
            "note": "首次进入雷达，无上期可比",
        }

    return {
        # 扁平字段：列表展示与筛选需要，避免前端每次从 assets 里取
        "code": item["code"],
        "symbol": item["symbol"],
        "name": item["name"],
        "category": item.get("category"),
        "theme": item.get("theme"),
        "state": item.get("state"),
        # 1 机会标题
        "title": f"{theme_label} 趋势转强 · {item['name']}",
        # 2 涉及资产与产品
        "assets": {
            "code": item["code"],
            "symbol": item["symbol"],
            "name": item["name"],
            "category": item.get("category"),
            "theme": item.get("theme"),
            "theme_label": theme_label,
        },
        # 3 事件或指标变化
        "change": {
            "type": "price_trend",
            "dev20": feats.get("dev20"),
            "dev60": feats.get("dev60"),
            "ret20": feats.get("ret20"),
            "ret60": feats.get("ret60"),
            "vol20": feats.get("vol20"),
            "drawdown_250": feats.get("drawdown_250"),
            "note": "触发条件为趋势状态进入 CANDIDATE，非事件驱动",
        },
        # 4 发现时间
        "first_seen": first_seen,
        # 5 原始证据
        "evidence": {
            "kind": "price_series",
            "bars": feats.get("bars"),
            "source": "eastmoney_kline（含腾讯/新浪回退）",
            "note": "本卡基于价量与相对强弱，未关联具体新闻事件；事件驱动机会另见事件模块",
        },
        # 6 主要逻辑
        "logic": "；".join(item.get("reasons") or []),
        # 7 潜在上涨驱动
        "drivers": drivers,
        # 8 潜在下跌风险
        "risks": risks,
        # 9 影响传导机制
        "transmission": f"主题（{theme_label}）资金流入 → 跟踪指数上行 → {item['name']} 跟随；无杠杆与衍生结构",
        # 10 预计影响期限
        "horizon": "中期（约 20–60 个交易日），趋势类信号不适用于日内",
        # 11 信号强度（机会分）与产品质量分必须分开呈现
        "score": score,
        "quality_score": quality,
        # 同类相对强弱分位：回答「为什么是它，而不是同类的另一只」
        "rs_score": rs_score,
        # 12 证据置信度
        "confidence": {
            "quality_confidence": item.get("quality_confidence"),
            "rs_available": rs_score is not None,
            "note": "置信度反映数据完整度，不代表胜率",
        },
        # 13 是否已提前反应
        "priced_in": _priced_in(feats),
        # 14 其他相反证据
        "counter_evidence": counters,
        # 15 失效条件
        "invalidation": item.get("invalidation"),
        # 16 关注指标和触发条件
        "watch_triggers": [
            f"收盘价跌破 MA20（约 {feats.get('ma20')}）",
            "同类相对强弱跌破 50 分位",
            "单日跌幅超过 3% 且放量",
        ],
        # 17 建议动作
        "action": _action(item.get("state", ""), score),
        # 18 建议权重区间
        "weight_range": _weight_range(quality, score),
        # 19 相比上期变化
        "delta": delta,
        # 20 最后更新时间
        "updated_at": updated_at,
    }
