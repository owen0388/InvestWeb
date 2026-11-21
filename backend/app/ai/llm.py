"""统一模型适配层（OpenAI 兼容协议）。

任何供应商只需改 LLM_BASE_URL / LLM_MODEL_* 即可切换；业务代码不绑定具体模型。
未配置密钥时 `available()` 返回 False，上层显示“未配置”，不使用伪造结果。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings


@dataclass
class LLMResult:
    ok: bool
    content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)


def available() -> tuple[bool, str]:
    if not settings.LLM_ENABLED:
        return False, "LLM_ENABLED=0，AI 分析未启用"
    if not settings.LLM_API_KEY:
        return False, "未配置 LLM_API_KEY"
    if not settings.LLM_BASE_URL:
        return False, "未配置 LLM_BASE_URL"
    if not settings.LLM_MODEL_CHEAP:
        return False, "未配置 LLM_MODEL_CHEAP"
    return True, "已配置"


def _price(model: str, kind: str) -> float:
    cheap = model == settings.LLM_MODEL_CHEAP
    if kind == "input":
        return settings.LLM_CHEAP_INPUT_PRICE if cheap else settings.LLM_STRONG_INPUT_PRICE
    return settings.LLM_CHEAP_OUTPUT_PRICE if cheap else settings.LLM_STRONG_OUTPUT_PRICE


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        prompt_tokens / 1_000_000 * _price(model, "input")
        + completion_tokens / 1_000_000 * _price(model, "output"),
        6,
    )


async def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format_json: bool = True,
) -> LLMResult:
    ok, reason = available()
    if not ok:
        return LLMResult(ok=False, error=reason)
    model = model or settings.LLM_MODEL_CHEAP
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return LLMResult(
            ok=False,
            error=str(exc)[:300],
            latency_ms=int((time.monotonic() - started) * 1000),
            model=model,
        )
    choice = (data.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "").strip()
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return LLMResult(
        ok=bool(content),
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
        latency_ms=int((time.monotonic() - started) * 1000),
        model=model,
        raw=data,
    )


def parse_json(text: str) -> dict | None:
    """容忍模型输出包裹 ```json 或前后缀说明的情况。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None
