"""JSON 清洗：把 NaN / Infinity 转为 None，避免前端与 JSON 序列化报错。"""
from __future__ import annotations

import math
from typing import Any


def clean_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return value
