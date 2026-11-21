"""应用配置：全部通过环境变量覆盖，避免把供应商或密钥写死在业务代码里。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "投资情报与动态资产配置系统"
    APP_ENV: str = "dev"
    DATABASE_URL: str = f"sqlite:///{(DATA_DIR / 'invest.db').as_posix()}"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

    SCHEDULER_ENABLED: int = 1
    TIMEZONE: str = "Asia/Shanghai"

    FRED_API_KEY: str = ""
    TUSHARE_TOKEN: str = ""
    GDELT_ENABLED: int = 0

    HTTP_TIMEOUT: float = 15.0
    HTTP_MAX_RETRIES: int = 2
    NEWS_MAX_ITEMS_PER_RUN: int = 80

    LLM_ENABLED: int = 0
    LLM_PROVIDER: str = "openai-compatible"
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL_CHEAP: str = ""
    LLM_MODEL_STRONG: str = ""
    LLM_TIMEOUT: float = 60.0
    LLM_MAX_INPUT_TOKENS: int = 6000
    LLM_MAX_OUTPUT_TOKENS: int = 1200
    LLM_DAILY_BUDGET_USD: float = 0.5
    LLM_MONTHLY_BUDGET_USD: float = 10.0
    LLM_CHEAP_INPUT_PRICE: float = 0.27
    LLM_CHEAP_OUTPUT_PRICE: float = 1.10
    LLM_STRONG_INPUT_PRICE: float = 0.55
    LLM_STRONG_OUTPUT_PRICE: float = 2.19

    # 公网部署必开：未设置密码时不启用认证（本地开发用），设置了就强制校验
    AUTH_USER: str = "admin"
    AUTH_PASSWORD: str = ""

    WEBHOOK_URL: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TO: str = ""

    @property
    def cors_origins(self) -> List[str]:
        return [item.strip() for item in self.CORS_ORIGINS.split(",") if item.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.LLM_ENABLED and self.LLM_API_KEY and self.LLM_BASE_URL
                    and self.LLM_MODEL_CHEAP)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
