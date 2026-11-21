"""时间工具：全系统统一使用中国标准时间（naive datetime，语义为 Asia/Shanghai）。

行情/新闻的 published_at 保留来源所给时间，fetched_at 为系统采集时间，
两者不混用，避免把“数据所属期间”当成“公布时间”。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def now_cn() -> datetime:
    return datetime.now(CN_TZ).replace(tzinfo=None)


def to_cn(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(CN_TZ).replace(tzinfo=None)


def parse_dt(value, fmts: tuple[str, ...] = ()) -> datetime | None:
    """宽松解析来源时间字符串。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return to_cn(value)
    text = str(value).strip().replace("T", " ").replace("Z", "")
    text = text.split("+")[0].strip()
    formats = fmts or (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt)
        except ValueError:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None
